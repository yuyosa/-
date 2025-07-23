from fastapi import FastAPI, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from datetime import datetime
from db import SessionLocal, init_db, User, Plot, Inventory

app = FastAPI()
init_db()  # 初始化数据库（首次运行会建表）

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

CROP_GROW_TIME = 60  # 秒

# === XP / 等级设定 ===
XP_PER_LEVEL = 100  # 每级所需经验
CROP_XP = {
    "carrot": 10,   # 每次收获胡萝卜奖励 10 XP
    # 未来： "tomato": 15, "potato": 20, ...
}

def calc_level(xp_total: int) -> int:
    """根据累计经验计算等级。每100XP升一级。0-99=1级，100-199=2级..."""
    return xp_total // XP_PER_LEVEL + 1

def xp_progress(xp_total: int):
    """返回当前等级、等级内经验、升下一级还差多少。"""
    lvl = calc_level(xp_total)
    xp_into_level = xp_total % XP_PER_LEVEL
    xp_to_next = XP_PER_LEVEL - xp_into_level
    return lvl, xp_into_level, xp_to_next

def grant_xp(user: User, amount: int) -> tuple[int, bool]:
    """
    给玩家加经验。
    返回 (获得经验值, 是否升级)
    """
    before_level = calc_level(user.xp)
    user.xp += amount
    after_level = calc_level(user.xp)
    leveled_up = after_level > before_level
    return amount, leveled_up


# 数据库会话依赖
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------- Auth ----------------
@app.post("/register")
def register(username: str, password: str, db: Session = Depends(get_db)):
    if db.query(User).filter(User.username == username).first():
        return {"error": "Username already exists"}
    user = User(username=username, password=password)
    db.add(user)
    db.commit()
    # 创建4块地
    for _ in range(4):
        db.add(Plot(owner=user))
    db.commit()
    return {"message": "Registered successfully"}


@app.post("/login")
def login(username: str, password: str, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == username, User.password == password).first()
    if not user:
        return {"error": "Invalid credentials"}
    return {"message": "Login successful", "user_id": user.id}


# ---------------- Game State ----------------
@app.get("/state/{user_id}")
def get_state(user_id: int, db: Session = Depends(get_db)):
    user = db.query(User).get(user_id)
    if not user:
        return {"error": "User not found"}

    lvl, xp_into_level, xp_to_next = xp_progress(user.xp)

    plots = []
    for plot in user.plots:
        plots.append({
            "id": plot.id,
            "crop": plot.crop,
            "planted_at": plot.planted_at.isoformat() + "Z" if plot.planted_at else None
        })

    return {
        "username": user.username,
        "gold": user.gold,
        "plots": plots,
        "level": lvl,
        "xp_total": user.xp,
        "xp_into_level": xp_into_level,
        "xp_to_next": xp_to_next,
        "xp_per_level": XP_PER_LEVEL,
    }


# ---------------- Plant ----------------
@app.post("/plant/{plot_id}")
def plant(plot_id: int, user_id: int = Query(...), seed_name: str = Query(...), db: Session = Depends(get_db)):
    user = db.query(User).get(user_id)
    plot = db.query(Plot).get(plot_id)

    if not user or not plot or plot.user_id != user.id:
        return {"error": "Invalid plot or user"}

    if plot.crop is not None:
        return {"error": "Plot already planted"}

    # 检查库存
    inventory = db.query(Inventory).filter_by(user_id=user.id, item_name=seed_name).first()
    if not inventory or inventory.quantity <= 0:
        return {"error": "You don't have this seed"}

    # 使用种子
    inventory.quantity -= 1

    # 种植：去掉 _seed
    plot.crop = seed_name.replace("_seed", "")
    plot.planted_at = datetime.utcnow()

    db.commit()
    return {"message": "Planted successfully", "gold": user.gold}


# ---------------- Harvest ----------------
@app.post("/harvest/{plot_id}")
def harvest(plot_id: int, db: Session = Depends(get_db)):
    plot = db.query(Plot).get(plot_id)
    if not plot or not plot.crop:
        return {"error": "Nothing to harvest"}

    elapsed = (datetime.utcnow() - plot.planted_at).total_seconds()
    if elapsed < CROP_GROW_TIME:
        return {"error": f"Crop not ready, wait {int(CROP_GROW_TIME - elapsed)}s"}

    user = plot.owner
    crop_name = plot.crop

    # 收获收益（金币）
    user.gold += 20

    # 收获经验
    xp_gain = CROP_XP.get(crop_name, 0)
    gained, leveled = grant_xp(user, xp_gain)

    # 清理地块
    plot.crop = None
    plot.planted_at = None

    db.commit()

    # 返回新的等级信息
    lvl, xp_into_level, xp_to_next = xp_progress(user.xp)
    msg = f"Harvest successful! +{gained} XP."
    if leveled:
        msg += f" Level up! You are now Level {lvl}."

    return {
        "message": msg,
        "gold": user.gold,
        "level": lvl,
        "xp_total": user.xp,
        "xp_into_level": xp_into_level,
        "xp_to_next": xp_to_next,
    }


# ---------------- Admin ----------------
@app.get("/admin/users")
def get_all_users(db: Session = Depends(get_db)):
    users = db.query(User).all()
    result = []
    for user in users:
        lvl, xp_into_level, xp_to_next = xp_progress(user.xp)
        plots = [{
            "id": p.id,
            "crop": p.crop,
            "planted_at": p.planted_at.isoformat() + "Z" if p.planted_at else None
        } for p in user.plots]
        result.append({
            "id": user.id,
            "username": user.username,
            "password": user.password,
            "gold": user.gold,
            "plots": plots,
            "level": lvl,
            "xp_total": user.xp,
            "xp_into_level": xp_into_level,
            "xp_to_next": xp_to_next,
            "xp_per_level": XP_PER_LEVEL,
        })
    return result


@app.post("/admin/update_gold")
def update_gold(user_id: int = Query(...), gold: int = Query(...), db: Session = Depends(get_db)):
    user = db.query(User).get(user_id)
    if not user:
        return {"error": "User not found"}
    user.gold = gold
    db.commit()
    return {"message": f"Updated gold for user {user.username} to {gold}"}


@app.delete("/admin/delete_user")
def delete_user(user_id: int = Query(...), db: Session = Depends(get_db)):
    user = db.query(User).get(user_id)
    if not user:
        return {"error": "User not found"}
    for plot in user.plots:
        db.delete(plot)
    db.delete(user)
    db.commit()
    return {"message": f"Deleted user {user.username}"}


# ---------------- Shop ----------------
@app.post("/buy_seed")
def buy_seed(
    user_id: int = Query(...),
    seed_name: str = Query(...),
    quantity: int = Query(1),
    db: Session = Depends(get_db)
):
    # 服务器端再次防御
    if quantity < 1:
        quantity = 1
    if quantity > 99:
        quantity = 99

    user = db.query(User).get(user_id)
    if not user:
        return {"error": "User not found"}

    price_per_seed = 10  # TODO: 根据不同种子定价
    total_price = price_per_seed * quantity
    if user.gold < total_price:
        return {"error": f"Not enough gold to buy {quantity} seeds"}

    user.gold -= total_price

    inventory = db.query(Inventory).filter_by(user_id=user.id, item_name=seed_name).first()
    if inventory:
        inventory.quantity += quantity
    else:
        inventory = Inventory(user_id=user.id, item_name=seed_name, quantity=quantity)
        db.add(inventory)

    db.commit()
    return {"message": f"Bought {quantity} x {seed_name}", "gold": user.gold}


# ---------------- Inventory ----------------
@app.get("/inventory/{user_id}")
def get_inventory(user_id: int, db: Session = Depends(get_db)):
    inventory = db.query(Inventory).filter_by(user_id=user_id).all()
    return [{"item_name": i.item_name, "quantity": i.quantity} for i in inventory]

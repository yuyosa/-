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

# 统一管理所有物品的配置：买价，卖价，收获经验
# key为库存中item_name，例如"carrot_seed", "carrot"
ITEM_CONFIG = {
    "carrot_seed": {"buy_price": 10, "sell_price": 5, "xp": 0},
    "carrot": {"buy_price": 20, "sell_price": 10, "xp": 10},
    "potato_seed": {"buy_price": 20, "sell_price": 10, "xp": 0},
    "potato": {"buy_price": 40, "sell_price": 35, "xp": 20},
    # 可以继续添加更多种子和果实
}

# === XP / 等级设定 ===
XP_PER_LEVEL = 100  # 每级所需经验

def calc_level(xp_total: int) -> int:
    return xp_total // XP_PER_LEVEL + 1

def xp_progress(xp_total: int):
    lvl = calc_level(xp_total)
    xp_into_level = xp_total % XP_PER_LEVEL
    xp_to_next = XP_PER_LEVEL - xp_into_level
    return lvl, xp_into_level, xp_to_next

def grant_xp(user: User, amount: int) -> tuple[int, bool]:
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
    plots = [{
        "id": plot.id,
        "crop": plot.crop,
        "planted_at": plot.planted_at.isoformat() + "Z" if plot.planted_at else None
    } for plot in user.plots]

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

    inventory = db.query(Inventory).filter_by(user_id=user.id, item_name=seed_name).first()
    if not inventory or inventory.quantity <= 0:
        return {"error": "You don't have this seed"}

    inventory.quantity -= 1
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

    # 收获后放入库存，而不是直接变成金币
    inventory = db.query(Inventory).filter_by(user_id=user.id, item_name=crop_name).first()
    if inventory:
        inventory.quantity += 1
    else:
        inventory = Inventory(user_id=user.id, item_name=crop_name, quantity=1)
        db.add(inventory)

    # 获取对应的经验值，默认0
    xp_gain = ITEM_CONFIG.get(crop_name, {}).get("xp", 0)
    gained, leveled = grant_xp(user, xp_gain)

    plot.crop = None
    plot.planted_at = None
    db.commit()

    lvl, xp_into_level, xp_to_next = xp_progress(user.xp)
    msg = f"Harvest successful! +{gained} XP, +1 {crop_name}."
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

# ---------------- Market ----------------
@app.post("/market/sell")
def sell_item(user_id: int = Query(...), item_name: str = Query(...), quantity: int = Query(...), db: Session = Depends(get_db)):
    user = db.query(User).get(user_id)
    if not user:
        return {"error": "User not found"}
    inv = db.query(Inventory).filter_by(user_id=user.id, item_name=item_name).first()
    if not inv or inv.quantity < quantity:
        return {"error": "Not enough items to sell"}
    inv.quantity -= quantity

    # 读取卖价，默认20金币
    sell_price = ITEM_CONFIG.get(item_name, {}).get("sell_price", 20)
    total_price = sell_price * quantity

    user.gold += total_price
    if inv.quantity <= 0:
        db.delete(inv)
    db.commit()
    return {"message": f"Sold {quantity} x {item_name} for {total_price} gold", "gold": user.gold}

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
def buy_seed(user_id: int = Query(...), seed_name: str = Query(...), quantity: int = Query(1), db: Session = Depends(get_db)):
    if quantity < 1:
        quantity = 1
    if quantity > 99:
        quantity = 99
    user = db.query(User).get(user_id)
    if not user:
        return {"error": "User not found"}

    # 读取买价，默认10金币
    buy_price = ITEM_CONFIG.get(seed_name, {}).get("buy_price", 10)
    total_price = buy_price * quantity

    if user.gold < total_price:
        return {"error": f"Not enough gold to buy {quantity} {seed_name}"}

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

from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from datetime import datetime
from db import SessionLocal, init_db, User, Plot, Inventory
from fastapi import Query


app = FastAPI()
init_db()  # 初始化数据库

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

CROP_GROW_TIME = 60  # 秒

# 数据库会话依赖
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

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

@app.get("/state/{user_id}")
def get_state(user_id: int, db: Session = Depends(get_db)):
    user = db.query(User).get(user_id)
    if not user:
        return {"error": "User not found"}
    plots = []
    for plot in user.plots:
        plots.append({
            "id": plot.id,
            "crop": plot.crop,
            "planted_at": plot.planted_at.isoformat() + "Z" if plot.planted_at else None
        })
    return {
        "username": user.username,   # 新增用户名字段
        "gold": user.gold,
        "plots": plots
    }

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

    # 种植
    plot.crop = seed_name.replace("_seed", "")  # "carrot"
    plot.planted_at = datetime.utcnow()

    db.commit()
    return {"message": "Planted successfully", "gold": user.gold}

@app.post("/harvest/{plot_id}")
def harvest(plot_id: int, db: Session = Depends(get_db)):
    plot = db.query(Plot).get(plot_id)
    if not plot or not plot.crop:
        return {"error": "Nothing to harvest"}
    elapsed = (datetime.utcnow() - plot.planted_at).total_seconds()
    if elapsed < CROP_GROW_TIME:
        return {"error": f"Crop not ready, wait {int(CROP_GROW_TIME - elapsed)}s"}
    plot.crop = None
    plot.planted_at = None
    plot.owner.gold += 20
    db.commit()
    return {"message": "Harvest successful", "gold": plot.owner.gold}

@app.get("/admin/users")
def get_all_users(db: Session = Depends(get_db)):
    users = db.query(User).all()
    result = []
    for user in users:
        plots = [{"id": p.id, "crop": p.crop, "planted_at": p.planted_at.isoformat() + "Z" if p.planted_at else None} for p in user.plots]
        result.append({
            "id": user.id,
            "username": user.username,
            "password": user.password,   # 加上密码字段
            "gold": user.gold,
            "plots": plots
        })
    return result


# 修改用户金币
@app.post("/admin/update_gold")
def update_gold(user_id: int = Query(...), gold: int = Query(...), db: Session = Depends(get_db)):
    user = db.query(User).get(user_id)
    if not user:
        return {"error": "User not found"}
    user.gold = gold
    db.commit()
    return {"message": f"Updated gold for user {user.username} to {gold}"}

# 删除用户
@app.delete("/admin/delete_user")
def delete_user(user_id: int = Query(...), db: Session = Depends(get_db)):
    user = db.query(User).get(user_id)
    if not user:
        return {"error": "User not found"}
    # 删除用户的地块
    for plot in user.plots:
        db.delete(plot)
    db.delete(user)
    db.commit()
    return {"message": f"Deleted user {user.username}"}


# 增加商店
@app.post("/buy_seed")
def buy_seed(user_id: int = Query(...), seed_name: str = Query(...), db: Session = Depends(get_db)):
    user = db.query(User).get(user_id)
    if not user:
        return {"error": "User not found"}

    price = 10  # 胡萝卜种子固定10金币
    if user.gold < price:
        return {"error": "Not enough gold"}

    # 扣金币
    user.gold -= price

    # 更新库存
    inventory = db.query(Inventory).filter_by(user_id=user.id, item_name=seed_name).first()
    if inventory:
        inventory.quantity += 1
    else:
        inventory = Inventory(user_id=user.id, item_name=seed_name, quantity=1)
        db.add(inventory)

    db.commit()
    return {"message": f"Bought {seed_name}", "gold": user.gold}

# 查看库存
@app.get("/inventory/{user_id}")
def get_inventory(user_id: int, db: Session = Depends(get_db)):
    inventory = db.query(Inventory).filter_by(user_id=user_id).all()
    return [{"item_name": i.item_name, "quantity": i.quantity} for i in inventory]
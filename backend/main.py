from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from pydantic import BaseModel
from db import SessionLocal, init_db, User, Plot, Inventory

app = FastAPI()
init_db()

app.mount("/static", StaticFiles(directory="static"), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ------------------ ITEM CONFIG ------------------
ITEM_CONFIG = {
    "carrot_seed": {"buy_price": 10, "sell_price": 5, "xp": 0},
    "carrot": {"buy_price": 20, "sell_price": 2000, "xp": 10, "grow_time": 60, "stages": 3},
    "potato_seed": {"buy_price": 20, "sell_price": 15, "xp": 0},
    "potato": {"buy_price": 40, "sell_price": 35, "xp": 20, "grow_time": 90, "stages": 3},
    "wheat_seed": {"buy_price": 30, "sell_price": 25, "xp": 0},
    "wheat": {"buy_price": 60, "sell_price": 500, "xp": 300, "grow_time": 120, "stages": 4}
}

def exp_to_next_level(level: int) -> int:
    base = 200
    factor = 1.15   # 每级递增 15%
    return int(base * (factor ** (level - 1)))

def calculate_level(xp: int) -> int:
    level = 1
    while xp >= exp_to_next_level(level):
        xp -= exp_to_next_level(level)
        level += 1
    return level

# ------------------ REGISTER / LOGIN ------------------
class RegisterRequest(BaseModel):
    username: str
    password: str

@app.post("/register")
def register(req: RegisterRequest, db: Session = Depends(get_db)):
    if db.query(User).filter_by(username=req.username).first():
        raise HTTPException(status_code=400, detail="Username already registered")
    user = User(username=req.username, password=req.password)
    db.add(user)
    db.commit()
    db.refresh(user)
    for _ in range(user.unlocked_plots):
        plot = Plot(user_id=user.id)
        db.add(plot)
    db.commit()
    return {"message": "Registered successfully"}

class LoginRequest(BaseModel):
    username: str
    password: str

@app.post("/login")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter_by(username=req.username, password=req.password).first()
    if not user:
        raise HTTPException(status_code=400, detail="Invalid credentials")
    return {"user_id": user.id, "username": user.username}

# ------------------ GET STATE ------------------
@app.get("/state/{user_id}")
def get_state(user_id: int, db: Session = Depends(get_db)):
    user = db.query(User).get(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # 保证 plots 数量 >= unlocked_plots
    while len(user.plots) < user.unlocked_plots:
        new_plot = Plot(user_id=user.id)
        db.add(new_plot)
        db.commit()
        db.refresh(new_plot)
        user.plots.append(new_plot)

    plots = []
    now = datetime.utcnow()
    for plot in user.plots:
        crop_name = plot.crop
        planted_time = plot.planted_at
        image_url = "/static/images/crops/empty.png"

        if crop_name and planted_time:
            item_cfg = ITEM_CONFIG.get(crop_name)
            if item_cfg:
                grow_time = item_cfg.get("grow_time", 30)
                stages = item_cfg.get("stages", 1)
                elapsed = (now - planted_time).total_seconds()
                remain = max(0, grow_time - elapsed)
                if stages > 1:
                    stage_length = grow_time / stages
                    current_stage = stages - int(remain // stage_length)
                    current_stage = min(max(current_stage, 1), stages)
                    image_url = f"/static/images/crops/{crop_name}_stage{current_stage}.png"
                else:
                    image_url = f"/static/images/crops/{crop_name}.png"
        elif crop_name:
            image_url = f"/static/images/crops/{crop_name}.png"

        plots.append({
            "id": plot.id,
            "crop": crop_name,
            "planted_at": planted_time.isoformat() + "Z" if planted_time else None,
            "image_url": image_url
        })

    inventory = [{"item_name": item.item_name, "quantity": item.quantity} for item in user.inventory]
    level = (user.xp // 100) + 1

    return {
        "username": user.username,
        "gold": user.gold,
        "xp": user.xp,
        "level": user.level,
        "unlocked_plots": user.unlocked_plots,
        "plots": plots,
        "inventory": inventory
    }


# ------------------ PLANT ------------------
class PlantRequest(BaseModel):
    user_id: int
    plot_id: int
    crop: str

@app.post("/plant")
def plant(req: PlantRequest, db: Session = Depends(get_db)):
    user = db.query(User).get(req.user_id)
    plot = db.query(Plot).get(req.plot_id)
    if not user or not plot or plot.owner != user:
        raise HTTPException(status_code=400, detail="Invalid user or plot")

    seed_name = f"{req.crop}_seed"
    inventory_item = db.query(Inventory).filter_by(user_id=req.user_id, item_name=seed_name).first()
    if not inventory_item or inventory_item.quantity < 1:
        raise HTTPException(status_code=400, detail="Not enough seeds")

    inventory_item.quantity -= 1
    plot.crop = req.crop
    plot.planted_at = datetime.utcnow()
    db.commit()
    return {"message": "Crop planted"}


def add_experience(user: User, amount: int, db: Session):
    user.xp += amount
    # 检查升级
    while user.xp >= exp_to_next_level(user.level):
        user.xp -= exp_to_next_level(user.level)
        user.level += 1
    db.commit()

# ------------------ HARVEST ------------------
class HarvestRequest(BaseModel):
    user_id: int
    plot_id: int



@app.post("/harvest")
def harvest(req: HarvestRequest, db: Session = Depends(get_db)):
    user = db.query(User).get(req.user_id)
    plot = db.query(Plot).get(req.plot_id)
    if not user or not plot or plot.owner != user or not plot.crop:
        raise HTTPException(status_code=400, detail="Nothing to harvest")

    crop_name = plot.crop
    item_info = ITEM_CONFIG.get(crop_name)
    if not item_info:
        raise HTTPException(status_code=400, detail="Invalid crop")

    grow_time = item_info.get("grow_time", 30)
    if datetime.utcnow() < (plot.planted_at + timedelta(seconds=grow_time)):
        raise HTTPException(status_code=400, detail="Crop not yet ready to harvest")

    add_experience(user, item_info["xp"], db)
    crop_item = db.query(Inventory).filter_by(user_id=req.user_id, item_name=crop_name).first()
    if not crop_item:
        crop_item = Inventory(user_id=req.user_id, item_name=crop_name, quantity=0)
        db.add(crop_item)
    crop_item.quantity += 1

    plot.crop = None
    plot.planted_at = None
    db.commit()
    return {"message": "Crop harvested"}

# ------------------ BUY / SELL ------------------
class BuyRequest(BaseModel):
    user_id: int
    item_name: str
    quantity: int

@app.post("/buy")
def buy_item(req: BuyRequest, db: Session = Depends(get_db)):
    user = db.query(User).get(req.user_id)
    item_info = ITEM_CONFIG.get(req.item_name)
    if not user or not item_info:
        raise HTTPException(status_code=400, detail="Invalid user or item")
    total_cost = item_info["buy_price"] * req.quantity
    if user.gold < total_cost:
        raise HTTPException(status_code=400, detail="Not enough gold")

    user.gold -= total_cost
    inventory_item = db.query(Inventory).filter_by(user_id=req.user_id, item_name=req.item_name).first()
    if not inventory_item:
        inventory_item = Inventory(user_id=req.user_id, item_name=req.item_name, quantity=0)
        db.add(inventory_item)
    inventory_item.quantity += req.quantity
    db.commit()
    return {"message": "Item purchased"}

class SellRequest(BaseModel):
    user_id: int
    item_name: str
    quantity: int

@app.post("/sell")
def sell_item(req: SellRequest, db: Session = Depends(get_db)):
    user = db.query(User).get(req.user_id)
    item_info = ITEM_CONFIG.get(req.item_name)
    inventory_item = db.query(Inventory).filter_by(user_id=req.user_id, item_name=req.item_name).first()
    if not user or not item_info or not inventory_item or inventory_item.quantity < req.quantity:
        raise HTTPException(status_code=400, detail="Invalid sell request")

    total_earnings = item_info["sell_price"] * req.quantity
    inventory_item.quantity -= req.quantity
    user.gold += total_earnings
    db.commit()
    return {"message": "Item sold"}

@app.get("/inventory/{user_id}")
def get_inventory(user_id: int, db: Session = Depends(get_db)):
    user = db.query(User).get(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return [{"item_name": item.item_name, "quantity": item.quantity} for item in user.inventory]

# ------------------ UPGRADE LAND ------------------
def calc_upgrade_cost(current_plots: int) -> int:
    return 200 * (current_plots - 3) ** 2

def get_max_plots_by_level(level: int) -> int:
    if level <= 2: return 4
    elif level <= 4: return 5
    elif level <= 6: return 6
    elif level <= 8: return 7
    elif level <= 10: return 8
    elif level <= 12: return 9
    elif level <= 14: return 10
    elif level <= 16: return 11
    elif level <= 18: return 12
    elif level <= 20: return 13
    elif level <= 22: return 14
    elif level <= 25: return 15
    elif level <= 28: return 16
    elif level <= 31: return 17
    elif level <= 34: return 18
    elif level <= 36: return 19
    elif level == 37: return 20
    elif level == 40: return 21
    elif level == 43: return 22
    elif level == 47: return 23
    elif level >= 50: return 24
    else: return 19

@app.post("/upgrade_land/{user_id}")
def upgrade_land(user_id: int, db: Session = Depends(get_db)):
    user = db.query(User).get(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    level = user.level
    max_allowed = get_max_plots_by_level(level)
    if user.unlocked_plots >= max_allowed:
        raise HTTPException(status_code=400, detail="等级未达或土地已满")
    
    cost = calc_upgrade_cost(user.unlocked_plots + 1)
    if user.gold < cost:
        raise HTTPException(status_code=400, detail=f"金币不足，需要 {cost} 金币")
    
    user.gold -= cost
    user.unlocked_plots += 1

    # 创建新地块
    new_plot = Plot(user_id=user.id)
    db.add(new_plot)
    
    db.commit()
    
    return {
        "unlocked_plots": user.unlocked_plots,
        "gold_left": user.gold,
        "next_cost": calc_upgrade_cost(user.unlocked_plots + 1)
    }

# 获取所有用户
@app.get("/admin/users")
def get_all_users(db: Session = Depends(get_db)):
    users = db.query(User).all()
    return [
        {
            "id": u.id,
            "username": u.username,
            "password": u.password,  # ⚠ 如果是 hash 就显示 hash
            "gold": u.gold,
            "level": u.level,
            "xp": u.xp
        }
        for u in users
    ]

# 修改金币
@app.post("/admin/update_gold/{user_id}")
def update_gold(user_id: int, payload: dict, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return {"error": "用户不存在"}
    user.gold = payload["gold"]
    db.commit()
    return {"message": "金币已更新", "gold": user.gold}

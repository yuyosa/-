from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from datetime import datetime
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

ITEM_CONFIG = {
    "carrot_seed": {"buy_price": 10, "sell_price": 5, "xp": 0},
    "carrot": {"buy_price": 20, "sell_price": 10, "xp": 10},
    "potato_seed": {"buy_price": 20, "sell_price": 15, "xp": 0},
    "potato": {"buy_price": 40, "sell_price": 33, "xp": 20},
}

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
    for _ in range(6):
        plot = Plot(owner=user)
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

@app.get("/state/{user_id}")
def get_state(user_id: int, db: Session = Depends(get_db)):
    user = db.query(User).get(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    plots = []
    for plot in user.plots:
        crop_name = plot.crop
        if crop_name:
            image_url = f"/static/images/crops/{crop_name}.png"
        else:
            image_url = "/static/images/crops/empty.png"
        plots.append({
            "id": plot.id,
            "crop": crop_name,
            "planted_at": plot.planted_at.isoformat() + "Z" if plot.planted_at else None,
            "image_url": image_url
        })

    inventory = [{"item_name": item.item_name, "quantity": item.quantity} for item in user.inventory]
    # 计算等级，假设每100 XP升级一次
    level = (user.xp // 100) + 1

    return {
        "username": user.username,
        "gold": user.gold,
        "xp": user.xp,
        "level": level,
        "plots": plots,
        "inventory": inventory
    }

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

    user.xp += item_info["xp"]
    crop_item = db.query(Inventory).filter_by(user_id=req.user_id, item_name=crop_name).first()
    if not crop_item:
        crop_item = Inventory(user_id=req.user_id, item_name=crop_name, quantity=0)
        db.add(crop_item)
    crop_item.quantity += 1

    plot.crop = None
    plot.planted_at = None
    db.commit()
    return {"message": "Crop harvested"}

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

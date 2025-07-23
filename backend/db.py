from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.orm import sessionmaker, declarative_base, relationship

# 数据库连接
DATABASE_URL = "sqlite:///./farm.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# 用户表
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True)
    password = Column(String)
    gold = Column(Integer, default=1000)
    xp = Column(Integer, default=0)  # 新增：累计经验
    plots = relationship("Plot", back_populates="owner")
    inventory = relationship("Inventory", back_populates="user")

# 农田表
class Plot(Base):
    __tablename__ = "plots"
    id = Column(Integer, primary_key=True, index=True)
    crop = Column(String, nullable=True)
    planted_at = Column(DateTime, nullable=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    owner = relationship("User", back_populates="plots")

# 库存表
class Inventory(Base):
    __tablename__ = "inventory"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    item_name = Column(String)  # 种子名字，如 "carrot_seed"
    quantity = Column(Integer, default=0)
    user = relationship("User", back_populates="inventory")

# 初始化数据库
def init_db():
    Base.metadata.create_all(bind=engine)

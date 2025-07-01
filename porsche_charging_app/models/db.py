import logging
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, Float, String, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

from ..core.config import settings

logger = logging.getLogger(__name__)

# Create database engine
engine = create_engine(settings.DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Define models
class VehicleStatus(Base):
    """Model for storing vehicle status data"""
    __tablename__ = "vehicle_status"
    
    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=datetime.now)
    vin = Column(String, index=True)
    battery_level = Column(Float)
    battery_charging_state = Column(String)
    estimated_range = Column(Float)
    raw_data = Column(String)  # JSON string of full status data

class Price(Base):
    """Model for storing electricity price data"""
    __tablename__ = "prices"
    
    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=datetime.now, index=True)
    value = Column(Float)

class Configuration(Base):
    """Model for storing configuration settings"""
    __tablename__ = "configuration"
    
    key = Column(String, primary_key=True, index=True)
    value = Column(String)

# Create tables
def init_db():
    """Initialize the database"""
    try:
        Base.metadata.create_all(bind=engine)
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Error initializing database: {e}")

# Get database session
def get_db_session():
    """Get a database session"""
    db = SessionLocal()
    try:
        return db
    except:
        db.close()
        raise

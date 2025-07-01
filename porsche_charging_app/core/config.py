import os
import json
from pathlib import Path
from pydantic import BaseModel
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

CONFIG_FILE = Path("porsche_charging_app/config.json")

class Settings(BaseModel):
    # Application settings
    APP_NAME: str = "Porsche Smart Charging"
    DEBUG: bool = os.getenv("DEBUG", "False").lower() == "true"
    LOG_FILE: str = "porsche_charging.log"

    # Porsche Connect API credentials
    PORSCHE_EMAIL: str = os.getenv("PORSCHE_EMAIL", "")
    PORSCHE_PASSWORD: str = os.getenv("PORSCHE_PASSWORD", "")

    # 2Captcha API key (for solving Porsche login captchas)
    CAPTCHA_API_KEY: str = os.getenv("2CAPTCHA_API_KEY", "")

    # Amber Electric API Key
    AMBER_API_KEY: str = os.getenv("AMBER_API_KEY", "")

    # Optional fallback Electricity price API settings
    PRICE_API_URL: str = os.getenv("PRICE_API_URL", "")
    PRICE_API_KEY: str = os.getenv("PRICE_API_KEY", "")

    # Charging settings - these can be updated at runtime
    PRICE_THRESHOLD: float = 0.15  # Default threshold
    TARGET_SOC: int = 80  # Default target state of charge
    AUTO_MODE_ENABLED: bool = True

    # Price check interval (in minutes)
    PRICE_CHECK_INTERVAL: int = 5
    
    # Vehicle status check interval (in minutes)
    VEHICLE_CHECK_INTERVAL: int = 15

    # Session storage
    SESSION_FILE: Path = Path("porsche_session.json")

    # Database settings
    DATABASE_URL: str = os.getenv("DATABASE_URL", f"sqlite:///./porsche_charging.db")

    def save(self):
        """Saves the mutable settings to the config file."""
        mutable_settings = {
            "PRICE_THRESHOLD": self.PRICE_THRESHOLD,
            "TARGET_SOC": self.TARGET_SOC,
            "AUTO_MODE_ENABLED": self.AUTO_MODE_ENABLED,
            "VEHICLE_CHECK_INTERVAL": self.VEHICLE_CHECK_INTERVAL
        }
        with open(CONFIG_FILE, 'w') as f:
            json.dump(mutable_settings, f, indent=4)

    def load(self):
        """Loads mutable settings from the config file if it exists."""
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE, 'r') as f:
                try:
                    mutable_settings = json.load(f)
                    self.PRICE_THRESHOLD = mutable_settings.get("PRICE_THRESHOLD", self.PRICE_THRESHOLD)
                    self.TARGET_SOC = mutable_settings.get("TARGET_SOC", self.TARGET_SOC)
                    self.AUTO_MODE_ENABLED = mutable_settings.get("AUTO_MODE_ENABLED", self.AUTO_MODE_ENABLED)
                    self.VEHICLE_CHECK_INTERVAL = mutable_settings.get("VEHICLE_CHECK_INTERVAL", self.VEHICLE_CHECK_INTERVAL)
                except json.JSONDecodeError:
                    # If the file is corrupted or empty, just use defaults
                    pass

# Create settings instance
settings = Settings()
# Load any saved settings from config.json
settings.load()

# Ensure required settings are provided from environment variables
def validate_settings():
    missing = []
    if not settings.PORSCHE_EMAIL:
        missing.append("PORSCHE_EMAIL")
    if not settings.PORSCHE_PASSWORD:
        missing.append("PORSCHE_PASSWORD")
    if not settings.AMBER_API_KEY:
        missing.append("AMBER_API_KEY")

    if missing:
        raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

# Validate settings on import
validate_settings()

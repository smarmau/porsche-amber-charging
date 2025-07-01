import logging
import asyncio
from fastapi import FastAPI, Request, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from .core.config import settings
from .api.routes import router as api_router
from .services.porsche_service import get_porsche_service
from .services.price_service import get_price_service, generate_price_chart
from .services.charge_controller import get_charge_controller
from .models.db import init_db

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(settings.LOG_FILE)
    ]
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# Lifespan event handler for startup/shutdown tasks
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initialize services and database
    logger.info("Starting application...")
    
    # Initialize services
    porsche_service = get_porsche_service()
    price_service = get_price_service()
    charge_controller = get_charge_controller()
    
    # Initialize database
    init_db()
    
    # Try to authenticate with Porsche Connect
    try:
        authenticated = await porsche_service.authenticate()
        if authenticated:
            logger.info("Successfully authenticated with Porsche Connect")
        else:
            logger.warning("Failed to authenticate with Porsche Connect")
    except Exception as e:
        logger.error(f"Error during authentication: {e}")
    
    # Start background tasks
    charge_controller.start()
    
    yield
    
    # Shutdown: Cleanup resources
    logger.info("Shutting down application...")
    charge_controller.stop()
    if porsche_service.conn:
        await porsche_service.conn.close()

# Create FastAPI app
app = FastAPI(
    title="Porsche Connect Manager",
    description="Application for managing Porsche vehicle status and charging",
    version="1.0.0",
    lifespan=lifespan
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, replace with specific origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Templates
templates = Jinja2Templates(directory="porsche_charging_app/templates")

# Mount static files
app.mount("/static", StaticFiles(directory="porsche_charging_app/static"), name="static")

# Root route (dashboard)
@app.get("/")
async def root(request: Request):
    """Serve the main dashboard page"""
    try:
        porsche_service = get_porsche_service()
        price_service = get_price_service()

        # Parallel fetch of all data
        results = await asyncio.gather(
            porsche_service.get_vehicle_overview(),
            price_service.get_live_prices(),
            price_service.get_amber_prices(hours=12),
            return_exceptions=True
        )
        vehicle_overview, live_prices, electricity_prices = results

        # Handle potential errors from API calls
        error = None
        if isinstance(vehicle_overview, Exception) or (isinstance(vehicle_overview, dict) and vehicle_overview.get("error")):
            error = f"Could not retrieve vehicle overview: {vehicle_overview}"
            vehicle_overview = {} # Provide empty dict to template
        if isinstance(live_prices, Exception):
            error = f"Could not retrieve live prices: {live_prices}"
            live_prices = {} # Provide empty dict to template

        if isinstance(electricity_prices, Exception):
            error = f"Could not retrieve electricity prices: {electricity_prices}"
            electricity_prices = []

        # logger.info(f"Electricity prices for template: {electricity_prices}")

        context = {
            "request": request,
            "is_authenticated": porsche_service.is_authenticated(),
            "vehicle_overview": vehicle_overview,
            "live_price": live_prices.get("general", 0),
            "feed_in_price": live_prices.get("feed_in", 0),
            "price_threshold": price_service.get_price_threshold(),
            "electricity_prices": electricity_prices,
            "error": error
        }
        
        return templates.TemplateResponse("index.html", context)
    except Exception as e:
        logger.error(f"Fatal error in root route: {e}")
        return templates.TemplateResponse(
            "index.html", {"request": request, "error": "An unexpected error occurred."}
        )

# Include API routes
app.include_router(api_router, prefix="/api")

# Mount static files
app.mount("/static", StaticFiles(directory="porsche_charging_app/static"), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("porsche_charging_app.main:app", host="0.0.0.0", port=8000, reload=True)

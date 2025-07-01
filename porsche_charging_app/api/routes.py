from fastapi import APIRouter, Depends, HTTPException, status
from typing import Dict, Any, List, Optional
from pydantic import BaseModel
from fastapi.responses import JSONResponse

from ..services.porsche_service import get_porsche_service
from ..services.price_service import get_price_service, generate_price_chart
from ..core.config import settings
import asyncio
import logging

logger = logging.getLogger(__name__)

router = APIRouter()

@router.get("/vehicle/status")
async def get_vehicle_status() -> Dict[str, Any]:
    """Get current vehicle status"""
    porsche_service = get_porsche_service()
    
    # Check if authenticated
    if not porsche_service.is_authenticated():
        authenticated = await porsche_service.authenticate()
        if not authenticated:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated with Porsche Connect"
            )
    
    # Get vehicle status
    vehicle_status = await porsche_service.get_vehicle_status()
    
    if "error" in vehicle_status:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=vehicle_status["error"]
        )
    
    return vehicle_status

@router.get("/vehicle/charging")
async def get_charging_status() -> Dict[str, Any]:
    """Get vehicle charging status"""
    porsche_service = get_porsche_service()
    
    # Check if authenticated
    if not porsche_service.is_authenticated():
        authenticated = await porsche_service.authenticate()
        if not authenticated:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated with Porsche Connect"
            )
    
    # Get charging status
    charging_status = await porsche_service.get_charging_status()
    
    if "error" in charging_status:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=charging_status["error"]
        )
    
    return charging_status

@router.post("/vehicle/charging/start")
async def start_charging() -> Dict[str, Any]:
    """Start charging the vehicle"""
    porsche_service = get_porsche_service()
    
    # Check if authenticated
    if not porsche_service.is_authenticated():
        authenticated = await porsche_service.authenticate()
        if not authenticated:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated with Porsche Connect"
            )
    
    # Start charging
    success = await porsche_service.start_charging()
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to start charging"
        )
    
    return {"status": "success", "message": "Charging started"}

@router.post("/vehicle/charging/stop")
async def stop_charging() -> Dict[str, Any]:
    """Stop charging the vehicle"""
    porsche_service = get_porsche_service()
    
    # Check if authenticated
    if not porsche_service.is_authenticated():
        authenticated = await porsche_service.authenticate()
        if not authenticated:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated with Porsche Connect"
            )
    
    # Stop charging
    success = await porsche_service.stop_charging()
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to stop charging"
        )
    
    return {"status": "success", "message": "Charging stopped"}

@router.get("/prices/live")
async def get_live_data():
    """Get live electricity prices and vehicle status"""
    price_service = get_price_service()
    porsche_service = get_porsche_service()

    # Fetch all data in parallel
    # Fetch all data in parallel
    results = await asyncio.gather(
        porsche_service.get_vehicle_overview(),
        price_service.get_live_prices(),
        price_service.get_amber_prices(hours=12),
        return_exceptions=True
    )

    vehicle_overview, live_prices, electricity_prices = results

    # Create a consolidated response
    response_data = {}

    if isinstance(live_prices, Exception):
        logger.error(f"Live prices error in API: {live_prices}")
        response_data.update({"general": None, "feed_in": None})
    elif live_prices:
        response_data.update(live_prices)

    if isinstance(vehicle_overview, Exception) or (isinstance(vehicle_overview, dict) and vehicle_overview.get("error")):
        logger.error(f"Vehicle overview error in API: {vehicle_overview}")
        response_data["vehicle_overview"] = {"error": "Could not retrieve vehicle overview."}
    else:
        # Add binary charging state safely
        battery_charging_state = vehicle_overview.get('BATTERY_CHARGING_STATE')
        state_str = str(battery_charging_state).upper() if battery_charging_state is not None else ""
        vehicle_overview['is_charging'] = state_str in ['CHARGING', 'ON']
        response_data["vehicle_overview"] = vehicle_overview

    if isinstance(electricity_prices, Exception):
        logger.error(f"Electricity prices error in API: {electricity_prices}")
        response_data["electricity_prices"] = []
    else:
        response_data["electricity_prices"] = electricity_prices

    return response_data

@router.get("/prices/current")
async def get_current_prices(hours: int = 12) -> Dict[str, Any]:
    """Get current electricity prices for the specified number of hours"""
    try:
        price_service = get_price_service()
        
        # Get prices from Amber API
        prices = await price_service.get_amber_prices(hours)
        
        # Generate price chart
        chart_image = await generate_price_chart(prices, hours)
        
        return {
            "status": "success",
            "prices": prices,
            "chart": chart_image
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get electricity prices: {str(e)}"
        )

@router.get("/auth/status")
async def auth_status() -> Dict[str, Any]:
    """Check authentication status"""
    porsche_service = get_porsche_service()
    is_authenticated = porsche_service.is_authenticated()
    
    return {
        "authenticated": is_authenticated,
        "email": settings.PORSCHE_EMAIL if is_authenticated else None
    }

# --- Configuration Endpoints ---

class PriceThresholdRequest(BaseModel):
    threshold: float

class MockPriceRequest(BaseModel):
    price: Optional[float] = None

@router.get("/config/price_threshold", response_model=Dict[str, float])
async def get_price_threshold_api():
    price_service = get_price_service()
    threshold = price_service.get_price_threshold()
    return {"price_threshold": threshold}

@router.post("/config/price_threshold")
async def set_price_threshold_api(request: PriceThresholdRequest):
    price_service = get_price_service()
    success = price_service.set_price_threshold(request.threshold)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to set price threshold")
    return {"status": "success", "message": f"Price threshold set to {request.threshold}"}

@router.get("/config/mock_price", response_model=Dict[str, Optional[float]])
async def get_mock_price_api():
    price_service = get_price_service()
    price = price_service.get_mock_price()
    return {"mock_price": price}

@router.post("/config/mock_price")
async def set_mock_price_api(request: MockPriceRequest):
    price_service = get_price_service()
    price_service.set_mock_price(request.price)
    return {"status": "success", "message": f"Mock price set to {request.price}"}

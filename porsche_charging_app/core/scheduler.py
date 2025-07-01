import asyncio
import logging
import threading
import time
import schedule
from datetime import datetime

from ..core.config import settings
from ..services.price_service import get_price_service
from ..services.porsche_service import get_porsche_service
from ..services.decision_service import should_charge_vehicle

logger = logging.getLogger(__name__)

# Global variables
_scheduler_thread = None
_stop_event = threading.Event()

def _run_continuously(interval=1):
    """Continuously run, while executing pending jobs at each elapsed
    time interval.
    @return cease_continuous_run: threading.Event which can be set to
    terminate the continuous run.
    """
    cease_continuous_run = threading.Event()

    class ScheduleThread(threading.Thread):
        @classmethod
        def run(cls):
            while not cease_continuous_run.is_set():
                schedule.run_pending()
                time.sleep(interval)

    continuous_thread = ScheduleThread()
    continuous_thread.daemon = True
    continuous_thread.start()
    return cease_continuous_run

async def _check_price_and_decide():
    """Check current electricity price and decide whether to charge the vehicle"""
    try:
        logger.info("Running scheduled price check and charging decision...")
        
        # Get current price
        price_service = get_price_service()
        current_price = await price_service.get_current_price()
        price_threshold = price_service.get_price_threshold()
        
        logger.info(f"Current price: {current_price}, Threshold: {price_threshold}")
        
        # Skip if auto mode is disabled
        if not settings.AUTO_MODE_ENABLED:
            logger.info("Auto mode is disabled. Skipping charging decision.")
            return
        
        # Get Porsche service
        porsche_service = get_porsche_service()
        
        # Check if we're authenticated with Porsche
        if not porsche_service.is_authenticated():
            logger.warning("Not authenticated with Porsche Connect. Attempting to authenticate...")
            authenticated = await porsche_service.authenticate()
            if not authenticated:
                logger.error("Failed to authenticate with Porsche Connect. Skipping charging decision.")
                return
        
        # Get current vehicle status
        vehicle_status = await porsche_service.get_vehicle_status()
        charging_status = await porsche_service.get_charging_status()
        
        # Make charging decision
        should_charge = should_charge_vehicle(current_price, price_threshold, vehicle_status, charging_status)
        
        # Log decision
        logger.info(f"Charging decision: {should_charge}")
        
        # Execute charging command if needed
        if should_charge:
            if charging_status.get('BATTERY_CHARGING_STATE') != 'CHARGING':
                logger.info("Starting charging...")
                await porsche_service.start_charging()
        else:
            if charging_status.get('BATTERY_CHARGING_STATE') == 'CHARGING':
                logger.info("Stopping charging...")
                await porsche_service.stop_charging()
                
    except Exception as e:
        logger.error(f"Error in scheduled price check: {e}", exc_info=True)

def _run_threaded(job_func):
    """Run the job function in a new thread"""
    job_thread = threading.Thread(target=job_func)
    job_thread.daemon = True
    job_thread.start()

def _run_async_job():
    """Run the async job in the event loop"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_check_price_and_decide())
    finally:
        loop.close()

def start_scheduler():
    """Start the scheduler thread"""
    global _scheduler_thread, _stop_event
    
    if _scheduler_thread is not None:
        logger.warning("Scheduler is already running")
        return
    
    logger.info(f"Starting scheduler with {settings.PRICE_CHECK_INTERVAL} minute interval")
    
    # Schedule the job
    schedule.every(settings.PRICE_CHECK_INTERVAL).minutes.do(_run_threaded, _run_async_job)
    
    # Run once immediately
    _run_threaded(_run_async_job)
    
    # Start the scheduler thread
    _stop_event = _run_continuously()
    _scheduler_thread = True
    
    logger.info("Scheduler started successfully")

def stop_scheduler():
    """Stop the scheduler thread"""
    global _scheduler_thread, _stop_event
    
    if _scheduler_thread is None:
        logger.warning("Scheduler is not running")
        return
    
    logger.info("Stopping scheduler...")
    
    # Clear all scheduled jobs
    schedule.clear()
    
    # Stop the scheduler thread
    _stop_event.set()
    _scheduler_thread = None
    
    logger.info("Scheduler stopped successfully")

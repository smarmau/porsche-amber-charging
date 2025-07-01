import asyncio
import logging
from .porsche_service import get_porsche_service, PorscheService
from .price_service import get_price_service, PriceService

logger = logging.getLogger(__name__)

class ChargeController:
    def __init__(self, porsche_service: PorscheService, price_service: PriceService):
        self.porsche_service = porsche_service
        self.price_service = price_service
        self.is_running = False
        self.task = None

    async def _ensure_authenticated(self):
        if not self.porsche_service.is_authenticated():
            logger.info("Not authenticated with Porsche Connect. Authenticating...")
            authenticated = await self.porsche_service.authenticate()
            if not authenticated:
                logger.error("Failed to authenticate with Porsche Connect. Cannot control charging.")
                return False
        return True

    async def run_charging_logic(self):
        logger.info("Running automated charging logic...")
        
        if not await self._ensure_authenticated():
            return

        try:
            current_price = await self.price_service.get_current_price()
            price_threshold = self.price_service.get_price_threshold()

            # Fetch the latest vehicle overview
            vehicle_overview = await self.porsche_service.get_vehicle_overview(force_refresh=True)
            if not vehicle_overview or vehicle_overview.get("error"):
                logger.error(f"Could not retrieve vehicle overview: {vehicle_overview.get('error', 'Unknown error')}")
                return

            # Determine charging state from the overview - check multiple fields
            is_charging = False
            is_plugged_in = False
            
            # Method 1: Check BATTERY_CHARGING_STATE field
            battery_charging_state = vehicle_overview.get('BATTERY_CHARGING_STATE')
            if battery_charging_state is not None:
                state_str = str(battery_charging_state).upper()
                if state_str in ['CHARGING', 'ON']:
                    is_charging = True
                    logger.debug(f"Charging detected via BATTERY_CHARGING_STATE: {state_str}")
            
            # Method 2: Check CHARGING_SUMMARY field (more reliable based on observations)
            charging_summary = vehicle_overview.get('CHARGING_SUMMARY', {})
            if isinstance(charging_summary, dict):
                # Check if charging
                if charging_summary.get('status') == 'CHARGING':
                    is_charging = True
                    is_plugged_in = True
                    logger.debug(f"Charging detected via CHARGING_SUMMARY: {charging_summary.get('status')}")
                # Check if plugged in but not charging
                elif charging_summary.get('status') not in ['NOT_PLUGGED', None]:
                    is_plugged_in = True
                    logger.debug(f"Vehicle is plugged in but not charging. Status: {charging_summary.get('status')}")
                
            # Method 3: Check CHARGING_RATE field for non-zero charging power
            charging_rate = vehicle_overview.get('CHARGING_RATE', {})
            if isinstance(charging_rate, dict) and charging_rate.get('chargingPower', 0) > 0:
                is_charging = True
                is_plugged_in = True
                logger.debug(f"Charging detected via CHARGING_RATE: power={charging_rate.get('chargingPower')}kW")

            logger.info(f"Current Price: {current_price:.2f}c, Threshold: {price_threshold:.2f}c, Currently Charging: {is_charging}, Plugged In: {is_plugged_in}")

            if current_price <= price_threshold:
                if not is_charging:
                    if is_plugged_in:
                        logger.info(f"Price ({current_price:.2f}c) is below threshold ({price_threshold:.2f}c). Starting charging.")
                        await self.porsche_service.start_charging()
                    else:
                        logger.info(f"Price ({current_price:.2f}c) is below threshold, but vehicle is not plugged in. Cannot start charging.")
                else:
                    logger.info(f"Price ({current_price:.2f}c) is below threshold, and car is already charging. No action needed.")
            else:  # price > threshold
                if is_charging:
                    logger.info(f"Price ({current_price:.2f}c) is above threshold ({price_threshold:.2f}c). Stopping charging.")
                    await self.porsche_service.stop_charging()
                else:
                    logger.info(f"Price ({current_price:.2f}c) is above threshold, and car is not charging. No action needed.")

        except Exception as e:
            logger.error(f"An error occurred in the charging logic: {e}", exc_info=True)

    async def _periodic_task(self):
        while self.is_running:
            await self.run_charging_logic()
            await asyncio.sleep(5 * 60) # 5 minutes

    def start(self):
        if not self.is_running:
            logger.info("Starting Charge Controller background task.")
            self.is_running = True
            self.task = asyncio.create_task(self._periodic_task())
        else:
            logger.warning("Charge Controller is already running.")

    def stop(self):
        if self.is_running:
            logger.info("Stopping Charge Controller background task.")
            self.is_running = False
            if self.task:
                self.task.cancel()
                self.task = None
        else:
            logger.warning("Charge Controller is not running.")

_charge_controller = None

def get_charge_controller() -> ChargeController:
    global _charge_controller
    if _charge_controller is None:
        _charge_controller = ChargeController(get_porsche_service(), get_price_service())
    return _charge_controller

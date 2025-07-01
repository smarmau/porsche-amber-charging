import os
import json
import logging
import asyncio
from pathlib import Path
from typing import Dict, Any, Optional, List

from pyporscheconnectapi.connection import Connection
from pyporscheconnectapi.account import PorscheConnectAccount
from pyporscheconnectapi.exceptions import PorscheCaptchaRequiredError, PorscheWrongCredentialsError, PorscheRemoteServiceError, PorscheExceptionError
from pyporscheconnectapi.remote_services import RemoteServices

from ..core.config import settings
from ..utils.captcha_solver import CaptchaSolver

logger = logging.getLogger(__name__)

class PorscheService:
    """Service for interacting with Porsche Connect API"""
    
    def __init__(self):
        self.conn = None
        self.account = None
        self.vehicle = None
        self.captcha_solver = CaptchaSolver()
        
    def is_authenticated(self) -> bool:
        """Check if we are authenticated and have a vehicle selected."""
        return self.vehicle is not None

    async def authenticate_if_needed(self) -> bool:
        """Authenticates with Porsche Connect if not already authenticated."""
        if self.is_authenticated():
            return True
        return await self.authenticate()
    
    async def authenticate(self) -> bool:
        """Authenticate with Porsche Connect API"""
        try:
            # Try to load session from file
            token = self._load_session()
            
            if token:
                # Try to use existing token
                self.conn = Connection(token=token)
                self.account = PorscheConnectAccount(connection=self.conn)
                try:
                    await self.account.get_vehicles()
                    logger.info("Successfully authenticated using saved session")
                    await self._select_vehicle()
                    return True
                except Exception as e:
                    logger.warning(f"Saved session is invalid or expired: {e}")
                    # Continue with fresh login
            
            # Authenticate with username/password
            email = settings.PORSCHE_EMAIL
            password = settings.PORSCHE_PASSWORD
            
            if not email or not password:
                logger.error("Missing Porsche Connect credentials")
                return False
            
            captcha_code, captcha_state = None, None
            max_attempts = 3
            
            for attempt in range(max_attempts):
                try:
                    self.conn = Connection(email, password, captcha_code=captcha_code, state=captcha_state)
                    self.account = PorscheConnectAccount(connection=self.conn)
                    await self.account.get_vehicles()
                    self._save_session(self.conn.token)
                    logger.info("Successfully authenticated with Porsche Connect")
                    await self._select_vehicle()
                    return True
                except PorscheCaptchaRequiredError as e:
                    logger.warning("CAPTCHA challenge received")
                    captcha_code = self.captcha_solver.solve_image_captcha(e.captcha)
                    captcha_state = e.state
                    
                    if not captcha_code:
                        logger.error("Failed to solve CAPTCHA")
                        return False
                    
                    logger.info(f"CAPTCHA solved: {captcha_code}")
                except PorscheWrongCredentialsError:
                    logger.error("Wrong Porsche Connect credentials")
                    return False
                except Exception as e:
                    logger.error(f"Authentication error: {e}")
                    return False
            
            logger.error(f"Failed to authenticate after {max_attempts} attempts")
            return False
        
        except Exception as e:
            logger.error(f"Unexpected error during authentication: {e}")
            return False
    
    async def _select_vehicle(self):
        """Select the first vehicle from the account"""
        if not self.account:
            return
        
        vehicles = await self.account.get_vehicles()
        if not vehicles:
            logger.warning("No vehicles found in account")
            return
        
        self.vehicle = vehicles[0]
        logger.info(f"Selected vehicle: {self.vehicle.model_name} ({self.vehicle.vin})")
    
    def _save_session(self, token):
        """Save session token to file"""
        try:
            logger.info(f"Saving session to {settings.SESSION_FILE}")
            with open(settings.SESSION_FILE, "w") as f:
                json.dump(token, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save session: {e}")
    
    def _load_session(self):
        """Load session token from file"""
        try:
            if settings.SESSION_FILE.exists():
                logger.info(f"Loading session from {settings.SESSION_FILE}")
                with open(settings.SESSION_FILE, "r") as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load session: {e}")
        return None
    
    async def get_vehicle_overview(self, force_refresh: bool = False) -> Dict[str, Any]:
        """Fetches the complete vehicle overview data."""
        if not await self.authenticate_if_needed():
            return {"error": "Authentication failed."}

        if force_refresh:
            logger.info("Forcing refresh of vehicle data.")

        try:
            await self._get_overview_with_retry(force_refresh=force_refresh)
            logger.debug(f"Full vehicle data: {self.vehicle.data}")
            return self.vehicle.data
        except PorscheExceptionError as e:
            logger.error(f"Porsche API error in get_vehicle_overview: {e}")
            return {"error": f"Could not retrieve vehicle overview due to API error: {e}"}
        except Exception as e:
            logger.error(f"Unexpected error in get_vehicle_overview: {e}", exc_info=True)
            return {"error": "An unexpected error occurred while fetching vehicle overview."}

    async def _get_overview_with_retry(self, force_refresh: bool = False):
        """Calls get_current_overview with retry logic for timeouts."""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                overview = await self.vehicle.get_current_overview()
                return overview
            except KeyError as e:
                if "CHARGING_SUMMARY" in str(e) and attempt < max_retries - 1:
                    wait_time = 2 ** (attempt + 1)
                    logger.warning(f"Incomplete data from Porsche API (missing {e}). Retrying in {wait_time}s... (Attempt {attempt + 1}/{max_retries})")
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(f"Failed to get vehicle overview due to missing key {e} after {attempt + 1} attempts.")
                    raise
            except PorscheExceptionError as e:
                if hasattr(e, 'status_code') and e.status_code == 504 and attempt < max_retries - 1:
                    wait_time = 2 ** (attempt + 1)  # Exponential backoff: 2, 4 seconds
                    logger.warning(f"Gateway timeout from Porsche API. Retrying in {wait_time}s... (Attempt {attempt + 1}/{max_retries})")
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(f"Failed to get vehicle overview after {attempt + 1} attempts.")
                    raise e
    
    async def start_charging(self) -> bool:
        """Start charging the vehicle"""
        if not self.vehicle:
            await self._select_vehicle()
            if not self.vehicle:
                logger.error("No vehicle available for charging control")
                return False
        
        try:
            services = RemoteServices(self.vehicle)
            
            # Set target SoC to 100% for testing as requested
            target_soc = 100  # Using 100% as requested for testing instead of settings.TARGET_SOC
            logger.info(f"Setting target SoC to {target_soc}% before starting charge")
            
            if not hasattr(services, 'update_charging_setting'):
                logger.error("`update_charging_setting` method not found in RemoteServices. Cannot set target SoC. Please check pyporscheconnectapi library.")
            else:
                try:
                    logger.info(f"Calling update_charging_setting with target_soc={target_soc}...")
                    update_result = await services.update_charging_setting(target_soc=target_soc)
                    if update_result and getattr(update_result, 'status', None) in ['SUCCESS', 'PERFORMED']:
                        logger.info(f"Successfully set target SoC to {target_soc}%. Waiting 5 seconds...")
                        await asyncio.sleep(5)  # Wait for setting to apply
                    else:
                        status = getattr(update_result, 'status', 'N/A')
                        message = getattr(update_result, 'message', 'N/A')
                        logger.warning(f"Call to update_charging_setting did not return SUCCESS. Status: '{status}', Message: '{message}'. Proceeding to start charge anyway.")
                        await asyncio.sleep(2)  # Shorter wait if it didn't succeed
                except Exception as e:
                    logger.error(f"Error during update_charging_setting: {e}. Proceeding to start charge.")
            
            # Start charging
            logger.info("Sending command to start charging...")
            result = await services.direct_charge_on()
            
            # Final verification
            if result and getattr(result, 'status', None) in ['SUCCESS', 'PERFORMED']:
                logger.info("Successfully sent 'Start charging' command. Verifying status in 10 seconds...")
                await asyncio.sleep(10)  # Give more time for the change to propagate
                
                # Verify the charging status
                try:
                    await self._get_overview_with_retry()
                    charging_state = self.vehicle.data.get('BATTERY_CHARGING_STATE')
                    battery_level = self.vehicle.data.get('BATTERY_LEVEL', {}).get('percent')
                    logger.info(f"Current charging state: {charging_state}, Battery level: {battery_level}%")
                    return True
                except Exception as verify_err:
                    logger.error(f"Error verifying charging status: {verify_err}")
                    # Still return True since the command was successful
                    return True
            else:
                status = getattr(result, 'status', 'N/A')
                message = getattr(result, 'message', 'N/A')
                logger.error(f"Command 'Start charging' did not return SUCCESS. Status: '{status}', Message: '{message}'")
                return False
        
        except Exception as e:
            logger.error(f"Error starting charging: {e}")
            return False
    
    async def stop_charging(self) -> bool:
        """Stop charging the vehicle"""
        if not self.vehicle:
            await self._select_vehicle()
            if not self.vehicle:
                logger.error("No vehicle available for charging control")
                return False
        
        try:
            services = RemoteServices(self.vehicle)
            
            logger.info("Sending command to stop charging (with force=True to override schedules)...")
            logger.info("Attempting to clear schedule by setting target SoC to current level, then stopping charge.")
            
            # Refresh vehicle data
            await self._get_overview_with_retry()
            current_soc = self.vehicle.data.get('BATTERY_LEVEL', {}).get('percent')
            
            if current_soc is None:
                logger.warning("Could not determine current battery level. Using fallback SoC of 25% for update_charging_setting.")
                target_update_soc = 25  # Fallback to minimum allowed by library if current_soc not found
            else:
                logger.info(f"Current SoC is {current_soc}%. Using this for update_charging_setting.")
                # Ensure target_soc is within the library's accepted range (25-100)
                target_update_soc = min(max(int(current_soc), 25), 100)
            
            if not hasattr(services, 'update_charging_setting'):
                logger.error("`update_charging_setting` method not found in RemoteServices. Cannot attempt to clear schedule. Please check pyporscheconnectapi library.")
            else:
                try:
                    logger.info(f"Calling update_charging_setting with target_soc={target_update_soc}...")
                    update_result = await services.update_charging_setting(target_soc=target_update_soc)
                    if update_result and getattr(update_result, 'status', None) in ['SUCCESS', 'PERFORMED']:
                        logger.info(f"Successfully called update_charging_setting. Waiting 5 seconds...")
                        await asyncio.sleep(5)
                    else:
                        status = getattr(update_result, 'status', 'N/A')
                        message = getattr(update_result, 'message', 'N/A')
                        logger.warning(f"Call to update_charging_setting did not return SUCCESS. Status: '{status}', Message: '{message}'. Proceeding to stop charge anyway.")
                        await asyncio.sleep(2)  # Shorter wait if it didn't succeed
                except Exception as e:
                    logger.error(f"Error during update_charging_setting: {e}. Proceeding to stop charge.")
            
            # Proceed to send direct_charge_off command
            logger.info("Proceeding to send direct_charge_off command.")
            result = await services.direct_charge_off()  # The library doesn't support force=True parameter
            
            # Final verification
            if result and getattr(result, 'status', None) in ['SUCCESS', 'PERFORMED']:
                logger.info("Successfully sent 'Stop charging' command. Verifying status in 10 seconds...")
                await asyncio.sleep(10)  # Give more time for the change to propagate
                
                # Verify the charging status
                try:
                    await self._get_overview_with_retry()
                    charging_state = self.vehicle.data.get('BATTERY_CHARGING_STATE')
                    battery_level = self.vehicle.data.get('BATTERY_LEVEL', {}).get('percent')
                    logger.info(f"Current charging state: {charging_state}, Battery level: {battery_level}%")
                    return True
                except Exception as verify_err:
                    logger.error(f"Error verifying charging status: {verify_err}")
                    # Still return True since the command was successful
                    return True
            else:
                status = getattr(result, 'status', 'N/A')
                message = getattr(result, 'message', 'N/A')
                logger.error(f"Command 'Stop charging' did not return SUCCESS. Status: '{status}', Message: '{message}'")
                return False
        
        except Exception as e:
            logger.error(f"Error stopping charging: {e}")
            return False

# Singleton instance
_porsche_service = None

def get_porsche_service() -> PorscheService:
    """Get the singleton instance of PorscheService"""
    global _porsche_service
    if _porsche_service is None:
        _porsche_service = PorscheService()
    return _porsche_service

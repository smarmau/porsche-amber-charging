import os
import asyncio
import base64
import logging
import subprocess
import sys
import json
from getpass import getpass
from pathlib import Path

from dotenv import load_dotenv
from pyporscheconnectapi.connection import Connection
from pyporscheconnectapi.account import PorscheConnectAccount
from pyporscheconnectapi.exceptions import PorscheCaptchaRequiredError, PorscheWrongCredentialsError, PorscheRemoteServiceError
from pyporscheconnectapi.remote_services import RemoteServices

# --- CONFIGURATION ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
load_dotenv()
SESSION_FILE = Path("porsche_session.json")

# --- HELPER FUNCTIONS ---

def save_and_open_captcha(captcha_data: str):
    """Saves the CAPTCHA image and opens it for the user."""
    captcha_path = ""
    try:
        if "data:image/svg+xml;base64" in captcha_data:
            file_extension = "svg"
            img_data_b64 = captcha_data.split(",")[1]
            img_data_decoded = base64.b64decode(img_data_b64).decode('utf-8')
            if '<rect' not in img_data_decoded:
                img_data_decoded = img_data_decoded.replace('<svg ', '<svg><rect width="100%" height="100%" fill="white"/>', 1)
            captcha_path = os.path.abspath("captcha.svg")
            with open(captcha_path, "w") as f:
                f.write(img_data_decoded)
        else:
            file_extension = "jpg"
            img_data_b64 = captcha_data.split(",")[1]
            captcha_path = os.path.abspath("captcha.jpg")
            with open(captcha_path, "wb") as f:
                f.write(base64.b64decode(img_data_b64))

        logger.info(f"CAPTCHA image saved to {captcha_path}")
        if sys.platform == "win32": os.startfile(captcha_path)
        elif sys.platform == "darwin": subprocess.run(["open", captcha_path], check=True)
        else: subprocess.run(["xdg-open", captcha_path], check=True)
    except Exception as e:
        logger.error(f"Could not open CAPTCHA image automatically. Please open it manually: {captcha_path}")
        logger.error(e)

def save_session(token):
    """Saves the session token to a file."""
    logger.info(f"Saving session to {SESSION_FILE}")
    with open(SESSION_FILE, "w") as f:
        json.dump(token, f, indent=2)

def load_session():
    """Loads the session token from a file."""
    if SESSION_FILE.exists():
        logger.info(f"Loading session from {SESSION_FILE}")
        with open(SESSION_FILE, "r") as f:
            return json.load(f)
    return None

# --- MENU ACTIONS ---

async def display_status(vehicle):
    """Fetches and displays the current status of the vehicle."""
    print("\nFetching latest vehicle status...")
    try:
        await vehicle.get_current_overview()
        print("--- Vehicle Status ---")
        for key, value in sorted(vehicle.data.items()):
            print(f"  {key}: {value}")
        print("----------------------")
    except Exception as e:
        logger.error(f"Failed to fetch status: {e}")

async def display_charging_status(vehicle):
    """Fetches and displays only the charging-related status of the vehicle."""
    print("\n--- Charging Status Verification ---")
    try:
        await vehicle.get_current_overview()
        charging_keys = ['BATTERY_LEVEL', 'CHARGING_SUMMARY', 'E_RANGE', 'BATTERY_CHARGING_STATE', 'TIMERS']
        for key in charging_keys:
            if key in vehicle.data:
                print(f"  {key}: {vehicle.data[key]}")
            else:
                print(f"  {key}: Not available")
        print("----------------------------------")
    except Exception as e:
        logger.error(f"Failed to fetch charging status: {e}")

async def toggle_charging(vehicle, turn_on: bool):
    """Turns direct charging on or off. Uses force=True for 'off' to override schedules."""
    action = "Enabling" if turn_on else "Disabling"
    print(f"\n{action} direct charging...")
    services = RemoteServices(vehicle)
    try:
        result = None
        if turn_on:
            target_on_soc = 80 # Target SoC for turning charging ON
            logger.info(f"Setting target SoC to {target_on_soc}% before starting charge.")
            if not hasattr(services, 'update_charging_setting'):
                logger.error("`update_charging_setting` method not found in RemoteServices. Cannot set target SoC. Please check pyporscheconnectapi library.")
            else:
                try:
                    logger.info(f"Calling update_charging_setting with target_soc={target_on_soc}...")
                    update_result = await services.update_charging_setting(target_soc=target_on_soc)
                    if update_result and getattr(update_result, 'status', None) in ['SUCCESS', 'PERFORMED']:
                        logger.info(f"Successfully called update_charging_setting. Waiting 5 seconds...")
                        await asyncio.sleep(5)
                    else:
                        status = getattr(update_result, 'status', 'N/A')
                        message = getattr(update_result, 'message', 'N/A')
                        logger.warning(f"Call to update_charging_setting did not return SUCCESS. Status: '{status}', Message: '{message}'. Proceeding to start charge anyway.")
                        await asyncio.sleep(2) # Shorter wait if it didn't succeed
                except PorscheRemoteServiceError as e:
                    logger.error(f"PorscheRemoteServiceError during update_charging_setting: {e}. Proceeding to start charge.")
                except Exception as e:
                    logger.error(f"Unexpected error during update_charging_setting: {e}. Proceeding to start charge.")

            logger.info("Sending command to start charging...")
            result = await services.direct_charge_on()
        else:
            logger.info("Sending command to stop charging (with force=True to override schedules)...")
            logger.info("Attempting to clear schedule by setting target SoC to current level, then stopping charge.")
            await vehicle.get_current_overview()  # Refresh data

            current_soc = vehicle.data.get('BATTERY_LEVEL', {}).get('percent')
            if current_soc is None:
                logger.warning("Could not determine current battery level. Using fallback SoC of 25% for update_charging_setting.")
                target_update_soc = 25 # Fallback to minimum allowed by library if current_soc not found
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
                        await asyncio.sleep(2) # Shorter wait if it didn't succeed
                except PorscheRemoteServiceError as e:
                    logger.error(f"PorscheRemoteServiceError during update_charging_setting: {e}. Proceeding to stop charge.")
                except Exception as e:
                    logger.error(f"Unexpected error during update_charging_setting: {e}. Proceeding to stop charge.")

            logger.info("Proceeding to send direct_charge_off command.")
            result = await services.direct_charge_off()

        # Final verification
        if result and getattr(result, 'status', None) in ['SUCCESS', 'PERFORMED']:
            logger.info(f"Successfully sent '{action} charging' command. Verifying status in 10 seconds...")
            await asyncio.sleep(10) # Give more time for the change to propagate
            await display_charging_status(vehicle)
        else:
            status = getattr(result, 'status', 'N/A')
            message = getattr(result, 'message', 'N/A')
            logger.error(f"Command '{action} charging' did not return SUCCESS. Status: '{status}', Message: '{message}'")

    except PorscheRemoteServiceError as e:
        logger.error(f"A remote service error occurred: {e}")
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}", exc_info=True)

# --- MAIN APPLICATION LOGIC ---

async def login_flow():
    """Handles the complete login and authentication process."""
    token = load_session()
    conn = None
    account = None

    if token:
        conn = Connection(token=token)
        account = PorscheConnectAccount(connection=conn)
        try:
            await account.get_vehicles() # Test the token
            logger.info("Successfully connected using saved session.")
            return conn, account
        except Exception:
            logger.warning("Saved session is invalid or expired. Re-authenticating...")
            token = None # Invalidate token

    # Manual login flow if no valid token
    email = os.getenv("PORSCHE_EMAIL") or input("Please enter your Porsche Connect email: ")
    password = os.getenv("PORSCHE_PASSWORD") or getpass("Please enter your Porsche Connect password: ")
    captcha_code, captcha_state = None, None

    while True:
        try:
            conn = Connection(email, password, captcha_code=captcha_code, state=captcha_state)
            account = PorscheConnectAccount(connection=conn)
            await account.get_vehicles()
            save_session(conn.token)
            logger.info("Successfully authenticated.")
            return conn, account
        except PorscheCaptchaRequiredError as e:
            logger.warning("CAPTCHA challenge received.")
            save_and_open_captcha(e.captcha)
            captcha_code = input("Please enter the CAPTCHA solution: ")
            captcha_state = e.state
        except PorscheWrongCredentialsError:
            logger.error("Wrong credentials. Please check and try again.")
            return None, None
        except Exception as e:
            logger.error(f"An unexpected login error occurred: {e}")
            return None, None

async def main():
    """Main application entry point and menu loop."""
    conn, account = await login_flow()

    if not account:
        logger.error("Login failed. Exiting.")
        if conn: await conn.close()
        return

    vehicles = await account.get_vehicles()
    if not vehicles:
        logger.error("No vehicles found in account. Exiting.")
        await conn.close()
        return
    
    vehicle = vehicles[0]
    print(f"\nWelcome! Connected to {vehicle.model_name} ({vehicle.vin})")

    while True:
        print("\n--- Menu ---")
        print("1. Check Status")
        print("2. Turn ON Charging")
        print("3. Turn OFF Charging")
        print("4. Exit")
        choice = input("Enter your choice: ")

        if choice == '1':
            await display_status(vehicle)
        elif choice == '2':
            await toggle_charging(vehicle, turn_on=True)
        elif choice == '3':
            await toggle_charging(vehicle, turn_on=False)
        elif choice == '4':
            break
        else:
            print("Invalid choice, please try again.")

    await conn.close()
    print("\nGoodbye!")

if __name__ == "__main__":
    asyncio.run(main())

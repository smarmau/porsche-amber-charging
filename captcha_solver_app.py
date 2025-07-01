import os
import asyncio
import base64
import logging
import subprocess
import sys
import json
import requests
import io
from getpass import getpass
from pathlib import Path
from time import sleep

from dotenv import load_dotenv
from pyporscheconnectapi.connection import Connection
from pyporscheconnectapi.account import PorscheConnectAccount
from pyporscheconnectapi.exceptions import PorscheCaptchaRequiredError, PorscheWrongCredentialsError, PorscheRemoteServiceError
from pyporscheconnectapi.remote_services import RemoteServices

# Try to import cairosvg for SVG conversion
try:
    import cairosvg
    HAS_CAIROSVG = True
except ImportError:
    HAS_CAIROSVG = False
    logging.warning("cairosvg not installed. SVG to PNG conversion will not be available.")
    logging.warning("Install with: pip install cairosvg")

# --- CONFIGURATION ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
load_dotenv()
SESSION_FILE = Path("porsche_session.json")
CAPTCHA_FILE = Path("captcha_image")
CAPTCHA_PNG_FILE = Path("captcha_image.png")

# --- 2CAPTCHA INTEGRATION ---
class CaptchaSolver:
    """Class to handle captcha solving via 2captcha API"""
    def __init__(self):
        self.api_key = os.getenv("2CAPTCHA_API_KEY")
        if not self.api_key:
            logger.error("2CAPTCHA_API_KEY not found in .env file")
            raise ValueError("2CAPTCHA_API_KEY is required in .env file")
        
        self.base_url = "https://2captcha.com/in.php"
        self.result_url = "https://2captcha.com/res.php"
    
    def solve_image_captcha(self, image_data):
        """Solve image captcha using 2captcha API"""
        logger.info("Processing captcha image...")
        
        # Determine if it's SVG or another format
        is_svg = "svg" in image_data.lower()
        
        # Extract the base64 image data
        if "base64" in image_data:
            img_data_b64 = image_data.split(",")[1]
        else:
            img_data_b64 = image_data
        
        # Save original image for debugging
        self._save_captcha_image(img_data_b64, image_data)
        
        # For SVG, we need to convert to JPG
        jpg_file_path = None
        if is_svg:
            logger.info("Converting SVG to JPG for 2captcha...")
            jpg_data = self._convert_svg_to_png(img_data_b64)
            if jpg_data:
                # Save the JPG data to a file
                jpg_file_path = "captcha_image.jpg"
                with open(jpg_file_path, "wb") as f:
                    f.write(jpg_data)
                logger.info(f"Successfully converted SVG to JPG and saved to {jpg_file_path}")
            else:
                logger.error("Failed to convert SVG to JPG. Cannot proceed with 2captcha.")
                return None
        else:
            # For non-SVG formats, save directly to a file
            jpg_file_path = "captcha_image.jpg"
            with open(jpg_file_path, "wb") as f:
                f.write(base64.b64decode(img_data_b64))
            logger.info(f"Saved image to {jpg_file_path}")
        
        # Try to use the 2captcha Python library (preferred method)
        try:
            from twocaptcha import TwoCaptcha
            
            logger.info("Using 2captcha Python library for solving...")
            solver = TwoCaptcha(self.api_key)
            
            # Solve using the library with the file path
            try:
                logger.info(f"Sending {jpg_file_path} to 2captcha for solving...")
                result = solver.normal(jpg_file_path)
                if result and 'code' in result:
                    solution = result['code']
                    logger.info(f"Captcha solved successfully using 2captcha library: {solution}")
                    return solution
            except Exception as lib_err:
                logger.error(f"Error with 2captcha library: {lib_err}")
                logger.info("Falling back to manual API implementation...")
        except ImportError:
            logger.warning("2captcha Python library not found. Using manual API implementation.")
        
        # Fallback to manual API implementation
        logger.info("Using manual 2captcha API implementation...")
        
        # Read the JPG file and convert to base64
        with open(jpg_file_path, "rb") as f:
            jpg_data = f.read()
        jpg_data_b64 = base64.b64encode(jpg_data).decode('utf-8')
        
        # Prepare the request data
        data = {
            'key': self.api_key,
            'method': 'base64',
            'body': jpg_data_b64,
            'json': 1,
            'phrase': 0,       # Not a phrase
            'regsense': 1,     # Case sensitive
            'numeric': 4,      # Any characters
            'min_len': 4,      # Minimum length
            'max_len': 8,      # Maximum length
            'language': 2,     # Language code for English
            'textinstructions': 'Enter the characters you see in the image',
            'soft_id': 'python_porsche_app',  # Identifier for our app
        }
        
        # Send to 2captcha
        logger.info("Sending captcha to 2captcha for solving...")
        
        try:
            response = requests.post(self.base_url, data=data)
            result = response.json()
            
            if result.get('status') != 1:
                logger.error(f"Failed to submit captcha: {result.get('request')}")
                return None
            
            captcha_id = result.get('request')
            logger.info(f"Captcha submitted successfully. ID: {captcha_id}")
            
            # Wait for the solution
            solution = self._get_captcha_solution(captcha_id)
            return solution
            
        except Exception as e:
            logger.error(f"Error solving captcha: {e}")
            return None
    
    def _convert_svg_to_png(self, svg_data_b64):
        """Convert SVG to JPG format that 2captcha can process using Wand/ImageMagick"""
        try:
            # Decode the base64 SVG data
            svg_data = base64.b64decode(svg_data_b64)
            svg_str = svg_data.decode('utf-8')
            
            # Add white background if needed
            if '<rect' not in svg_str:
                svg_str = svg_str.replace('<svg ', '<svg><rect width="100%" height="100%" fill="white"/>', 1)
            
            # Check if SVG has width and height attributes, add them if not
            if 'width=' not in svg_str or 'height=' not in svg_str:
                logger.info("Adding explicit width and height to SVG")
                # Add default width and height (300x100 is common for captchas)
                svg_str = svg_str.replace('<svg', '<svg width="300" height="100"', 1)
            
            # Save the modified SVG to a temporary file
            temp_svg_path = "temp_captcha.svg"
            with open(temp_svg_path, "w") as f:
                f.write(svg_str)
            
            # Use Wand (ImageMagick) to convert SVG to JPG
            try:
                logger.info("Converting SVG to JPG using Wand/ImageMagick...")
                from wand.image import Image as WandImage
                
                jpg_path = "captcha_image.jpg"
                
                with WandImage(filename=temp_svg_path) as img:
                    # Set the format to JPEG
                    img.format = 'jpeg'
                    
                    # Ensure the image has a white background
                    img.background_color = 'white'
                    img.alpha_channel = 'remove'
                    
                    # Save as JPG with high quality
                    img.compression_quality = 95
                    img.save(filename=jpg_path)
                
                # Read the JPG data
                with open(jpg_path, "rb") as f:
                    jpg_data = f.read()
                
                logger.info(f"Successfully converted SVG to JPG using Wand/ImageMagick and saved to {jpg_path}")
                return jpg_data
            except Exception as wand_err:
                logger.error(f"Wand/ImageMagick conversion failed: {wand_err}")
                
                # Try with direct ImageMagick command
                try:
                    logger.info("Attempting conversion with direct ImageMagick command...")
                    jpg_path = "captcha_image.jpg"
                    
                    # Use subprocess to call ImageMagick's convert command
                    subprocess.run(["convert", "-background", "white", "-flatten", temp_svg_path, jpg_path], check=True)
                    
                    # Read the converted JPG
                    with open(jpg_path, "rb") as f:
                        jpg_data = f.read()
                    
                    logger.info(f"Successfully converted SVG to JPG using ImageMagick command line")
                    return jpg_data
                except Exception as img_err:
                    logger.error(f"ImageMagick command line conversion failed: {img_err}")
                
                # Try with cairosvg as a fallback
                if HAS_CAIROSVG:
                    try:
                        logger.info("Attempting conversion with cairosvg...")
                        # Try with explicit dimensions
                        png_data = cairosvg.svg2png(
                            bytestring=svg_str.encode('utf-8'),
                            parent_width=300,
                            parent_height=100,
                            scale=2.0  # Increase resolution
                        )
                        
                        # Save PNG for debugging
                        with open(CAPTCHA_PNG_FILE, "wb") as f:
                            f.write(png_data)
                        logger.info(f"Converted SVG to PNG and saved to {CAPTCHA_PNG_FILE}")
                        
                        # Convert to JPG using Pillow for better compatibility with 2captcha
                        from PIL import Image
                        from io import BytesIO
                        
                        # Open the PNG data
                        img = Image.open(BytesIO(png_data))
                        
                        # Convert to RGB mode (required for JPG)
                        if img.mode != 'RGB':
                            img = img.convert('RGB')
                        
                        # Save as JPG
                        jpg_path = "captcha_image.jpg"
                        img.save(jpg_path, "JPEG", quality=95)
                        
                        # Read the JPG data
                        with open(jpg_path, "rb") as f:
                            jpg_data = f.read()
                        
                        logger.info(f"Successfully converted PNG to JPG and saved to {jpg_path}")
                        return jpg_data
                    except Exception as cairo_err:
                        logger.warning(f"cairosvg conversion failed: {cairo_err}")
                
                # Last resort: create a simple image with the text "CAPTCHA"
                try:
                    from PIL import Image, ImageDraw, ImageFont
                    
                    # Create a blank white image
                    img = Image.new('RGB', (300, 100), color='white')
                    d = ImageDraw.Draw(img)
                    
                    # Try to find a font
                    try:
                        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 40)
                    except:
                        font = ImageFont.load_default()
                    
                    # Draw a placeholder text
                    d.text((50, 30), "CAPTCHA", fill='black', font=font)
                    
                    # Save as JPG
                    jpg_path = "captcha_image.jpg"
                    img.save(jpg_path, "JPEG", quality=95)
                    
                    with open(jpg_path, "rb") as f:
                        jpg_data = f.read()
                    
                    logger.info(f"Created fallback JPG image with placeholder text")
                    return jpg_data
                except Exception as img_err:
                    logger.error(f"Failed to create fallback image: {img_err}")
                    return None
        except Exception as e:
            logger.error(f"Error converting SVG to JPG: {e}")
            return None
    
    def _get_captcha_solution(self, captcha_id, max_attempts=30, delay=5):
        """Poll 2captcha API for the solution"""
        logger.info(f"Waiting for captcha solution (checking every {delay} seconds)...")
        
        for attempt in range(max_attempts):
            try:
                params = {
                    'key': self.api_key,
                    'action': 'get',
                    'id': captcha_id,
                    'json': 1
                }
                
                response = requests.get(self.result_url, params=params)
                result = response.json()
                
                if result.get('status') == 1:
                    logger.info("Captcha solved successfully!")
                    return result.get('request')
                
                if "CAPCHA_NOT_READY" not in result.get('request', ''):
                    logger.error(f"Error getting captcha solution: {result.get('request')}")
                    return None
                
                logger.info(f"Captcha not ready yet. Attempt {attempt+1}/{max_attempts}")
                sleep(delay)
                
            except Exception as e:
                logger.error(f"Error checking captcha status: {e}")
                return None
        
        logger.error(f"Timed out waiting for captcha solution after {max_attempts} attempts")
        return None
    
    def _save_captcha_image(self, img_data_b64, original_data):
        """Save the captcha image for debugging purposes"""
        try:
            if "svg" in original_data.lower():
                file_extension = ".svg"
                img_data_decoded = base64.b64decode(img_data_b64).decode('utf-8')
                if '<rect' not in img_data_decoded:
                    img_data_decoded = img_data_decoded.replace('<svg ', '<svg><rect width="100%" height="100%" fill="white"/>', 1)
                with open(f"{CAPTCHA_FILE}{file_extension}", "w") as f:
                    f.write(img_data_decoded)
            else:
                file_extension = ".jpg"
                with open(f"{CAPTCHA_FILE}{file_extension}", "wb") as f:
                    f.write(base64.b64decode(img_data_b64))
            
            logger.info(f"Original captcha image saved to {CAPTCHA_FILE}{file_extension}")
        except Exception as e:
            logger.error(f"Failed to save captcha image: {e}")

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
    """Handles the complete login and authentication process with automatic captcha solving."""
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

    # Initialize captcha solver
    try:
        captcha_solver = CaptchaSolver()
    except ValueError as e:
        logger.error(f"{e}. Please add your 2CAPTCHA_API_KEY to the .env file.")
        return None, None

    # Manual login flow if no valid token
    email = os.getenv("PORSCHE_EMAIL") or input("Please enter your Porsche Connect email: ")
    password = os.getenv("PORSCHE_PASSWORD") or getpass("Please enter your Porsche Connect password: ")
    captcha_code, captcha_state = None, None

    # Set max retry attempts for login
    max_login_attempts = 3
    current_attempt = 0

    while current_attempt < max_login_attempts:
        current_attempt += 1
        logger.info(f"Login attempt {current_attempt}/{max_login_attempts}")
        
        try:
            conn = Connection(email, password, captcha_code=captcha_code, state=captcha_state)
            account = PorscheConnectAccount(connection=conn)
            await account.get_vehicles()
            save_session(conn.token)
            logger.info("Successfully authenticated.")
            return conn, account
        except PorscheCaptchaRequiredError as e:
            logger.warning("CAPTCHA challenge received. Attempting to solve automatically...")
            
            # Try automatic solving if API key is available
            if os.getenv("2CAPTCHA_API_KEY"):
                try:
                    captcha_solution = captcha_solver.solve_image_captcha(e.captcha)
                    
                    if captcha_solution:
                        logger.info(f"Automatic CAPTCHA solution obtained: {captcha_solution}")
                        captcha_code = captcha_solution
                        captcha_state = e.state
                        continue  # Try login again with the solution
                except Exception as captcha_err:
                    logger.error(f"Error during automatic captcha solving: {captcha_err}")
            
            # Fall back to manual solving
            logger.warning("Falling back to manual CAPTCHA solving.")
            save_and_open_captcha(e.captcha)
            captcha_code = input("Please enter the CAPTCHA solution: ")
            captcha_state = e.state
        except PorscheWrongCredentialsError:
            logger.error("Wrong credentials. Please check and try again.")
            return None, None
        except Exception as e:
            logger.error(f"An unexpected login error occurred: {e}")
            if current_attempt >= max_login_attempts:
                logger.error(f"Maximum login attempts ({max_login_attempts}) reached. Giving up.")
                return None, None
            logger.info("Retrying login...")
            await asyncio.sleep(2)  # Wait before retrying

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

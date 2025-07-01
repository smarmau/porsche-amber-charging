import os
import base64
import logging
import requests
from pathlib import Path

from ..core.config import settings

logger = logging.getLogger(__name__)

class CaptchaSolver:
    """Class to handle captcha solving via 2captcha API"""
    def __init__(self):
        self.api_key = settings.CAPTCHA_API_KEY
        if not self.api_key:
            logger.warning("2CAPTCHA_API_KEY not found in .env file. Automatic CAPTCHA solving will not be available.")
        
        self.base_url = "https://2captcha.com/in.php"
        self.result_url = "https://2captcha.com/res.php"
    
    def solve_image_captcha(self, image_data):
        """Solve image captcha using 2captcha API"""
        if not self.api_key:
            logger.error("No 2CAPTCHA_API_KEY available. Cannot solve CAPTCHA automatically.")
            return None
            
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
        
        try:
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
                import time
                time.sleep(delay)
                
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
                with open(f"captcha_image{file_extension}", "w") as f:
                    f.write(img_data_decoded)
            else:
                file_extension = ".jpg"
                with open(f"captcha_image{file_extension}", "wb") as f:
                    f.write(base64.b64decode(img_data_b64))
            
            logger.info(f"Original captcha image saved to captcha_image{file_extension}")
        except Exception as e:
            logger.error(f"Failed to save captcha image: {e}")
            
    def _convert_svg_to_png(self, svg_data_b64):
        """Convert SVG to JPG format that 2captcha can process"""
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
            
            # Try with cairosvg as a fallback
            try:
                import cairosvg
                logger.info("Attempting conversion with cairosvg...")
                # Try with explicit dimensions
                png_data = cairosvg.svg2png(
                    bytestring=svg_str.encode('utf-8'),
                    parent_width=300,
                    parent_height=100,
                    scale=2.0  # Increase resolution
                )
                
                # Save PNG for debugging
                png_file = "captcha_image.png"
                with open(png_file, "wb") as f:
                    f.write(png_data)
                logger.info(f"Converted SVG to PNG and saved to {png_file}")
                
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
                
                logger.info(f"Successfully converted SVG to JPG using cairosvg and saved to {jpg_path}")
                return jpg_data
            except Exception as cairo_err:
                logger.error(f"cairosvg conversion failed: {cairo_err}")
                return None
                
        except Exception as e:
            logger.error(f"Error converting SVG to PNG: {e}")
            return None

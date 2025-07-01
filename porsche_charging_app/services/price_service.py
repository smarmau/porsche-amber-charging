import logging
import httpx
import asyncio
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
import json
import os
from pathlib import Path
import base64
import io
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

from ..core.config import settings
from ..models.db import get_db_session, Price, Configuration

logger = logging.getLogger(__name__)

class PriceService:
    """Service for fetching and managing electricity prices"""
    
    def __init__(self):
        # Amber API settings
        self.amber_api_key = settings.AMBER_API_KEY
        self.amber_base_url = "https://api.amber.com.au/v1"
        self.site_id = None
        
        # Fallback to generic API settings if Amber not configured
        self.api_url = settings.PRICE_API_URL
        self.api_key = settings.PRICE_API_KEY
        
        self.cache_file = Path("price_cache.json")
        self.price_history = []
        self._load_cache()
        self._amber_prices_cache = None
        self._amber_prices_cache_timestamp = None
        self._live_prices_cache = None
        self._live_prices_cache_timestamp = None
    
    def _load_cache(self):
        """Load cached price data"""
        try:
            if self.cache_file.exists():
                with open(self.cache_file, "r") as f:
                    cache_data = json.load(f)
                    self.price_history = cache_data.get("history", [])
                    logger.info(f"Loaded {len(self.price_history)} price points from cache")
        except Exception as e:
            logger.error(f"Failed to load price cache: {e}")
            self.price_history = []
    
    async def _get_site_id(self):
        """Fetch the first site ID from the Amber API."""
        if self.site_id:
            return
        if not self.amber_api_key:
            logger.error("Amber API key not set, cannot get site ID.")
            return
        
        headers = {"Authorization": f"Bearer {self.amber_api_key}", "Accept": "application/json"}
        url = f"{self.amber_base_url}/sites"
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                sites = response.json()
                if sites and isinstance(sites, list) and len(sites) > 0:
                    self.site_id = sites[0]['id']
                    logger.info(f"Found Amber site ID: {self.site_id}")
                else:
                    logger.error("No sites found for this Amber account.")
        except Exception as e:
            logger.error(f"Failed to get Amber site ID: {e}")

    async def get_amber_prices(self, hours: int = 12) -> List[Dict[str, Any]]:
        """Fetch upcoming electricity prices from Amber API."""
        if (self._amber_prices_cache and self._amber_prices_cache_timestamp and
                (datetime.now() - self._amber_prices_cache_timestamp) < timedelta(minutes=5)):
            logger.info("Returning cached Amber prices.")
            return self._amber_prices_cache

        if not self.amber_api_key:
            logger.warning("Amber API key not configured. Cannot fetch prices.")
            return []

        await self._get_site_id()
        if not self.site_id:
            return []

        try:
            headers = {
                "Authorization": f"Bearer {self.amber_api_key}",
                "Accept": "application/json"
            }
            # Calculate number of 30-minute periods to fetch
            num_periods = (hours * 60) // 30

            async with httpx.AsyncClient() as client:
                url = f"{self.amber_base_url}/sites/{self.site_id}/prices/current?next={num_periods}&resolution=30"
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                raw_data = response.json()

                # Filter for current and forecast data and transform to the format expected by the frontend
                transformed_data = [
                    {
                        "timestamp": item.get("nemTime"),
                        "price": item.get("perKwh")
                    }
                    for item in raw_data
                    if item.get('type') in ['CurrentInterval', 'ForecastInterval'] 
                    and item.get("perKwh") is not None
                    and item.get("channelType") == "general"
                ]
                
                self._amber_prices_cache = transformed_data
                self._amber_prices_cache_timestamp = datetime.now()
                logger.info(f"Cached {len(transformed_data)} new Amber price points.")

                return transformed_data

        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error fetching Amber prices: {e.response.status_code} - {e.response.text}")
            return []
        except Exception as e:
            logger.error(f"Error fetching Amber prices: {e}")
            return []

    def _save_cache(self):
        """Save price data to cache"""
        try:
            with open(self.cache_file, "w") as f:
                json.dump({"history": self.price_history}, f, indent=2)
            logger.info(f"Saved {len(self.price_history)} price points to cache")
        except Exception as e:
            logger.error(f"Failed to save price cache: {e}")
    
    async def get_current_price(self) -> float:
        """Get the current electricity price"""
        try:
            # Check for mock price override first
            mock_price = self.get_mock_price()
            if mock_price is not None:
                logger.info(f"Using mock price override: {mock_price}")
                return mock_price

            # Check if we have a recent price in the cache (less than 5 minutes old)
            if self.price_history:
                latest = self.price_history[-1]
                timestamp = datetime.fromisoformat(latest["timestamp"])
                if (datetime.now() - timestamp) < timedelta(minutes=5):
                    logger.info(f"Using cached price: {latest['price']}")
                    return latest["price"]
            
            # Fetch new price from API
            price = await self._fetch_price_from_api()
            
            # Store in database
            self._store_price_in_db(price)
            
            # Update cache
            self.price_history.append({
                "timestamp": datetime.now().isoformat(),
                "price": price
            })
            
            # Trim history to keep only the last 24 hours (288 points at 5-minute intervals)
            if len(self.price_history) > 288:
                self.price_history = self.price_history[-288:]
            
            # Save cache
            self._save_cache()
            
            return price
        
        except Exception as e:
            logger.error(f"Error getting current price: {e}")
            
            # Return last known price if available
            if self.price_history:
                logger.info(f"Returning last known price: {self.price_history[-1]['price']}")
                return self.price_history[-1]["price"]
            
            # Return default price as fallback
            logger.info(f"Returning default price: {self.get_price_threshold()}")
            return self.get_price_threshold()
    
    async def _fetch_price_from_api(self, max_retries=2, timeout=10):
        """Fetch the current price from the Amber Electric API
        
        Args:
            max_retries (int): Maximum number of retry attempts for transient errors
            timeout (int): Timeout in seconds for API requests
        """
        logger = logging.getLogger(__name__)
        
        # First check if we should use a mock price
        mock_price = self.get_mock_price()
        if mock_price is not None:
            logger.info(f"Using mock price: {mock_price} c/kWh")
            return mock_price
        
        # If we have a generic API URL configured, use that
        if self.api_url:
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    headers = {}
                    if self.api_key:
                        headers["Authorization"] = f"Bearer {self.api_key}"
                    response = await client.get(self.api_url, headers=headers)
                    response.raise_for_status()
                    data = response.json()
                    price = float(data.get("price", 0.0)) # Assuming cents
                    return price
            except (httpx.ConnectTimeout, httpx.ReadTimeout) as e:
                logger.error(f"Timeout connecting to generic API: {e}")
                logger.warning("Falling back to Amber API due to generic API timeout.")
                # Fall through to Amber API
            except Exception as e:
                logger.error(f"Error with generic API: {e}")
                # Fall through to Amber API
        
        # Use Amber Electric API with retry mechanism
        retry_count = 0
        while retry_count <= max_retries:
            try:
                if not self.site_id:
                    try:
                        await self._get_amber_site_id()
                    except Exception as e:
                        logger.error(f"Failed to get Amber site ID: {e}")
                        return self.get_price_threshold()
                
                if not self.site_id:
                    logger.error("Failed to get Amber site ID, returning threshold.")
                    return self.get_price_threshold()
                
                headers = {"Authorization": f"Bearer {self.amber_api_key}"}
                url = f"{self.amber_base_url}/sites/{self.site_id}/prices/current"
                
                async with httpx.AsyncClient(headers=headers, timeout=timeout) as client:
                    response = await client.get(url)
                    response.raise_for_status()
                    data = response.json()
                    
                    if data and isinstance(data, list) and len(data) > 0:
                        general_prices = [p for p in data if p.get('channelType') == 'general']
                        if not general_prices:
                            logger.warning("No 'general' channel prices found in Amber API response.")
                            return self.get_price_threshold()

                        sorted_prices = sorted(general_prices, key=lambda x: x.get('nemTime', ''), reverse=True)
                        current_price_data = sorted_prices[0]
                        
                        price_in_cents = current_price_data.get('perKwh', 0.0)
                        logger.info(f"Current price from API: {price_in_cents} c/kWh")
                        return price_in_cents
                    else:
                        logger.warning("No price data returned from Amber API")
                        return self.get_price_threshold()
                        
            except (httpx.ConnectTimeout, httpx.ReadTimeout) as e:
                retry_count += 1
                if retry_count <= max_retries:
                    wait_time = 2 ** retry_count  # Exponential backoff: 2s, 4s
                    logger.warning(f"Connection timeout to Amber API (attempt {retry_count}/{max_retries}). Retrying in {wait_time}s...")
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(f"Connection timeout to Amber API after {max_retries} retries: {e}")
                    return self.get_price_threshold()
            except httpx.HTTPStatusError as e:
                logger.error(f"HTTP error from Amber API: {e.response.status_code} - {e.response.text}")
                return self.get_price_threshold()
            except httpx.RequestError as e:
                logger.error(f"Request error to Amber API: {e}")
                retry_count += 1
                if retry_count <= max_retries:
                    wait_time = 2 ** retry_count
                    logger.warning(f"Request error (attempt {retry_count}/{max_retries}). Retrying in {wait_time}s...")
                    await asyncio.sleep(wait_time)
                else:
                    return self.get_price_threshold()
            except Exception as e:
                logger.error(f"Failed to fetch price from Amber API: {e}", exc_info=True)
                logger.warning("Falling back to price threshold due to API error.")
                return self.get_price_threshold()
    
    def _store_price_in_db(self, price: float):
        """Store the price in the database"""
        try:
            session = get_db_session()
            price_record = Price(
                timestamp=datetime.now(),
                value=price
            )
            session.add(price_record)
            session.commit()
        except Exception as e:
            logger.error(f"Failed to store price in database: {e}")
    
    async def _get_amber_site_id(self) -> Optional[str]:
        """Get the user's site ID from Amber API"""
        try:
            if not self.amber_api_key or self.amber_api_key.strip() == "":
                logger.error("Amber API key is empty or not configured")
                return None
                
            headers = {"Authorization": f"Bearer {self.amber_api_key}"}
            url = f"{self.amber_base_url}/sites"
            
            async with httpx.AsyncClient(headers=headers) as client:
                response = await client.get(url)
                response.raise_for_status()
                
                sites = response.json()
                
                if sites and len(sites) > 0:
                    # Use the first site ID
                    self.site_id = sites[0].get('id')
                    logger.info(f"Using Amber site ID: {self.site_id}")
                    return self.site_id
                else:
                    logger.warning("No sites found in Amber account")
                    return None
                    
        except Exception as e:
            logger.error(f"Failed to get Amber site ID: {e}")
            return None
            
    async def get_live_prices(self) -> Dict[str, Optional[float]]:
        """Fetch the current live general and feed-in prices from the Amber Electric API"""
        if (self._live_prices_cache and self._live_prices_cache_timestamp and
                (datetime.now() - self._live_prices_cache_timestamp) < timedelta(minutes=5)):
            logger.info("Returning cached live prices.")
            return self._live_prices_cache

        if not self.amber_api_key or not self.amber_api_key.strip():
            logger.warning("Amber API key not configured. Cannot fetch live prices.")
            return {"general": None, "feed_in": None}

        try:
            if not self.site_id:
                await self._get_amber_site_id()
            
            if not self.site_id:
                logger.error("Failed to get Amber site ID, cannot fetch live prices.")
                return {"general": None, "feed_in": None}
            
            headers = {"Authorization": f"Bearer {self.amber_api_key}"}
            # We only need the current price, so `next=1` should be sufficient.
            url = f"{self.amber_base_url}/sites/{self.site_id}/prices/current?next=1"
            
            async with httpx.AsyncClient(headers=headers) as client:
                response = await client.get(url)
                response.raise_for_status()
                data = response.json()
                
                live_prices = {"general": None, "feed_in": None}
                
                if data and isinstance(data, list):
                    # The first item for each channelType is the current one
                    for price_data in data:
                        channel_type = price_data.get('channelType')
                        price = price_data.get('perKwh')
                        
                        if channel_type == 'general' and live_prices['general'] is None:
                            live_prices['general'] = price
                        elif channel_type == 'feedIn' and live_prices['feed_in'] is None:
                            live_prices['feed_in'] = price
                    
                    logger.info(f"Live prices from API: General={live_prices['general']}, Feed-in={live_prices['feed_in']}")
                    self._live_prices_cache = live_prices
                    self._live_prices_cache_timestamp = datetime.now()
                    return live_prices
                else:
                    logger.warning("No live price data returned from Amber API")
                    return live_prices
        
        except Exception as e:
            logger.error(f"Failed to fetch live prices from Amber API: {e}", exc_info=True)
            return {"general": None, "feed_in": None}
            

            
    async def get_recent_prices(self, hours: int = 24) -> List[Dict[str, Any]]:
        """Get price history for the specified number of hours"""
        try:
            # First try to get from Amber API if configured
            if self.amber_api_key:
                amber_prices = await self.get_amber_prices(hours)
                if amber_prices:
                    return amber_prices
            
            # Try to get from database if Amber API failed or not configured
            session = get_db_session()
            cutoff = datetime.now() - timedelta(hours=hours)
            
            db_prices = session.query(Price).filter(
                Price.timestamp >= cutoff
            ).order_by(Price.timestamp).all()
            
            if db_prices:
                return [
                    {"timestamp": p.timestamp.isoformat(), "price": p.value}
                    for p in db_prices
                ]
            
            # Fall back to cache if database is empty
            if self.price_history:
                cutoff_iso = cutoff.isoformat()
                return [
                    p for p in self.price_history
                    if p["timestamp"] >= cutoff_iso
                ]
            
            return []
        
        except Exception as e:
            logger.error(f"Error getting recent prices: {e}")
            return []
    
    def get_config_value(self, key: str, default: Optional[Any] = None) -> Optional[str]:
        session = get_db_session()
        try:
            config_item = session.query(Configuration).filter_by(key=key).first()
            if config_item:
                return config_item.value
            if default is not None:
                self.set_config_value(key, str(default))
                return str(default)
            return None
        finally:
            session.close()

    def set_config_value(self, key: str, value: str):
        session = get_db_session()
        try:
            config_item = session.query(Configuration).filter_by(key=key).first()
            if config_item:
                config_item.value = value
            else:
                config_item = Configuration(key=key, value=value)
                session.add(config_item)
            session.commit()
        finally:
            session.close()

    def get_price_threshold(self) -> float:
        """Get the current price threshold for charging decisions from settings."""
        return settings.PRICE_THRESHOLD

    def set_price_threshold(self, threshold: float) -> bool:
        """Set the price threshold for charging decisions in settings and save to file."""
        try:
            settings.PRICE_THRESHOLD = threshold
            settings.save()
            logger.info(f"Price threshold set to {threshold} and saved to config.")
            return True
        except Exception as e:
            logger.error(f"Failed to set price threshold: {e}")
            return False

    def get_mock_price(self) -> Optional[float]:
        """Get the mock price override from the database"""
        price_str = self.get_config_value("mock_price", "")
        if price_str is not None and price_str != "":
            try:
                return float(price_str)
            except (ValueError, TypeError):
                return None
        return None

    def set_mock_price(self, price: Optional[float]):
        """Set the mock price override in the database. Set to None or empty string to disable."""
        value_to_set = str(price) if price is not None else ""
        self.set_config_value("mock_price", value_to_set)
        logger.info(f"Mock price override set to: {price}")

# Singleton instance
_price_service = None

def get_price_service() -> PriceService:
    """Get the singleton instance of PriceService"""
    global _price_service
    if _price_service is None:
        _price_service = PriceService()
    return _price_service

async def generate_price_chart(prices: List[Dict[str, Any]], hours: int = 12) -> str:
    """Generate a chart of electricity prices and return as base64 encoded string"""
    if not prices:
        return ""
        
    # Check if we have an error message instead of actual price data
    if len(prices) == 1 and "error" in prices[0]:
        logger.error(f"Cannot generate price chart: {prices[0]['error']}")
        return ""
        
    try:
        # Extract timestamps and prices
        timestamps = [datetime.fromisoformat(price["timestamp"].replace("Z", "+00:00")) for price in prices]
        price_values = [price["price"] for price in prices]
        
        # Create the figure and plot
        plt.figure(figsize=(8, 3))
        plt.plot(timestamps, price_values, marker='o', linestyle='-', color='#007bff', label='Price (c/kWh)')
        plt.legend()
        
        # Format the plot
        plt.gcf().autofmt_xdate()
        plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
        plt.ylabel('Price (c/kWh)')
        plt.title(f'Electricity Prices - Next {hours} Hours')
        plt.grid(True, alpha=0.3)
        
        # Format the plot
        plt.gcf().autofmt_xdate()
        plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
        plt.ylabel('Price (c/kWh)')
        plt.title(f'Electricity Prices - Next {hours} Hours')
        plt.grid(True, alpha=0.3)
        
        # Save the plot to a BytesIO object
        # Add legend
        plt.legend()
        
        # Set y-axis to start from 0 or slightly below the minimum price
        min_price = min(price_values) if price_values else 0
        plt.ylim(bottom=max(0, min_price * 0.9))
        
        # Save to a bytes buffer
        buf = io.BytesIO()
        plt.savefig(buf, format='png', bbox_inches='tight')
        plt.close()
        
        # Convert to base64
        buf.seek(0)
        img_base64 = base64.b64encode(buf.read()).decode('utf-8')
        return img_base64
        
    except Exception as e:
        logger.error(f"Failed to generate price chart: {e}")
        return ""

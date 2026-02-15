#!/home/pi/src/pumphouse/pistat/venv/bin/python
# -*- coding:utf-8 -*-
"""
E-paper display daemon for pump house monitoring
Updates display every 5 minutes without screen flashing
"""

import sys
import os
import logging
import time
import signal
from datetime import datetime
from pathlib import Path

# Add library paths
script_dir = Path(__file__).parent
sys.path.append(str(script_dir / 'lib'))

import requests
from io import BytesIO
from PIL import Image
from waveshare_epd import epd2in13_V4

# Configuration
UPDATE_INTERVAL = 180  # 3 minutes in seconds
IMAGE_URL = "https://REDACTED-HOST:6443/api/epaper.bmp"
CACHE_FILE = script_dir / "last_display.bmp"
LOG_FILE = script_dir / "epaper_daemon.log"

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)

class EPaperDaemon:
    def __init__(self):
        self.epd = None
        self.running = True
        self.cleanup_done = False
        self.base_image_set = False
        
    def init_display(self):
        """Initialize display once on startup"""
        try:
            logging.info("Initializing e-paper display")
            self.epd = epd2in13_V4.EPD()
            self.epd.init()
            self.epd.Clear(0xFF)  # Clear to white
            logging.info("Display initialized and cleared")
            return True
        except Exception as e:
            logging.error(f"Failed to initialize display: {e}")
            return False
    
    def set_base_image(self, image):
        """Set the base image for partial updates"""
        try:
            self.epd.displayPartBaseImage(self.epd.getbuffer(image))
            self.base_image_set = True
            logging.info("Base image set for partial updates")
        except Exception as e:
            logging.error(f"Failed to set base image: {e}")
            raise
        
    def update_display(self):
        """Fetch and update display image using partial refresh"""
        try:
            # Fetch image
            logging.info("Fetching display image")
            response = requests.get(IMAGE_URL, timeout=45, verify=False)
            response.raise_for_status()
            
            # Convert to proper format
            remote_image = Image.open(BytesIO(response.content))
            bmp_image = remote_image.convert('1')
            
            # Cache it
            bmp_image.save(CACHE_FILE)
            
            # First update: set base image
            if not self.base_image_set:
                self.set_base_image(bmp_image)
            
            # Update display using partial refresh (no flash!)
            self.epd.displayPartial(self.epd.getbuffer(bmp_image))
            logging.info("Display updated successfully (partial refresh)")
            
            return True
            
        except requests.RequestException as e:
            logging.error(f"Network error: {e}")
            return self.display_cached()
            
        except Exception as e:
            logging.error(f"Error updating display: {e}", exc_info=True)
            # On error, reset and try again
            self.base_image_set = False
            if self.init_display():
                return self.display_cached()
            return False
    
    def display_cached(self):
        """Display cached image as fallback"""
        if not CACHE_FILE.exists():
            logging.error("No cached image available")
            return False
            
        try:
            logging.info("Using cached image")
            cached_image = Image.open(CACHE_FILE)
            
            if not self.base_image_set:
                self.set_base_image(cached_image)
            
            self.epd.displayPartial(self.epd.getbuffer(cached_image))
            return True
        except Exception as e:
            logging.error(f"Error displaying cached image: {e}", exc_info=True)
            return False
    
    def signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        if not self.cleanup_done:
            signal_name = signal.Signals(signum).name
            logging.info(f"Received {signal_name}, shutting down...")
            self.running = False
    
    def cleanup(self):
        """Clean shutdown - only run once"""
        if self.cleanup_done:
            return
            
        self.cleanup_done = True
        logging.info("Cleaning up...")
        
        try:
            # Clear display and put to sleep
            if self.epd:
                logging.info("Clearing display...")
                self.epd.init()
                self.epd.Clear(0xFF)
                self.epd.sleep()
                logging.info("Display cleared and sleeping")
            
            epd2in13_V4.epdconfig.module_exit(cleanup=True)
            logging.info("Shutdown complete")
        except Exception as e:
            logging.error(f"Error during cleanup: {e}")
    
    def run(self):
        """Main daemon loop"""
        logging.info("Starting e-paper daemon")
        
        # Set up signal handlers for clean shutdown
        signal.signal(signal.SIGTERM, self.signal_handler)
        signal.signal(signal.SIGINT, self.signal_handler)
        
        try:
            # Initialize display ONCE
            if not self.init_display():
                logging.error("Failed to initialize display, exiting")
                return
            
            # Initial update (sets base image and displays)
            self.update_display()
            
            # Main loop
            while self.running:
                # Sleep in small chunks so we can respond to signals
                for _ in range(UPDATE_INTERVAL):
                    if not self.running:
                        break
                    time.sleep(1)
                
                if not self.running:
                    break
                
                # Update display with partial refresh
                self.update_display()
                
        except KeyboardInterrupt:
            logging.info("Interrupted by user")
        except Exception as e:
            logging.error(f"Fatal error in main loop: {e}", exc_info=True)
        finally:
            self.cleanup()

if __name__ == "__main__":
    # Disable SSL warnings for self-signed cert
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    
    daemon = EPaperDaemon()
    daemon.run()


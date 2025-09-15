import os
import sys
import json
import time
import logging
import asyncio
import threading
import paho.mqtt.client as mqtt
from discord_bot.discord_notify import post_traffic_alerts_async

# ---------------------
# logging
# ---------------------
# Configure logging for standalone operation
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/app/data/ignition.log") if os.path.exists("/app/data") else logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Add startup message
print("Ignition Monitor starting...")
logger.info("Ignition Monitor module loaded")

class IgnitionMonitor:
    def __init__(self):
        self.mqtt_broker = os.getenv("MQTT_BROKER")
        self.mqtt_port = int(os.getenv("MQTT_PORT"))
        self.mqtt_topic = os.getenv("MQTT_TOPIC")
        self.ignition_state = False
        self.last_msg_time = None
        self.ignition_on_time = None
        self.timeout = int(os.getenv("IGNITION_TIMEOUT", 60))

        self.client = mqtt.Client()
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

        # Async loop
        try:
            self.loop = asyncio.get_running_loop()
        except RuntimeError:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)

    # ----------------------------
    # MQTT callbacks
    # ----------------------------
    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            logger.info(f"MQTT: Connected to {self.mqtt_broker}:{self.mqtt_port}")
            client.subscribe(self.mqtt_topic)
            logger.info(f"MQTT: Subscribed to {self.mqtt_topic}")
        else:
            logger.error(f"MQTT: Connection failed with code {rc}")

    def on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
            ignition_on = payload.get("Ignition On", False)
            now = time.time()
            
            if ignition_on:
                self.last_msg_time = now
                # Only trigger alert if ignition state is OFF
                if not self.ignition_state:
                    self.ignition_state = True
                    self.ignition_on_time = now
                    logger.info(f"IGNITION: ON at {time.strftime('%Y-%m-%d %H:%M:%S')}")
                    # Schedule async alert if within 5 minutes
                    if now - self.ignition_on_time <= 300:
                        asyncio.run_coroutine_threadsafe(
                            post_traffic_alerts_async(), self.loop
                        )
                    else:
                        logger.info("Ignition ON event too old, skipping traffic alert")
        except json.JSONDecodeError as e:
            logger.error(f"Failed to decode JSON message: {msg.payload} - {e}")
        except Exception as e:
            logger.error(f"Processing message failed: {e}")

    async def _monitor_ignition(self):
        while True:
            if self.ignition_state and self.last_msg_time:
                if time.time() - self.last_msg_time > self.timeout:
                    self.ignition_state = False
                    self.ignition_on_time = None
                    self.last_msg_time = None
                    logger.info(f"IGNITION: OFF at {time.strftime('%Y-%m-%d %H:%M:%S')} (timeout)")
            await asyncio.sleep(1)

    def start(self):
        if not all([self.mqtt_broker, self.mqtt_port, self.mqtt_topic]):
            raise ValueError("Missing MQTT configuration")
        
        logger.info(f"Starting MQTT connection to {self.mqtt_broker}:{self.mqtt_port}")
        logger.info(f"Monitoring topic: {self.mqtt_topic}")
        logger.info(f"Ignition timeout: {self.timeout}s")
        
        # Start OFF/timeout monitor as async task
        threading.Thread(target=lambda: self.loop.run_until_complete(self._monitor_ignition()), daemon=True).start()
        
        # Start MQTT loop (blocking)
        self.client.connect(self.mqtt_broker, self.mqtt_port, 60)
        logger.info("Starting MQTT loop...")
        self.client.loop_forever()


# ----------------------------
# Entry point
# ----------------------------
def main():
    try:
        logger.info("Initializing Ignition Monitor...")
        monitor = IgnitionMonitor()
        monitor.start()
    except KeyboardInterrupt:
        logger.info("Ignition Monitor stopped by user")
    except Exception as e:
        logger.error(f"Ignition Monitor crashed: {e}")
        raise


if __name__ == "__main__":
    main()
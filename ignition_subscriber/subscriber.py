import os
import json
import time
import threading
import asyncio
import paho.mqtt.client as mqtt
from discord_bot.discord_notify import post_traffic_alerts_async

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
            print(f"MQTT: Connected to {self.mqtt_broker}:{self.mqtt_port}")
            client.subscribe(self.mqtt_topic)
            print(f"MQTT: Subscribed to {self.mqtt_topic}")
        else:
            print(f"MQTT: Connection failed with code {rc}")

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
                    print(f"IGNITION: ON at {time.strftime('%Y-%m-%d %H:%M:%S')}")
                    # Schedule async alert if within 5 minutes
                    if now - self.ignition_on_time <= 300:
                        asyncio.run_coroutine_threadsafe(
                            post_traffic_alerts_async(), self.loop
                        )
                    else:
                        print("INFO: Ignition ON event too old, skipping traffic alert")

        except json.JSONDecodeError as e:
            print(f"ERROR: Failed to decode JSON message: {msg.payload} - {e}")
        except Exception as e:
            print(f"ERROR: Processing message failed: {e}")

    # ----------------------------
    # Monitor ignition OFF / timeout
    # ----------------------------
    async def _monitor_ignition(self):
        while True:
            if self.ignition_state and self.last_msg_time:
                if time.time() - self.last_msg_time > self.timeout:
                    self.ignition_state = False
                    self.ignition_on_time = None
                    self.last_msg_time = None
                    print(f"IGNITION: OFF at {time.strftime('%Y-%m-%d %H:%M:%S')} (timeout)")
            await asyncio.sleep(1)

    # ----------------------------
    # Start MQTT client and monitor
    # ----------------------------
    def start(self):
        if not all([self.mqtt_broker, self.mqtt_port, self.mqtt_topic]):
            raise ValueError("Missing MQTT configuration")

        # Start OFF/timeout monitor as async task
        threading.Thread(target=lambda: self.loop.run_until_complete(self._monitor_ignition()), daemon=True).start()

        # Start MQTT loop (blocking)
        self.client.connect(self.mqtt_broker, self.mqtt_port, 60)
        self.client.loop_forever()


# ----------------------------
# Entry point
# ----------------------------
def main():
    monitor = IgnitionMonitor()
    monitor.start()


if __name__ == "__main__":
    main()
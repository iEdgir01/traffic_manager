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

        # Async queue for ignition events
        self.queue = asyncio.Queue()
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
            self.last_msg_time = time.time()

            if ignition_on:
                # Put ignition event into queue
                if self.loop.is_running():
                    asyncio.run_coroutine_threadsafe(self.queue.put(time.time()), self.loop)
        except json.JSONDecodeError as e:
            print(f"ERROR: Failed to decode JSON message: {msg.payload} - {e}")
        except Exception as e:
            print(f"ERROR: Processing message failed: {e}")

    # ----------------------------
    # Process ignition events
    # ----------------------------
    async def _process_queue(self):
        while True:
            ignition_time = await self.queue.get()
            # Only process if within 5 minutes of ignition event
            if time.time() - ignition_time <= 300:
                print(f"IGNITION: ON at {time.strftime('%Y-%m-%d %H:%M:%S')}")
                try:
                    await post_traffic_alerts_async()
                except Exception as e:
                    print(f"ERROR: Traffic processing failed: {e}")
            else:
                print("INFO: Ignition ON too old, skipping traffic processing")
            self.queue.task_done()

    # ----------------------------
    # Monitor ignition OFF / timeout
    # ----------------------------
    def _monitor_ignition(self):
        while True:
            if self.last_msg_time and (time.time() - self.last_msg_time > self.timeout):
                self.ignition_state = False
                print(f"IGNITION: OFF at {time.strftime('%Y-%m-%d %H:%M:%S')} (timeout)")
                self.last_msg_time = None
                self.ignition_on_time = None
            time.sleep(2)

    # ----------------------------
    # Start MQTT client and monitor thread
    # ----------------------------
    def start(self):
        if not all([self.mqtt_broker, self.mqtt_port, self.mqtt_topic]):
            raise ValueError("Missing MQTT configuration")

        # Start queue processor
        threading.Thread(target=lambda: self.loop.run_until_complete(self._process_queue()), daemon=True).start()
        # Start OFF monitor
        threading.Thread(target=self._monitor_ignition, daemon=True).start()
        # Start MQTT loop
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
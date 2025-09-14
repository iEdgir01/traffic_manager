import os
import json
import time
import threading
import paho.mqtt.client as mqtt
from discord_bot.discord_notify import post_traffic_alerts

class IgnitionMonitor:
    def __init__(self):
        self.mqtt_broker = os.getenv("MQTT_BROKER")
        self.mqtt_port = int(os.getenv("MQTT_PORT"))
        self.mqtt_topic = os.getenv("MQTT_TOPIC")
        self.ignition_state = False
        self.last_msg_time = None
        self.timeout = int(os.getenv("IGNITION_TIMEOUT", 60))

        self.client = mqtt.Client()
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

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

            if ignition_on and not self.ignition_state:
                self._handle_ignition_on()
        except json.JSONDecodeError as e:
            print(f"ERROR: Failed to decode JSON message: {msg.payload} - {e}")
        except Exception as e:
            print(f"ERROR: Processing message failed: {e}")

    def _handle_ignition_on(self):
        self.ignition_state = True
        print(f"IGNITION: ON at {time.strftime('%Y-%m-%d %H:%M:%S')}")
        try:
            print("INFO: Traffic processing started")
            post_traffic_alerts()
        except Exception as e:
            print(f"ERROR: Traffic processing failed: {e}")


    def _monitor_ignition(self):
        while True:
            if self.ignition_state and self.last_msg_time:
                if time.time() - self.last_msg_time > self.timeout:
                    self.ignition_state = False
                    print(f"IGNITION: OFF at {time.strftime('%Y-%m-%d %H:%M:%S')} (timeout)")
                    self.last_msg_time = None
            time.sleep(2)

    def start(self):
        if not all([self.mqtt_broker, self.mqtt_port, self.mqtt_topic]):
            raise ValueError("Missing MQTT configuration")
        self.client.connect(self.mqtt_broker, self.mqtt_port, 60)
        threading.Thread(target=self._monitor_ignition, daemon=True).start()
        self.client.loop_forever()


def main():
    monitor = IgnitionMonitor()
    monitor.start()


if __name__ == "__main__":
    main()
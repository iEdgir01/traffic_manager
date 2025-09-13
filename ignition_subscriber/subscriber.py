import os
import json
import time
import threading
import paho.mqtt.client as mqtt
from traffic_utils import process_all_routes_for_discord
from discord_bot.discord_notify import post_traffic_alerts

class IgnitionMonitor:
    def __init__(self):
        # MQTT Configuration
        self.mqtt_broker = os.getenv("MQTT_BROKER")
        self.mqtt_port = int(os.getenv("MQTT_PORT"))
        self.mqtt_topic = os.getenv("MQTT_TOPIC")
        
        # State tracking
        self.ignition_state = False
        self.last_msg_time = None
        self.timeout = int(os.getenv("IGNITION_TIMEOUT"))  # Default to 60 seconds if not set
        
        # Setup MQTT client
        self.client = mqtt.Client()
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
    
    def on_connect(self, client, userdata, flags, rc):
        """Callback for MQTT connection"""
        if rc == 0:
            print(f"Connected to MQTT broker {self.mqtt_broker}:{self.mqtt_port}")
            client.subscribe(self.mqtt_topic)
            print(f"Subscribed to topic: {self.mqtt_topic}")
        else:
            print(f"Failed to connect to MQTT broker. Return code: {rc}")
    
    def on_message(self, client, userdata, msg):
        """Callback for MQTT messages"""
        try:
            payload = json.loads(msg.payload.decode())
            ignition_on = payload.get("Ignition On", False)
            
            current_time = time.time()
            self.last_msg_time = current_time
            
            # Handle ignition state change
            if ignition_on and not self.ignition_state:
                self._handle_ignition_on(payload, msg.topic)
                
        except json.JSONDecodeError as e:
            print(f"❌ Failed to decode JSON message: {msg.payload} - Error: {e}")
        except Exception as e:
            print(f"❌ Error processing message: {e}")
    
    def _handle_ignition_on(self, payload, topic):
        """Handle ignition turning on"""
        self.ignition_state = True
        timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
        print(f"🔥 Ignition ON detected at {timestamp}")
        print(f"📩 Topic: {topic}")
        print(f"💾 Payload: {json.dumps(payload, indent=2)}")
        print(f"⏱️  Timeout: {self.timeout}s")
        
        try:
            results = process_all_routes_for_discord()
            post_traffic_alerts(results)
            print("📤 Traffic alerts sent successfully")
        except Exception as e:
            print(f"❌ Error sending traffic alerts: {e}")
    
    def _monitor_ignition(self):
        """Monitor ignition state and handle timeout"""
        while True:
            if self.ignition_state and self.last_msg_time:
                time_since_last = time.time() - self.last_msg_time
                
                if time_since_last > self.timeout:
                    self.ignition_state = False
                    timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
                    print(f"💤 Ignition OFF detected at {timestamp} "
                          f"(timeout: {self.timeout}s)")
                    self.last_msg_time = None
            
            time.sleep(2)  # Check every 2 seconds
    
    def start(self):
        """Start the MQTT client and monitoring"""
        try:
            # Validate configuration
            if not all([self.mqtt_broker, self.mqtt_port, self.mqtt_topic]):
                raise ValueError("Missing MQTT configuration. Check environment variables.")
            
            print(f"🚀 Starting Ignition Monitor...")
            print(f"📡 Broker: {self.mqtt_broker}:{self.mqtt_port}")
            print(f"📋 Topic: {self.mqtt_topic}")
            print(f"⏱️  Static timeout: {self.timeout}s")
            
            # Connect to MQTT broker
            self.client.connect(self.mqtt_broker, self.mqtt_port, 60)
            
            # Start monitoring thread
            monitor_thread = threading.Thread(target=self._monitor_ignition, daemon=True)
            monitor_thread.start()
            print("🔄 Monitoring thread started")
            
            # Start MQTT loop
            print("🔄 Starting MQTT loop...")
            self.client.loop_forever()
            
        except KeyboardInterrupt:
            print("\n⏹️  Shutting down gracefully...")
            self.client.disconnect()
        except Exception as e:
            print(f"❌ Fatal error: {e}")
            raise


def main():
    """Main entry point"""
    monitor = IgnitionMonitor()
    monitor.start()


if __name__ == "__main__":
    main()
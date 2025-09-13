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
            print(f"MQTT: Connected to {self.mqtt_broker}:{self.mqtt_port}")
            client.subscribe(self.mqtt_topic)
            print(f"MQTT: Subscribed to {self.mqtt_topic}")
        else:
            print(f"MQTT: Connection failed with code {rc}")
    
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
            print(f"ERROR: Failed to decode JSON message: {msg.payload} - {e}")
        except Exception as e:
            print(f"ERROR: Processing message failed: {e}")
    
    def _handle_ignition_on(self, payload, topic):
        """Handle ignition turning on"""
        self.ignition_state = True
        timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
        print(f"IGNITION: ON at {timestamp}")
        
        try:
            results = process_all_routes_for_discord()
            
            # Check if we actually have results to send
            if not results or len(results) == 0:
                print("TRAFFIC: No routes or traffic data available")
                return
                
            # Check if results contain actual traffic data
            has_traffic_data = any(
                route.get('traffic_alerts') or route.get('incidents') 
                for route in results if isinstance(route, dict)
            )
            
            if not has_traffic_data:
                print("TRAFFIC: No traffic alerts to send")
                return
            
            post_traffic_alerts(results)
            print("TRAFFIC: Alerts sent successfully")
            
        except Exception as e:
            print(f"ERROR: Traffic processing failed: {e}")
    
    def _monitor_ignition(self):
        """Monitor ignition state and handle timeout"""
        while True:
            if self.ignition_state and self.last_msg_time:
                time_since_last = time.time() - self.last_msg_time
                
                if time_since_last > self.timeout:
                    self.ignition_state = False
                    timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
                    print(f"IGNITION: OFF at {timestamp} (timeout: {self.timeout}s)")
                    self.last_msg_time = None
            
            time.sleep(2)  # Check every 2 seconds
    
    def start(self):
        """Start the MQTT client and monitoring"""
        try:
            # Validate configuration
            if not all([self.mqtt_broker, self.mqtt_port, self.mqtt_topic]):
                raise ValueError("Missing MQTT configuration. Check environment variables.")
            
            print("MONITOR: Starting ignition monitor")
            print(f"BROKER: {self.mqtt_broker}:{self.mqtt_port}")
            print(f"TOPIC: {self.mqtt_topic}")
            print(f"TIMEOUT: {self.timeout}s")
            
            # Connect to MQTT broker
            self.client.connect(self.mqtt_broker, self.mqtt_port, 60)
            
            # Start monitoring thread
            monitor_thread = threading.Thread(target=self._monitor_ignition, daemon=True)
            monitor_thread.start()
            print("MONITOR: Thread started")
            
            # Start MQTT loop
            print("MQTT: Starting message loop")
            self.client.loop_forever()
            
        except KeyboardInterrupt:
            print("SHUTDOWN: Stopping gracefully...")
            self.client.disconnect()
        except Exception as e:
            print(f"FATAL: {e}")
            raise


def main():
    """Main entry point"""
    monitor = IgnitionMonitor()
    monitor.start()


if __name__ == "__main__":
    main()
import os
import json
import time
import threading
import paho.mqtt.client as mqtt

MQTT_BROKER = os.getenv("MQTT_BROKER")
MQTT_PORT = int(os.getenv("MQTT_PORT"))
MQTT_TOPIC = os.getenv("MQTT_TOPIC")

# State tracking
ignition_state = False
last_msg_time = None
timeout = 10  # seconds without messages -> ignition off

def on_connect(client, userdata, flags, rc):
    print(f"Connected to MQTT broker {MQTT_BROKER}:{MQTT_PORT} with result code {rc}")
    client.subscribe(MQTT_TOPIC)

def on_message(client, userdata, msg):
    global ignition_state, last_msg_time
    try:
        payload = json.loads(msg.payload.decode())
        ignition_on = payload.get("Ignition On", False)
        
        current_time = time.time()
        last_msg_time = current_time

        if ignition_on and not ignition_state:
            ignition_state = True
            print(f"🔥 Ignition On detected at {time.strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"📩 {msg.topic}: {json.dumps(payload)}")
        # else: ignition already on, do nothing

    except json.JSONDecodeError:
        print(f"❌ Failed to decode message: {msg.payload}")

def monitor_ignition():
    global ignition_state, last_msg_time
    while True:
        if ignition_state and last_msg_time:
            if time.time() - last_msg_time > timeout:
                ignition_state = False
                print(f"💤 Ignition Off detected at {time.strftime('%Y-%m-%d %H:%M:%S')}")
                last_msg_time = None
        time.sleep(1)

# MQTT setup
client = mqtt.Client()
client.on_connect = on_connect
client.on_message = on_message

client.connect(MQTT_BROKER, MQTT_PORT, 60)

# Start monitoring thread
threading.Thread(target=monitor_ignition, daemon=True).start()

client.loop_forever()
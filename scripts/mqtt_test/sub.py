import paho.mqtt.client as mqtt

def on_message(client, userdata, msg):
    print(f"Received message: {msg.payload.decode()}")

client = mqtt.Client()
client.on_message = on_message
client.connect("mqtt.fixetics.co.za", 1883, 60)
client.subscribe("test")
client.loop_forever()
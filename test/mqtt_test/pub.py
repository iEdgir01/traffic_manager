import paho.mqtt.publish as publish

publish.single("test", "Hello world from Python!", hostname="mqtt.fixetics.co.za", port=1883)
print("Message sent!")

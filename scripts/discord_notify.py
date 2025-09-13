import os
import requests

WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

def send_alert(message: str):
    if not WEBHOOK_URL:
        print("⚠️ No Discord webhook URL set")
        return
    payload = {"content": message}
    response = requests.post(WEBHOOK_URL, json=payload)
    if response.status_code == 204:
        print(f"✅ Discord alert sent: {message}")
    else:
        print(f"⚠️ Failed to send Discord alert: {response.status_code}")

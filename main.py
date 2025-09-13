import threading
import asyncio
from ignition_subscriber.subscriber import IgnitionMonitor
from discord_bot.traffic_helper import run_discord_bot

def start_ignition_monitor():
    monitor = IgnitionMonitor()
    monitor.start()  # blocking call, runs forever

def start_discord_bot():
    asyncio.run(run_discord_bot())  # async bot

if __name__ == "__main__":
    # Start MQTT ignition monitor in a thread
    threading.Thread(target=start_ignition_monitor, daemon=True).start()
    print("🔥 Ignition monitor started in background thread")

    # Start Discord bot (blocking)
    print("🤖 Starting Discord bot...")
    start_discord_bot()

import threading
import asyncio
import logging
import time
import sys
import signal

from ignition_subscriber.subscriber import IgnitionMonitor
from discord_bot.traffic_helper import run_discord_bot, hard_shutdown, sync_cleanup

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# ----------------------------
# Ignition monitor
# ----------------------------
def start_ignition_monitor():
    """Start MQTT ignition monitor with restart capability"""
    max_restarts = 10
    restart_count = 0

    while restart_count < max_restarts:
        try:
            logger.info(f"Starting ignition monitor (attempt {restart_count + 1})")
            monitor = IgnitionMonitor()
            monitor.start()  # blocking call
        except Exception as e:
            restart_count += 1
            logger.error(f"Ignition monitor crashed (attempt {restart_count}/{max_restarts}): {e}")

            if restart_count < max_restarts:
                logger.info("Restarting ignition monitor in 10 seconds...")
                time.sleep(10)
            else:
                logger.error("Max restart attempts reached for ignition monitor")
                break


# ----------------------------
# Discord bot runner
# ----------------------------
async def start_discord_bot():
    """Run Discord bot with internal restart capability"""
    try:
        await run_discord_bot()  # your restart logic lives inside this
    finally:
        await hard_shutdown()


# ----------------------------
# Main entrypoint
# ----------------------------
def main():
    try:
        # Start ignition monitor in background thread
        ignition_thread = threading.Thread(
            target=start_ignition_monitor,
            daemon=True,  # or False if you want controlled shutdown
            name="IgnitionMonitor"
        )
        ignition_thread.start()
        logger.info("🔥 Ignition monitor started in background thread")

        # Hook up signal handlers for Docker stop / Ctrl+C
        signal.signal(signal.SIGTERM, lambda s, f: sync_cleanup())
        signal.signal(signal.SIGINT, lambda s, f: sync_cleanup())

        # Run Discord bot (restart-safe) in main thread
        logger.info("🤖 Starting Discord bot...")
        asyncio.run(start_discord_bot())

    except KeyboardInterrupt:
        logger.info("Application manually stopped")
    except Exception as e:
        logger.error(f"Application crashed: {e}")
        sys.exit(1)
    finally:
        logger.info("Application shutting down")
        sync_cleanup()


if __name__ == "__main__":
    main()
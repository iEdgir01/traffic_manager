import threading
import asyncio
import logging
import time
import sys
import signal
import atexit
from ignition_subscriber.subscriber import IgnitionMonitor
from discord_bot.traffic_helper import run_discord_bot, force_permanent_shutdown, sync_cleanup

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Global flags
_force_shutdown = False  # Only True when we REALLY want to stop
_restart_requested = False

# ----------------------------
# Ignition monitor
# ----------------------------
def start_ignition_monitor():
    """Start MQTT ignition monitor with restart capability"""
    max_restarts = 10
    restart_count = 0
    
    while restart_count < max_restarts and not _force_shutdown:
        try:
            logger.info(f"Starting ignition monitor (attempt {restart_count + 1})")
            monitor = IgnitionMonitor()
            monitor.start()  # blocking call
            
        except Exception as e:
            restart_count += 1
            logger.error(f"Ignition monitor crashed (attempt {restart_count}/{max_restarts}): {e}")
            
            if restart_count < max_restarts and not _force_shutdown:
                logger.info("Restarting ignition monitor in 10 seconds...")
                time.sleep(10)
            else:
                logger.error("Max restart attempts reached for ignition monitor")
                break
    
    logger.info("Ignition monitor thread ending")

# ----------------------------
# Discord bot infinite runner
# ----------------------------
async def run_discord_bot_infinite():
    """Bot runner that never gives up (unless forced to)"""
    global _force_shutdown
    
    restart_count = 0
    consecutive_failures = 0
    max_consecutive_failures = 10  # Backoff after this many failures
    
    while not _force_shutdown:
        try:
            restart_count += 1
            logger.info(f"Starting Discord bot (outer attempt {restart_count})")
            
            # Reset failure count on successful start
            consecutive_failures = 0
            
            # Run the bot (this will restart internally up to 5 times)
            await run_discord_bot()
            
            # If we get here, bot shut down normally
            logger.info("Bot shut down normally - will restart")
            
        except Exception as e:
            consecutive_failures += 1
            logger.error(f"Bot failed to start (failure {consecutive_failures}): {e}")
            
            if consecutive_failures >= max_consecutive_failures:
                # Exponential backoff for persistent failures
                backoff_time = min(300, 10 * (2 ** (consecutive_failures - max_consecutive_failures)))
                logger.warning(f"Too many consecutive failures, backing off for {backoff_time} seconds")
                await asyncio.sleep(backoff_time)
            else:
                # Short delay for normal failures
                await asyncio.sleep(30)
        
        # Check if we should continue
        if not _force_shutdown:
            logger.info("Restarting bot in 10 seconds...")
            await asyncio.sleep(10)
    
    logger.info("Bot runner shutting down permanently")

# ----------------------------
# Signal handlers
# ----------------------------
def signal_handler(signum, frame):
    """Handle shutdown signals - but allow for graceful restart"""
    global _force_shutdown, _restart_requested
    
    logger.info(f"Received signal {signum}")
    
    if signum == signal.SIGTERM:
        # Docker stop or system shutdown - force shutdown
        _force_shutdown = True
        force_permanent_shutdown()  # Tell the bot to shutdown permanently too
        logger.info("SIGTERM received - forcing permanent shutdown")
    elif signum == signal.SIGINT:
        # Ctrl+C - check if this is the first or second time
        if _restart_requested:
            _force_shutdown = True
            force_permanent_shutdown()  # Tell the bot to shutdown permanently too
            logger.info("Second SIGINT - forcing permanent shutdown")
        else:
            _restart_requested = True
            sync_cleanup()  # Just restart the bot
            logger.info("First SIGINT - restarting bot (press Ctrl+C again to force quit)")
            return  # Don't exit, let it restart
    
    sys.exit(0)

def cleanup_on_exit():
    """Cleanup function for atexit"""
    global _force_shutdown
    
    if not _force_shutdown:
        _force_shutdown = True
        logger.info("Exit cleanup triggered - forcing permanent shutdown")
        force_permanent_shutdown()

# ----------------------------
# Discord bot wrapper
# ----------------------------
async def start_discord_bot():
    """Run Discord bot with infinite restart capability"""
    try:
        await run_discord_bot_infinite()
    except Exception as e:
        logger.error(f"Discord bot infinite runner failed: {e}")

# ----------------------------
# Main entrypoint
# ----------------------------
def main():
    global _force_shutdown
    
    # Register cleanup handlers
    atexit.register(cleanup_on_exit)
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    ignition_thread = None
    
    try:
        # Start ignition monitor in background thread
        ignition_thread = threading.Thread(
            target=start_ignition_monitor,
            daemon=True,  # Let it die when main thread exits
            name="IgnitionMonitor"
        )
        ignition_thread.start()
        logger.info("Ignition monitor started in background thread")
        
        # Run Discord bot with infinite restart in main thread
        logger.info("Starting always-online Discord bot...")
        asyncio.run(start_discord_bot())
        
    except KeyboardInterrupt:
        logger.info("Application manually stopped (KeyboardInterrupt)")
        _force_shutdown = True
        force_permanent_shutdown()
        
    except SystemExit as e:
        if e.code == 0:
            # Normal exit from signal handler
            pass
        else:
            logger.info("System exit with restart code")
            # This was from first Ctrl+C, don't force shutdown
        
    except Exception as e:
        logger.error(f"Application crashed: {e}")
        _force_shutdown = True
        force_permanent_shutdown()
        
    finally:
        logger.info("Main function cleanup starting")
        
        # Ensure shutdown flags are set
        _force_shutdown = True
        force_permanent_shutdown()
        
        # Wait for ignition thread to finish if it's still alive
        if ignition_thread and ignition_thread.is_alive():
            logger.info("Waiting for ignition monitor to finish...")
            ignition_thread.join(timeout=5)
            if ignition_thread.is_alive():
                logger.warning("Ignition monitor did not shut down gracefully")
        
        logger.info("Application shutdown complete")

if __name__ == "__main__":
    main()
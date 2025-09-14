import threading
import asyncio
import logging
import time
import sys
import signal
import atexit
from ignition_subscriber.subscriber import IgnitionMonitor
from discord_bot.traffic_helper import run_discord_bot, force_permanent_shutdown

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Global flags
_force_shutdown = False  # Only True when we REALLY want to stop

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
    """Bot runner that handles restarts more gracefully"""
    global _force_shutdown
    
    restart_count = 0
    consecutive_failures = 0
    max_consecutive_failures = 5
    
    while not _force_shutdown:
        try:
            restart_count += 1
            logger.info(f"Starting Discord bot (outer attempt {restart_count})")
            
            # Reset failure count on successful start attempt
            consecutive_failures = 0
            
            # Run the bot (this will handle its own internal restarts)
            await run_discord_bot()
            
            # If we get here, check why the bot stopped
            if _force_shutdown:
                logger.info("Bot shut down due to force shutdown flag")
                break
            else:
                logger.info("Bot shut down normally - will restart")
            
        except asyncio.CancelledError:
            logger.info("Bot runner was cancelled")
            break
            
        except Exception as e:
            consecutive_failures += 1
            logger.error(f"Bot runner failed (failure {consecutive_failures}): {e}")
            
            if consecutive_failures >= max_consecutive_failures:
                # Exponential backoff for persistent failures
                backoff_time = min(300, 30 * (2 ** (consecutive_failures - max_consecutive_failures)))
                logger.warning(f"Too many consecutive failures, backing off for {backoff_time} seconds")
                await asyncio.sleep(backoff_time)
            else:
                # Short delay for normal failures
                await asyncio.sleep(30)
        
        # Check if we should continue
        if not _force_shutdown:
            logger.info("Restarting bot in 15 seconds...")
            await asyncio.sleep(15)
    
    logger.info("Bot runner shutting down permanently")

# ----------------------------
# Signal handlers
# ----------------------------
def signal_handler(signum, frame):
    """Handle shutdown signals with immediate response"""
    global _force_shutdown
    
    logger.info(f"Received signal {signum}")
    
    if signum == signal.SIGTERM:
        # Docker stop or system shutdown - force shutdown immediately
        _force_shutdown = True
        force_permanent_shutdown()
        logger.info("SIGTERM received - forcing permanent shutdown")
        sys.exit(0)
        
    elif signum == signal.SIGINT:
        # Ctrl+C - force shutdown immediately (no restart logic needed)
        _force_shutdown = True
        force_permanent_shutdown()
        logger.info("SIGINT received - forcing permanent shutdown")
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
        
    except SystemExit:
        # Normal exit from signal handler - don't log as error
        logger.info("Application shutting down via system exit")
        
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
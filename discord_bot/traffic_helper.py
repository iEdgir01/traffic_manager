import os
import re
import sys
import math
import json
import signal
import atexit
import asyncio
import logging
import threading
import contextlib
from io import BytesIO
from pathlib import Path
import concurrent.futures
from datetime import datetime, timezone, timezone
from PIL import Image, ImageDraw, ImageFont
import discord
from discord.ext import commands
from discord.ui import View, Button, Select, Modal, TextInput
from discord import SelectOption, File, Embed
from traffic_utils import (
    init_db,
    get_route_map,
    get_routes,
    check_route_traffic,
    update_route_time,
    summarize_segments,
    calculate_baseline,
    get_thresholds,
    set_thresholds,
    reset_thresholds,
    add_route,
    delete_route
)
# ---------------------
# logging
# ---------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/app/data/discord_bot.log") if os.path.exists("/app/data") else logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Configure Discord logging
discord_logger = logging.getLogger('discord')
discord_logger.setLevel(logging.INFO)

# Add startup message
print("Discord Bot starting...")
logger.info("Discord Bot module loaded")

# ---------------------
# Docker environment variables
# ---------------------
TOKEN = os.environ["DISCORD_BOT_TOKEN"]
DATA_DIR = Path(os.environ["DATA_DIR"])
MAP_DIR = Path(os.environ["MAPS_DIR"])

# Ensure directories exist
DATA_DIR.mkdir(parents=True, exist_ok=True)
MAP_DIR.mkdir(parents=True, exist_ok=True)

# Thread pool for blocking operations - make it recreatable
bot = None
thread_pool = None
_thread_pool_lock = threading.Lock()
_shutting_down = False
_force_permanent_shutdown = False

# =========================
# Thread pool management
# =========================
def create_thread_pool():
    """Create or recreate the thread pool with logging"""
    global thread_pool
    if thread_pool and not thread_pool._shutdown:
        logging.debug("Thread pool already exists and is active")
        return thread_pool
    
    logging.info("Creating new thread pool with 4 workers")
    thread_pool = concurrent.futures.ThreadPoolExecutor(max_workers=4)
    return thread_pool

def ensure_thread_pool():
    """Ensure thread pool exists and is not shut down with logging"""
    global thread_pool
    
    if thread_pool is None:
        logging.info("Thread pool is None, creating new one")
        thread_pool = create_thread_pool()
    elif thread_pool._shutdown:
        logging.warning("Thread pool was shut down, recreating")
        thread_pool = create_thread_pool()
    else:
        logging.debug("Thread pool is healthy")
    
    return thread_pool

def shutdown_thread_pool(wait=False):
    """Shutdown the thread pool if active with logging"""
    global thread_pool
    if thread_pool and not thread_pool._shutdown:
        try:
            logging.info(f"Shutting down thread pool (wait={wait})")
            thread_pool.shutdown(wait=wait, cancel_futures=True)
            logging.info("Thread pool shutdown complete")
        except TypeError:
            # Python < 3.9 doesn't support cancel_futures
            logging.info("Using legacy thread pool shutdown (no cancel_futures)")
            thread_pool.shutdown(wait=wait)
        except Exception as e:
            logging.error(f"Error during thread pool shutdown: {e}")
        finally:
            thread_pool = None
    else:
        logging.debug("Thread pool already shut down or None")

# =========================
# Bot management
# =========================
def create_bot_instance():
    """Factory for creating a fresh bot instance"""
    intents = discord.Intents.default()
    intents.message_content = True

    bot = commands.Bot(command_prefix="!", intents=intents)

    @bot.event
    async def on_ready():
        logging.info(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")

    @bot.command()
    async def ping(ctx):
        await ctx.send("Pong!")

    return bot

# Standardized colors and styles
class BotStyles:
    # Button styles
    PRIMARY = discord.ButtonStyle.primary      # Blue for main actions
    SECONDARY = discord.ButtonStyle.secondary  # Gray for navigation
    SUCCESS = discord.ButtonStyle.success      # Green for positive actions
    DANGER = discord.ButtonStyle.danger        # Red for destructive actions
    
    # Embed colors
    PRIMARY_COLOR = discord.Color.blue()       # Main embed color
    SUCCESS_COLOR = discord.Color.green()      # Success operations
    WARNING_COLOR = discord.Color.orange()     # Warnings
    ERROR_COLOR = discord.Color.red()          # Errors
    INFO_COLOR = discord.Color.blurple()       # Info/neutral
    LOADING_COLOR = discord.Color.yellow()     # Loading states

class ConnectionHealthMonitor:
    def __init__(self):
        self.last_heartbeat = None
        self.connection_failures = 0
        self.last_connection_attempt = None
    
    def record_heartbeat(self):
        self.last_heartbeat = datetime.now(timezone.utc)
        self.connection_failures = 0
    
    def record_failure(self):
        self.connection_failures += 1
        self.last_connection_attempt = datetime.now(timezone.utc)
    
    def is_healthy(self) -> bool:
        if not self.last_heartbeat:
            return False
        
        # Consider unhealthy if no heartbeat in 5 minutes
        time_since_heartbeat = (datetime.now(timezone.utc) - self.last_heartbeat).total_seconds()
        return time_since_heartbeat < 300
    
    def should_attempt_reconnection(self) -> bool:
        if not self.last_connection_attempt:
            return True
        
        # Wait longer between attempts based on failure count
        wait_time = min(300, 30 * self.connection_failures)  # Max 5 minutes
        time_since_attempt = (datetime.now(timezone.utc) - self.last_connection_attempt).total_seconds()
        return time_since_attempt >= wait_time

health_monitor = ConnectionHealthMonitor()

class BotResourceManager:
    """Async context manager for bot resources"""
    
    def __init__(self):
        self.bot_instance = None
        self.thread_pool_created = False
    
    async def __aenter__(self):
        logging.info("Entering bot resource context")
        # Ensure thread pool exists
        ensure_thread_pool()
        self.thread_pool_created = True
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        logging.info(f"Exiting bot resource context (exception: {exc_type.__name__ if exc_type else 'None'})")
        
        # Clean up bot
        if self.bot_instance and not self.bot_instance.is_closed():
            try:
                await asyncio.wait_for(self.bot_instance.close(), timeout=10.0)
                logging.info("Bot closed successfully in context manager")
            except Exception as e:
                logging.error(f"Error closing bot in context manager: {e}")
        
        # Clean up thread pool
        if self.thread_pool_created:
            shutdown_thread_pool(wait=False)
        
        logging.info("Bot resource cleanup complete")
    
    def set_bot(self, bot_instance):
        self.bot_instance = bot_instance

@contextlib.asynccontextmanager
async def bot_session():
    """Simple async context manager for bot sessions"""
    resource_manager = BotResourceManager()
    try:
        async with resource_manager:
            yield resource_manager
    except Exception as e:
        logging.error(f"Exception in bot session context: {e}")
        raise

# ---------------------
# Async helpers for blocking operations
# ---------------------
async def run_in_thread(func, *args, **kwargs):
    """Run blocking function in thread pool"""
    if _shutting_down:
        raise RuntimeError("Bot is shutting down")
    
    loop = asyncio.get_event_loop()
    if loop.is_closed():
        raise RuntimeError("Event loop is closed")
    
    # Ensure we have a working thread pool
    pool = ensure_thread_pool()
    
    try:
        return await loop.run_in_executor(pool, func, *args, **kwargs)
    except RuntimeError as e:
        if "cannot schedule new futures after shutdown" in str(e):
            # Thread pool was shut down, try to recreate it
            logging.warning("Thread pool was shut down, recreating...")
            pool = create_thread_pool()
            return await loop.run_in_executor(pool, func, *args, **kwargs)
        raise

async def hard_shutdown():
    """Hard shutdown for permanent termination"""
    global _shutting_down, _force_permanent_shutdown, bot
    
    _shutting_down = True
    _force_permanent_shutdown = True
    logging.info("Hard shutdown initiated (permanent)")
    
    await soft_cleanup()
    logging.info("Hard shutdown complete")

# Error recovery wrapper
async def with_error_recovery(coro_func, *args, **kwargs):
    """Wrapper to handle errors and attempt recovery"""
    max_retries = 2
    
    for attempt in range(max_retries + 1):
        try:
            return await coro_func(*args, **kwargs)
        except RuntimeError as e:
            if "shutdown" in str(e).lower() or "closed" in str(e).lower():
                if attempt < max_retries:
                    logging.warning(f"Attempt {attempt + 1} failed, retrying after recovery...")
                    await asyncio.sleep(1)  # Brief pause
                    continue
                else:
                    logging.error("Max retries reached, operation failed")
                    raise
            else:
                raise
        except Exception as e:
            if attempt < max_retries:
                logging.warning(f"Attempt {attempt + 1} failed with {type(e).__name__}: {e}, retrying...")
                await asyncio.sleep(1)
                continue
            else:
                raise

# Updated async wrappers with error recovery
async def async_get_routes():
    """Async wrapper for get_routes with error recovery"""
    try:
        result = await with_error_recovery(lambda: run_in_thread(get_routes))
        return result
    except Exception as e:
        raise

async def async_add_route(name, start_lat, start_lng, end_lat, end_lng):
    """Async wrapper for adding route with error recovery"""
    return await with_error_recovery(
        lambda: run_in_thread(register_route_and_generate_map, name, start_lat, start_lng, end_lat, end_lng)
    )

async def async_delete_route(name):
    """Async wrapper for deleting route with error recovery"""
    return await with_error_recovery(lambda: run_in_thread(delete_route, name))

async def async_get_route_map(name, start_lat, start_lng, end_lat, end_lng):
    """Async wrapper for getting route map with error recovery"""
    return await with_error_recovery(
        lambda: run_in_thread(get_route_map, name, start_lat, start_lng, end_lat, end_lng)
    )

async def async_check_traffic(start_coord, end_coord, baseline=None):
    """Async wrapper for traffic checking with error recovery"""
    try:
        result = await with_error_recovery(
            lambda: run_in_thread(check_route_traffic, start_coord, end_coord, baseline)
        )
        return result
    except Exception as e:
        raise

async def async_get_thresholds():
    """Async wrapper for get_thresholds with error recovery"""
    return await with_error_recovery(lambda: run_in_thread(get_thresholds))

async def async_set_thresholds(thresholds):
    """Async wrapper for set_thresholds with error recovery"""
    return await with_error_recovery(lambda: run_in_thread(set_thresholds, thresholds))

async def async_reset_thresholds():
    """Async wrapper for reset_thresholds with error recovery"""
    return await with_error_recovery(lambda: run_in_thread(reset_thresholds))

# ---------------------
# Loading state helpers
# ---------------------
def create_loading_embed(title: str, description: str = "Please wait...") -> discord.Embed:
    """Create a standardized loading embed"""
    embed = discord.Embed(
        title=f"⏳ {title}",
        description=description,
        color=BotStyles.LOADING_COLOR,
        timestamp=datetime.now(timezone.utc)
    )
    return embed

async def show_loading_state(interaction: discord.Interaction, title: str, description: str = "Please wait..."):
    """Show loading state to user"""
    if _shutting_down:
        return
    
    try:
        embed = create_loading_embed(title, description)
        if interaction.response.is_done():
            await interaction.edit_original_response(embed=embed, view=None, attachments=[])
        else:
            await interaction.response.edit_message(embed=embed, view=None, attachments=[])
    except (discord.NotFound, discord.HTTPException, RuntimeError):
        # Interaction expired or bot is shutting down
        pass

# --------------------
# DMS parsing helpers (keeping existing)
# --------------------
def dms_to_decimal(dms_str: str) -> float:
    """Convert a single DMS coordinate to decimal degrees."""
    dms_str = dms_str.strip()
    match = re.fullmatch(
        r'(\d{1,3})°(\d{1,2})\'(\d{1,2}(?:\.\d+)?)\"([NSEW])',
        dms_str, re.IGNORECASE
    )
    if not match:
        raise ValueError(f"Invalid DMS format: {dms_str!r}")
    
    deg, minute, sec, direction = match.groups()
    dec = float(deg) + float(minute)/60 + float(sec)/3600
    if direction.upper() in ("S", "W"):
        dec = -dec
    return dec

def parse_dms_pair(dms_pair: str):
    """Parse a lat/lng pair separated by whitespace."""
    parts = dms_pair.strip().split()
    if len(parts) != 2:
        raise ValueError(f"Invalid coordinate pair: {dms_pair!r}")
    
    lat = dms_to_decimal(parts[0])
    lng = dms_to_decimal(parts[1])
    return lat, lng

# --------------------
# Haversine distance (keeping existing)
# --------------------
def haversine_distance(lat1, lon1, lat2, lon2):
    R = 6371  # Earth radius in km
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

# --------------------
# Blocking processing
# --------------------
def register_route_and_generate_map(name, start_lat, start_lng, end_lat, end_lng):
    try:
        add_route(name, start_lat, start_lng, end_lat, end_lng)
    except Exception as e:
        raise e

    # Generate map
    map_path = get_route_map(name, start_lat, start_lng, end_lat, end_lng)
    return map_path

# =========================
# Signal handling
# =========================
def sync_cleanup():
    """Synchronous cleanup - now just sets flags appropriately"""
    global _shutting_down
    
    logging.info("Sync cleanup called")
    
    # For sync cleanup, we assume this is temporary unless explicitly permanent
    _shutting_down = True
    
    try:
        # Try to run async cleanup if possible
        try:
            loop = asyncio.get_running_loop()
            if loop and not loop.is_closed():
                # Create task for soft cleanup (not hard shutdown)
                asyncio.create_task(soft_cleanup())
        except RuntimeError:
            # No running loop, just shut down thread pool
            shutdown_thread_pool(wait=False)
            
    except Exception as e:
        logging.error(f"Error during sync cleanup: {e}")
        shutdown_thread_pool(wait=False)

async def cleanup_for_restart():
    """Clean up resources but keep the bot restartable"""
    global bot
    
    try:
        # Shutdown thread pool
        shutdown_thread_pool(wait=False)
        
        # Close bot if needed
        if bot and not bot.is_closed():
            await bot.close()
        bot = None
        
        # Recreate thread pool for next attempt
        create_thread_pool()
        
    except Exception as e:
        logging.error(f"Error during restart cleanup: {e}")

async def soft_cleanup():
    """Cleanup without setting permanent shutdown flags"""
    global bot
    
    try:
        # Close the bot
        if bot and not bot.is_closed():
            try:
                await bot.close()
            except Exception as e:
                logging.warning(f"Error closing bot during soft cleanup: {e}")
        
        # Cancel current tasks but don't mark as permanently shut down
        current_task = asyncio.current_task()
        tasks = [task for task in asyncio.all_tasks() if task != current_task and not task.done()]
        
        if tasks:
            logging.info(f"Cancelling {len(tasks)} remaining tasks...")
            for task in tasks:
                if not task.cancelled():
                    task.cancel()
                    
            # Wait briefly for tasks to cancel
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=3.0
                )
            except asyncio.TimeoutError:
                logging.warning("Some tasks did not cancel within timeout")
                
        # Clean up thread pool
        shutdown_thread_pool(wait=False)
        
    except Exception as e:
        logging.error(f"Error during soft cleanup: {e}")

def force_permanent_shutdown():
    """Call this when you want the bot to never restart"""
    global _force_permanent_shutdown, _shutting_down
    
    _force_permanent_shutdown = True
    _shutting_down = True
    logging.info("Permanent shutdown requested")

# --------------------
# Add Route modal + button
# --------------------
class AddRouteModal(discord.ui.Modal):
    def __init__(self, original_message: discord.Message = None):
        super().__init__(title="Add Route")
        self.original_message = original_message

        self.route_name = discord.ui.TextInput(
            label="Route name",
            placeholder="Sea View - Umbilo [R102]",
            max_length=100
        )
        self.start_coord = discord.ui.TextInput(
            label="Start coordinate (DMS)",
            placeholder='33°55\'12"S 18°25\'36"E'
        )
        self.end_coord = discord.ui.TextInput(
            label="End coordinate (DMS)",
            placeholder='33°56\'00"S 18°22\'00"E'
        )

        self.add_item(self.route_name)
        self.add_item(self.start_coord)
        self.add_item(self.end_coord)

    async def on_submit(self, interaction: discord.Interaction):
        if _shutting_down:
            return

        route_name = str(self.route_name)
        start_raw = str(self.start_coord)
        end_raw = str(self.end_coord)

        if not route_name:
            try:
                await interaction.response.send_message("Route name is required.", ephemeral=True)
            except (discord.NotFound, discord.HTTPException):
                pass
            return

        try:
            await show_loading_state(interaction, "Adding Route", "Parsing coordinates and generating map...")
            
            start_lat, start_lng = await run_in_thread(parse_dms_pair, start_raw)
            end_lat, end_lng = await run_in_thread(parse_dms_pair, end_raw)
            
            map_path = await async_add_route(route_name, start_lat, start_lng, end_lat, end_lng)
            
            distance_km = haversine_distance(start_lat, start_lng, end_lat, end_lng)

            embed = discord.Embed(
                title=f"Route Added - {route_name}",
                color=BotStyles.SUCCESS_COLOR,
                timestamp=datetime.now(timezone.utc)
            )
            embed.add_field(name="Distance", value=f"{distance_km:.2f} km", inline=True)
            embed.add_field(name="Start", value=f"{start_lat:.6f}, {start_lng:.6f}", inline=False)
            embed.add_field(name="End", value=f"{end_lat:.6f}, {end_lng:.6f}", inline=False)

            file = None
            if os.path.isfile(map_path):
                filename = os.path.basename(map_path)
                file = discord.File(map_path, filename=filename)
                embed.set_image(url=f"attachment://{filename}")

            await interaction.edit_original_response(embed=embed, attachments=[file] if file else [], view=BackToMenuView())
            
        except RuntimeError as e:
            if "shutdown" in str(e).lower():
                return
            embed = discord.Embed(
                title="System Error",
                description=f"System error: {e}",
                color=BotStyles.ERROR_COLOR
            )
            try:
                await interaction.edit_original_response(embed=embed, view=BackToMenuView())
            except (discord.NotFound, discord.HTTPException):
                pass
        except Exception as e:
            embed = discord.Embed(
                title="Route Addition Failed" if "Coordinate" not in str(e) else "Coordinate Parsing Error",
                description=f"Failed to add route: {e}",
                color=BotStyles.ERROR_COLOR
            )
            try:
                await interaction.edit_original_response(embed=embed, view=BackToMenuView())
            except (discord.NotFound, discord.HTTPException):
                pass

class AddRouteButton(Button):
    def __init__(self):
        super().__init__(label="Add Route", style=BotStyles.SUCCESS)

    async def callback(self, interaction: discord.Interaction):
        if _shutting_down:
            return
            
        try:
            await interaction.response.send_modal(AddRouteModal(interaction.message))
        except discord.errors.NotFound:
            try:
                await interaction.followup.send(
                    "Interaction expired. Please reopen the menu and try again.", ephemeral=True
                )
            except (discord.NotFound, discord.HTTPException):
                pass
        except (TypeError, RuntimeError):
            if not _shutting_down:
                try:
                    await interaction.response.send_modal(AddRouteModal())
                except (discord.NotFound, discord.HTTPException):
                    pass

# --------------------
# Back to Menu Button
# --------------------
class BackToMenuButton(Button):
    def __init__(self):
        super().__init__(label="Back to Menu", style=BotStyles.SECONDARY)

    async def callback(self, interaction: discord.Interaction):
        if _shutting_down:
            return
            
        try:
            await interaction.response.edit_message(
                content="Traffic Manager Menu",
                embed=None,
                attachments=[],
                view=get_main_menu_view()
            )
        except (discord.NotFound, discord.HTTPException):
            pass

# --------------------
# Back to Menu View
# --------------------
class BackToMenuView(View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(BackToMenuButton())

# --------------------
# Paginated Routes View
# --------------------
class RoutesPagination(View):
    def __init__(self, routes_data):
        super().__init__(timeout=300)
        self.routes = routes_data
        self.index = 0
        self.message: discord.Message | None = None
        self.cached_urls: dict[str, str] = {}

    async def update_embed(self, interaction: discord.Interaction = None):
        if _shutting_down:
            return
            
        try:
            route = self.routes[self.index]
            start_lat, start_lng = route['start_lat'], route['start_lng']
            end_lat, end_lng = route['end_lat'], route['end_lng']
            distance_km = haversine_distance(start_lat, start_lng, end_lat, end_lng)

            embed = discord.Embed(
                title=f"Route: {route['name']}",
                color=BotStyles.PRIMARY_COLOR,
                timestamp=datetime.now(timezone.utc)
            )
            embed.add_field(name="Distance", value=f"{distance_km:.2f} km", inline=True)
            embed.add_field(name="Start", value=f"{start_lat:.6f}, {start_lng:.6f}", inline=False)
            embed.add_field(name="End", value=f"{end_lat:.6f}, {end_lng:.6f}", inline=False)
            embed.set_footer(text=f"Page {self.index + 1} of {len(self.routes)}")

            file = None
            if route.get("map_path") and os.path.isfile(route["map_path"]):
                if route["name"] in self.cached_urls:
                    embed.set_image(url=self.cached_urls[route["name"]])
                else:
                    filename = os.path.basename(route["map_path"])
                    file = discord.File(route["map_path"], filename=filename)
                    embed.set_image(url=f"attachment://{filename}")

            self.prev_button.disabled = self.index == 0
            self.next_button.disabled = self.index == len(self.routes) - 1

            if interaction:
                await interaction.response.edit_message(embed=embed, attachments=[file] if file else [], view=self)
                if not self.message:
                    self.message = await interaction.original_response()
            elif self.message:
                await self.message.edit(embed=embed, attachments=[file] if file else [], view=self)

            if file and self.message:
                uploaded_embed = self.message.embeds[0] if self.message.embeds else None
                if uploaded_embed and uploaded_embed.image and uploaded_embed.image.url:
                    self.cached_urls[route["name"]] = uploaded_embed.image.url
                    
        except (discord.NotFound, discord.HTTPException):
            pass

    @discord.ui.button(label="Previous", style=BotStyles.SECONDARY)
    async def prev_button(self, interaction: discord.Interaction, button: Button):
        if _shutting_down:
            return
        self.index = max(0, self.index - 1)
        await self.update_embed(interaction)

    @discord.ui.button(label="Next", style=BotStyles.SECONDARY)
    async def next_button(self, interaction: discord.Interaction, button: Button):
        if _shutting_down:
            return
        self.index = min(len(self.routes) - 1, self.index + 1)
        await self.update_embed(interaction)

    @discord.ui.button(label="Main Menu", style=BotStyles.DANGER)
    async def close_button(self, interaction: discord.Interaction, button: Button):
        if _shutting_down:
            return
        try:
            await interaction.response.edit_message(
                content="Traffic Manager Menu",
                embed=None,
                attachments=[],
                view=get_main_menu_view()
            )
            self.stop()
        except (discord.NotFound, discord.HTTPException):
            pass

# --------------------
# List Routes Button
# --------------------
class ListRoutesButton(Button):
    def __init__(self):
        super().__init__(label="List Routes", style=BotStyles.PRIMARY)

    async def callback(self, interaction: discord.Interaction):
        if _shutting_down:
            return
            
        try:
            await show_loading_state(interaction, "Loading Routes", "Fetching routes and generating maps...")
            
            rows = await async_get_routes()

            if not rows:
                embed = discord.Embed(
                    title="No Routes Found",
                    description="No routes have been added yet.",
                    color=BotStyles.WARNING_COLOR
                )
                await interaction.edit_original_response(embed=embed, view=BackToMenuView())
                return

            routes_data = []
            for row in rows:
                if _shutting_down:
                    return

                # Access row data using keys instead of unpacking to ensure proper types
                route_id = row['id']
                name = row['name']
                start_lat = row['start_lat']
                start_lng = row['start_lng']
                end_lat = row['end_lat']
                end_lng = row['end_lng']
                map_path = os.path.join(MAP_DIR, f"{name}.png")
                
                if not os.path.isfile(map_path):
                    try:
                        map_path = await async_get_route_map(name, start_lat, start_lng, end_lat, end_lng)
                    except RuntimeError:
                        if _shutting_down:
                            return
                        map_path = None
                    except Exception:
                        map_path = None
                
                routes_data.append({
                    "name": name,
                    "start_lat": start_lat,
                    "start_lng": start_lng,
                    "end_lat": end_lat,
                    "end_lng": end_lng,
                    "map_path": map_path
                })

            pagination = RoutesPagination(routes_data)
            await interaction.edit_original_response(content="Listing routes...", embed=None, view=pagination)
            pagination.message = await interaction.original_response()
            await pagination.update_embed()

        except RuntimeError as e:
            if "shutdown" in str(e).lower():
                return
            embed = discord.Embed(
                title="System Error",
                description="System is shutting down",
                color=BotStyles.ERROR_COLOR
            )
            try:
                await interaction.edit_original_response(embed=embed, view=BackToMenuView())
            except (discord.NotFound, discord.HTTPException):
                pass
        except Exception as e:
            embed = discord.Embed(
                title="Error Loading Routes",
                description=f"Failed to load routes: {str(e)}",
                color=BotStyles.ERROR_COLOR
            )
            try:
                await interaction.edit_original_response(embed=embed, view=BackToMenuView())
            except (discord.NotFound, discord.HTTPException):
                pass

# --------------------
# Confirmation Popup
# --------------------
class ConfirmDeleteView(View):
    def __init__(self, route_name: str, route_data: dict, all_routes: list):
        super().__init__(timeout=300)
        self.route_name = route_name
        self.route_data = route_data
        self.all_routes = all_routes

        self.add_item(YesButton(route_name, route_data, self))
        self.add_item(NoButton(self, all_routes))

class YesButton(Button):
    def __init__(self, route_name: str, route_data: dict, parent_view: View):
        super().__init__(label="Yes", style=BotStyles.DANGER)
        self.route_name = route_name
        self.route_data = route_data
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        if _shutting_down:
            return
            
        try:
            await show_loading_state(interaction, "Deleting Route", "Removing route and cleaning up files...")

            await async_delete_route(self.route_name)

            if self.route_data.get("map_path") and os.path.isfile(self.route_data["map_path"]):
                await run_in_thread(os.remove, self.route_data["map_path"])

            embed = discord.Embed(
                title=f"Route Removed - {self.route_name}",
                color=BotStyles.SUCCESS_COLOR,
                timestamp=datetime.now(timezone.utc)
            )
            embed.add_field(name="Start", value=f"{self.route_data['start_lat']:.6f}, {self.route_data['start_lng']:.6f}", inline=False)
            embed.add_field(name="End", value=f"{self.route_data['end_lat']:.6f}, {self.route_data['end_lng']:.6f}", inline=False)

            await interaction.edit_original_response(embed=embed, view=BackToMenuView(), attachments=[])
            self.parent_view.stop()

        except RuntimeError as e:
            if "shutdown" in str(e).lower():
                return
        except Exception as e:
            embed = discord.Embed(
                title="Deletion Failed",
                description=f"Failed to delete route: {str(e)}",
                color=BotStyles.ERROR_COLOR
            )
            try:
                await interaction.edit_original_response(embed=embed, view=BackToMenuView())
            except (discord.NotFound, discord.HTTPException):
                pass
class NoButton(Button):
    def __init__(self, parent_view: View, all_routes: list):
        super().__init__(label="No", style=BotStyles.SECONDARY)
        self.parent_view = parent_view
        self.all_routes = all_routes

    async def callback(self, interaction: discord.Interaction):
        if _shutting_down:
            return
            
        try:
            view = RemoveRouteView(self.all_routes)
            await interaction.response.edit_message(
                content="Select a route to remove:",
                embed=None,
                view=view
            )
            self.parent_view.stop()
        except (discord.NotFound, discord.HTTPException):
            pass

# --------------------
# Remove Route Select + View
# --------------------
class RemoveRouteSelect(Select):
    def __init__(self, routes):
        options = [
            SelectOption(
                label=r['name'],
                description=f"{r['start_lat']:.4f},{r['start_lng']:.4f} → {r['end_lat']:.4f},{r['end_lng']:.4f}"
            ) for r in routes
        ]
        super().__init__(placeholder="Select a route to remove", min_values=1, max_values=1, options=options)
        self.routes = {r['name']: r for r in routes}

    async def callback(self, interaction: discord.Interaction):
        if _shutting_down:
            return
            
        try:
            selected_name = self.values[0]
            route = self.routes[selected_name]

            await show_loading_state(interaction, "Preparing Deletion", "Loading route details...")

            file = None
            if route.get("map_path") and os.path.isfile(route["map_path"]):
                filename = os.path.basename(route["map_path"])
                file = discord.File(route["map_path"], filename=filename)

            embed = discord.Embed(
                title=f"Confirm Deletion - {selected_name}",
                description="Are you sure you want to delete this route?",
                color=BotStyles.WARNING_COLOR,
                timestamp=datetime.now(timezone.utc)
            )
            embed.add_field(name="Start", value=f"{route['start_lat']:.6f}, {route['start_lng']:.6f}", inline=False)
            embed.add_field(name="End", value=f"{route['end_lat']:.6f}, {route['end_lng']:.6f}", inline=False)

            if file:
                embed.set_image(url=f"attachment://{filename}")

            view = ConfirmDeleteView(selected_name, route, list(self.routes.values()))
            await interaction.edit_original_response(embed=embed, view=view, attachments=[file] if file else [])
            
        except (discord.NotFound, discord.HTTPException):
            pass

class RemoveRouteView(View):
    def __init__(self, routes):
        super().__init__(timeout=300)
        self.routes = {r['name']: r for r in routes}
        self.add_item(RemoveRouteSelect(routes))
        self.add_item(BackToMenuButton())

# --------------------
# Remove Route Button
# --------------------
class RemoveRouteButton(Button):
    def __init__(self):
        super().__init__(label="Remove Route", style=BotStyles.DANGER)

    async def callback(self, interaction: discord.Interaction):
        if _shutting_down:
            return
            
        try:
            await show_loading_state(interaction, "Loading Routes", "Fetching routes for removal...")
            
            rows = await async_get_routes()
            
            if not rows:
                embed = discord.Embed(
                    title="No Routes to Remove",
                    description="No routes have been added yet.",
                    color=BotStyles.WARNING_COLOR
                )
                await interaction.edit_original_response(embed=embed, view=BackToMenuView())
                return

            routes_data = []
            for row in rows:
                # Access row data using keys instead of indices to ensure proper types
                route_id = row['id']
                name = row['name']
                start_lat = row['start_lat']
                start_lng = row['start_lng']
                end_lat = row['end_lat']
                end_lng = row['end_lng']
                routes_data.append({
                    "name": name,
                    "start_lat": start_lat,
                    "start_lng": start_lng,
                    "end_lat": end_lat,
                    "end_lng": end_lng,
                    "map_path": os.path.join(MAP_DIR, f"{name}.png")
                })

            view = RemoveRouteView(routes_data)
            await interaction.edit_original_response(
                content="Select a route to remove:",
                embed=None,
                view=view
            )

        except RuntimeError as e:
            if "shutdown" in str(e).lower():
                return
        except Exception as e:
            embed = discord.Embed(
                title="Error Loading Routes",
                description=f"Failed to load routes: {str(e)}",
                color=BotStyles.ERROR_COLOR
            )
            try:
                await interaction.edit_original_response(embed=embed, view=BackToMenuView())
            except (discord.NotFound, discord.HTTPException):
                pass

# ---------------------
# Main Traffic Status Button
# ---------------------
class TrafficStatusMainButton(Button):
    def __init__(self):
        super().__init__(label="Check Traffic Status", style=BotStyles.PRIMARY)

    async def callback(self, interaction: discord.Interaction):
        if _shutting_down:
            return
            
        try:
            view = TrafficStatusView(interaction.message)
            await interaction.response.edit_message(content="Choose an option:", embed=None, view=view)
        except (discord.NotFound, discord.HTTPException):
            pass

# --------------------
# Traffic Status Menu
# --------------------
class TrafficStatusView(View):
    def __init__(self, original_message: discord.Message):
        super().__init__(timeout=300)
        self.original_message = original_message
        self.add_item(CheckSingleRouteButton(original_message))
        self.add_item(CheckAllRoutesButton(original_message))
        self.add_item(BackToMenuButton())

# ---------------------
# Buttons for Traffic Status
# ---------------------
class CheckSingleRouteButton(Button):
    def __init__(self, original_message: discord.Message):
        super().__init__(label="Check a Specific Route", style=BotStyles.PRIMARY)
        self.original_message = original_message

    async def callback(self, interaction: discord.Interaction):
        if _shutting_down:
            return
            
        try:
            await show_loading_state(interaction, "Loading Routes", "Fetching available routes...")
            
            routes = await async_get_routes()
            if not routes:
                embed = discord.Embed(
                    title="No Routes Available",
                    description="No routes found to check.",
                    color=BotStyles.WARNING_COLOR
                )
                await interaction.edit_original_response(embed=embed, view=BackToMenuView())
                return
            
            await interaction.edit_original_response(
                content="Select a route to check:",
                embed=None,
                view=SelectRouteView(self.original_message, routes)
            )

        except RuntimeError as e:
            if "shutdown" in str(e).lower():
                return
        except Exception as e:
            embed = discord.Embed(
                title="Error Loading Routes",
                description=f"Failed to load routes: {str(e)}",
                color=BotStyles.ERROR_COLOR
            )
            try:
                await interaction.edit_original_response(embed=embed, view=BackToMenuView())
            except (discord.NotFound, discord.HTTPException):
                pass

class CheckAllRoutesButton(Button):
    def __init__(self, original_message: discord.Message):
        super().__init__(label="Check All Routes", style=BotStyles.SUCCESS)
        self.original_message = original_message

    async def callback(self, interaction: discord.Interaction):
        if _shutting_down:
            return
            
        try:
            await show_loading_state(interaction, "Checking All Routes", "This may take a moment...")

            routes = await async_get_routes()
            if not routes:
                embed = discord.Embed(
                    title="No Routes Found",
                    description="No routes available to check.",
                    color=BotStyles.WARNING_COLOR
                )
                await interaction.edit_original_response(embed=embed, view=BackToMenuView())
                return

            tasks = []
            for r in routes:
                if _shutting_down:
                    return
                    
                # Access row data using keys instead of unpacking to ensure proper types
                route_id = r['id']
                name = r['name']
                start_lat = r['start_lat']
                start_lng = r['start_lng']
                end_lat = r['end_lat']
                end_lng = r['end_lng']
                last_normal_time = r['last_normal_time']
                last_state = r['last_state']
                historical_json = r['historical_times']
                baseline = calculate_baseline([] if not historical_json else json.loads(historical_json))
                
                task = async_check_traffic(f"{start_lat},{start_lng}", f"{end_lat},{end_lng}", baseline)
                tasks.append((r, task))

            results = []
            for route, task in tasks:
                if _shutting_down:
                    return
                try:
                    traffic_result = await task
                    results.append({
                        "route": route,
                        "traffic": traffic_result
                    })
                    # Update database with traffic results
                    route_id = route["id"]
                    if "error" not in traffic_result:
                        await run_in_thread(update_route_time, route_id, traffic_result["total_normal"], traffic_result["state"])
                except RuntimeError as e:
                    if "shutdown" in str(e).lower():
                        return
                    results.append({
                        "route": route,
                        "traffic": {"error": str(e), "state": "Error"}
                    })
                except Exception as e:
                    logging.error(f"Failed to check traffic for route {route['name']}: {e}")
                    results.append({
                        "route": route,
                        "traffic": {"error": str(e), "state": "Error"}
                    })

            view = TrafficPaginationView(results, original_message=interaction.message)
            embed, attachments = await view.get_page_embed()
            await interaction.edit_original_response(embed=embed, attachments=attachments, view=view)

        except RuntimeError as e:
            if "shutdown" in str(e).lower():
                return
        except Exception as e:
            embed = discord.Embed(
                title="Traffic Check Failed",
                description=f"Failed to check traffic: {str(e)}",
                color=BotStyles.ERROR_COLOR
            )
            try:
                await interaction.edit_original_response(embed=embed, view=BackToMenuView())
            except (discord.NotFound, discord.HTTPException):
                pass

# ---------------------
# Route Selection Dropdown
# ---------------------
class SelectRouteView(View):
    def __init__(self, original_message: discord.Message, routes):
        super().__init__(timeout=300)
        self.original_message = original_message
        self.add_item(SelectRoute(original_message, routes))
        self.add_item(BackToTrafficStatusButton(original_message))

class SelectRoute(Select):
    def __init__(self, original_message: discord.Message, routes):
        options = [SelectOption(label=r["name"]) for r in routes]
        super().__init__(placeholder="Select a route...", min_values=1, max_values=1, options=options)
        self.routes = {r["name"]: r for r in routes}
        self.original_message = original_message

    async def callback(self, interaction: discord.Interaction):
        if _shutting_down:
            return
            
        try:
            selected = self.values[0]
            route = self.routes[selected]
            # Access route data using keys instead of unpacking to ensure proper types
            route_id = route['id']
            name = route['name']
            start_lat = route['start_lat']
            start_lng = route['start_lng']
            end_lat = route['end_lat']
            end_lng = route['end_lng']
            historical_json = route['historical_times']
            
            await show_loading_state(interaction, f"Checking Traffic - {name}", "Fetching current traffic conditions...")

            baseline = calculate_baseline([] if not historical_json else json.loads(historical_json))
            traffic = await async_check_traffic(f"{start_lat},{start_lng}", f"{end_lat},{end_lng}", baseline)

            map_path = await async_get_route_map(name, start_lat, start_lng, end_lat, end_lng)
            file = File(map_path, filename=os.path.basename(map_path)) if os.path.isfile(map_path) else None

            if "error" in traffic:
                color = BotStyles.ERROR_COLOR
                state_text = "Error"
            else:
                await run_in_thread(update_route_time, route_id, traffic["total_normal"], traffic["state"])
                color = BotStyles.SUCCESS_COLOR if traffic['state'] == 'Normal' else BotStyles.ERROR_COLOR
                state_text = "Normal" if traffic['state'] == 'Normal' else "Heavy"

            embed = Embed(
                title=f"Traffic Alert - {name}",
                color=color,
                timestamp=datetime.now(timezone.utc)
            )

            if "error" in traffic:
                embed.add_field(name="Error", value=traffic["error"], inline=False)
            else:
                embed.add_field(name="State", value=state_text, inline=True)
                embed.add_field(name="Distance", value=f"{traffic['distance_km']:.2f} km", inline=True)
                embed.add_field(name="Live Time", value=f"{traffic['total_live']} min", inline=True)
                embed.add_field(name="Normal Time", value=f"{traffic['total_normal']} min", inline=True)
                embed.add_field(name="Delay", value=f"{traffic['total_delay']} min", inline=True)
                
                segments_summary = summarize_segments(traffic['heavy_segments']) or 'None'
                embed.add_field(name="Heavy Segments", value=segments_summary, inline=False)

            if file:
                embed.set_image(url=f"attachment://{os.path.basename(map_path)}")

            await interaction.edit_original_response(
                embed=embed,
                attachments=[file] if file else [],
                view=BackToTrafficStatusView(self.original_message)
            )

        except RuntimeError as e:
            if "shutdown" in str(e).lower():
                return
        except Exception as e:
            embed = discord.Embed(
                title="Traffic Check Failed",
                description=f"Failed to check traffic for {name}: {str(e)}",
                color=BotStyles.ERROR_COLOR
            )
            try:
                await interaction.edit_original_response(embed=embed, view=BackToTrafficStatusView(self.original_message))
            except (discord.NotFound, discord.HTTPException):
                pass

# ---------------------
# Pagination for multiple routes
# ---------------------
class TrafficPaginationView(View):
    def __init__(self, results, original_message: discord.Message):
        super().__init__(timeout=300)  # 5 minute timeout
        self.results = results
        self.original_message = original_message
        self.current_page = 0
        self.total_pages = len(results)

        # Create buttons with proper references for update_buttons()
        self.prev_button = Button(label="Previous", style=BotStyles.SECONDARY)
        self.next_button = Button(label="Next", style=BotStyles.SECONDARY)
        self.back_button = BackToTrafficStatusButton(original_message)

        # Connect callbacks
        self.prev_button.callback = self.prev_callback
        self.next_button.callback = self.next_callback

        # Add buttons to view
        self.add_item(self.prev_button)
        self.add_item(self.next_button)
        self.add_item(self.back_button)

        # Initialize button states
        self.update_buttons()

    def update_buttons(self):
        """Update button disabled states based on current page"""
        self.prev_button.disabled = self.current_page == 0
        self.next_button.disabled = self.current_page >= self.total_pages - 1

    async def get_page_embed(self):
        """Generate embed and attachments for current page"""
        if _shutting_down:
            return None, []

        try:
            result = self.results[self.current_page]
            route = result["route"]

            # Access route data using keys instead of unpacking to ensure proper types
            r_id = route['id']
            name = route['name']
            start_lat = route['start_lat']
            start_lng = route['start_lng']
            end_lat = route['end_lat']
            end_lng = route['end_lng']
            historical_json = route['historical_times']
            traffic = result["traffic"]

            # Determine color based on traffic state
            if "error" in traffic:
                color = BotStyles.ERROR_COLOR
                state_text = "Error"
            else:
                color = BotStyles.SUCCESS_COLOR if traffic['state'] == 'Normal' else BotStyles.ERROR_COLOR
                state_text = "Normal" if traffic['state'] == 'Normal' else "Heavy"

            embed = Embed(
                title=f"Traffic Alert - {name}",
                color=color,
                timestamp=datetime.now(timezone.utc)
            )

            if "error" in traffic:
                embed.add_field(name="Error", value=traffic["error"], inline=False)
            else:
                embed.add_field(name="State", value=state_text, inline=True)
                embed.add_field(name="Distance", value=f"{traffic['distance_km']:.2f} km", inline=True)
                embed.add_field(name="Live Time", value=f"{traffic['total_live']} min", inline=True)
                embed.add_field(name="Normal Time", value=f"{traffic['total_normal']} min", inline=True)
                embed.add_field(name="Delay", value=f"{traffic['total_delay']} min", inline=True)
                
                segments_summary = summarize_segments(traffic['heavy_segments']) or 'None'
                embed.add_field(name="Heavy Segments", value=segments_summary, inline=False)
            
            embed.set_footer(text=f"Page {self.current_page+1} of {self.total_pages}")

            # Generate map attachment if it exists
            try:
                if not _shutting_down:
                    map_path = await async_get_route_map(name, start_lat, start_lng, end_lat, end_lng)
                    if map_path and os.path.isfile(map_path):
                        file = File(map_path, filename=os.path.basename(map_path))
                        embed.set_image(url=f"attachment://{os.path.basename(map_path)}")
                        return embed, [file]
            except RuntimeError as e:
                if "shutdown" in str(e).lower():
                    return embed, []
                logging.error(f"Runtime error generating map for {name}: {e}")
            except Exception as e:
                logging.error(f"Failed to generate map for {name}: {e}")
            
            return embed, []
            
        except Exception as e:
            logging.error(f"Error in get_page_embed: {e}")
            
            # Return error embed instead of None
            error_embed = Embed(
                title="Error Loading Page",
                description=f"Failed to load traffic data: {str(e)}",
                color=BotStyles.ERROR_COLOR,
                timestamp=datetime.now(timezone.utc)
            )
            error_embed.set_footer(text=f"Page {self.current_page+1} of {self.total_pages}")
            return error_embed, []

    async def prev_callback(self, interaction: discord.Interaction):
        """Handle previous button click"""
        if _shutting_down:
            return
            
        try:
            if self.current_page > 0:
                self.current_page -= 1
                self.update_buttons()
                embed, attachments = await self.get_page_embed()
                
                if embed:
                    await interaction.response.edit_message(
                        embed=embed, 
                        attachments=attachments, 
                        content=None, 
                        view=self
                    )
                else:
                    # Fallback if embed generation failed
                    await interaction.response.defer()
                    
        except discord.NotFound:
            # Interaction expired
            pass
        except discord.HTTPException as e:
            logging.error(f"Discord HTTP error in prev_callback: {e}")
            try:
                await interaction.response.defer()
            except:
                pass
        except RuntimeError as e:
            if "shutdown" in str(e).lower():
                return
            logging.error(f"Runtime error in prev_callback: {e}")
        except Exception as e:
            logging.error(f"Unexpected error in prev_callback: {e}")
            try:
                await interaction.response.defer()
            except:
                pass

    async def next_callback(self, interaction: discord.Interaction):
        """Handle next button click"""
        if _shutting_down:
            return
            
        try:
            if self.current_page < self.total_pages - 1:
                self.current_page += 1
                self.update_buttons()
                embed, attachments = await self.get_page_embed()
                
                if embed:
                    await interaction.response.edit_message(
                        embed=embed, 
                        attachments=attachments, 
                        content=None, 
                        view=self
                    )
                else:
                    # Fallback if embed generation failed
                    await interaction.response.defer()
                    
        except discord.NotFound:
            # Interaction expired
            pass
        except discord.HTTPException as e:
            logging.error(f"Discord HTTP error in next_callback: {e}")
            try:
                await interaction.response.defer()
            except:
                pass
        except RuntimeError as e:
            if "shutdown" in str(e).lower():
                return
            logging.error(f"Runtime error in next_callback: {e}")
        except Exception as e:
            logging.error(f"Unexpected error in next_callback: {e}")
            try:
                await interaction.response.defer()
            except:
                pass

    async def on_timeout(self):
        """Handle view timeout - disable all buttons"""
        if _shutting_down:
            return
            
        try:
            # Disable all buttons
            for item in self.children:
                if hasattr(item, 'disabled'):
                    item.disabled = True
            
            # Try to update the message to show disabled state
            if hasattr(self, '_message') and self._message:
                try:
                    await self._message.edit(view=self)
                except (discord.NotFound, discord.HTTPException):
                    # Message might be deleted or we might not have permissions
                    pass
            
        except Exception as e:
            logging.error(f"Error in on_timeout: {e}")

    async def on_error(self, interaction: discord.Interaction, error: Exception, item):
        """Handle view errors"""
        if _shutting_down:
            return
            
        logging.error(f"View error in TrafficPaginationView: {error}")
        
        try:
            if not interaction.response.is_done():
                await interaction.response.defer()
        except:
            pass

    def set_message(self, message: discord.Message):
        """Set message reference for timeout handling"""
        self._message = message

# ---------------------
# Back Buttons and Views
# ---------------------
class BackToTrafficStatusButton(Button):
    def __init__(self, original_message: discord.Message):
        super().__init__(label="Back", style=BotStyles.SECONDARY)
        self.original_message = original_message

    async def callback(self, interaction: discord.Interaction):
        if _shutting_down:
            return
            
        try:
            await interaction.response.edit_message(
                content="Traffic Status Menu",
                embed=None,
                attachments=[],
                view=TrafficStatusView(self.original_message)
            )
        except (discord.NotFound, discord.HTTPException):
            pass

class BackToTrafficStatusView(View):
    def __init__(self, original_message: discord.Message):
        super().__init__(timeout=300)
        self.add_item(BackToTrafficStatusButton(original_message))

# --------------------
# Thresholds Button
# --------------------
class ThresholdsButton(Button):
    def __init__(self):
        super().__init__(label="Manage Thresholds", style=BotStyles.SECONDARY)

    async def callback(self, interaction: discord.Interaction):
        if _shutting_down:
            return
            
        try:
            await show_loading_state(interaction, "Loading Thresholds", "Fetching current threshold configuration...")

            thresholds = await async_get_thresholds()
            if not thresholds:
                thresholds = []

            view = ThresholdsView(thresholds)
            if thresholds:
                file = await run_in_thread(view.generate_thresholds_image, thresholds)
                await interaction.edit_original_response(
                    content="Traffic Thresholds Configuration",
                    embed=view.embed,
                    attachments=[file],
                    view=view
                )
            else:
                await interaction.edit_original_response(
                    content="Traffic Thresholds Configuration",
                    embed=view.embed,
                    view=view
                )

        except RuntimeError as e:
            if "shutdown" in str(e).lower():
                return
        except Exception as e:
            embed = discord.Embed(
                title="Failed to Load Thresholds",
                description=f"Error loading thresholds: {str(e)}",
                color=BotStyles.ERROR_COLOR
            )
            try:
                await interaction.edit_original_response(embed=embed, view=BackToMenuView())
            except (discord.NotFound, discord.HTTPException):
                pass

# --------------------
# Thresholds View
# --------------------
class ThresholdsView(View):
    def __init__(self, thresholds):
        super().__init__(timeout=300)
        self.thresholds = thresholds

        if thresholds:
            # Embed with image
            self.embed = Embed(
                title="Traffic Thresholds Configuration",
                description="Select a threshold below to edit, or use the Sensitivity Guide for help",
                color=BotStyles.INFO_COLOR
            )
            self.embed.set_image(url="attachment://thresholds_table.png")

            # Dropdown options
            options = [
                SelectOption(
                    label=f"{t['min_km']}-{t['max_km']} km",
                    description=f"Route x{t['factor_total']} | Segment x{t['factor_step']} | Delay +{t['delay_total']} / +{t['delay_step']}"
                ) for t in thresholds
            ]
            self.add_item(SelectThreshold(options, thresholds))
        else:
            self.embed = Embed(
                title="Traffic Thresholds Configuration",
                description="No thresholds found. Consider resetting to defaults.",
                color=BotStyles.WARNING_COLOR
            )

        # Action buttons
        self.add_item(SensitivityHelpButton())
        self.add_item(ResetThresholdsButton())
        self.add_item(BackToMenuButton())

    def generate_thresholds_image(self, thresholds):
        """Generate a large, readable thresholds table image for Discord embed."""
        # Settings
        col_widths = [250, 200, 200, 200, 200]  # wider columns
        row_height = 80  # taller rows
        padding = 30
        header_font_size = 72
        row_font_size = 56

        # Colors
        bg_color = (54, 57, 63)  # Discord embed background
        header_text_color = (0, 102, 204)  # Blue headers
        row_text_color = (255, 255, 255)  # White text

        # Fonts
        font_path = None  # Use default PIL font
        header_font = ImageFont.load_default() if not font_path else ImageFont.truetype(font_path, header_font_size)
        row_font = ImageFont.load_default() if not font_path else ImageFont.truetype(font_path, row_font_size)

        num_rows = len(thresholds)
        img_width = sum(col_widths) + padding * 2
        img_height = row_height * (num_rows + 1) + padding * 2  # +1 for header

        image = Image.new("RGB", (img_width, img_height), color=bg_color)
        draw = ImageDraw.Draw(image)

        # Draw header
        headers = ["Distance Range", "Route Delay", "Segment Delay", "Segment Multiplier", "Route Multiplier"]
        x = padding
        y = padding
        for i, header in enumerate(headers):
            draw.text((x + 10, y + 20), header, fill=header_text_color, font=header_font)
            x += col_widths[i]

        # Draw rows
        y += row_height
        for t in thresholds:
            x = padding
            values = [
                f"{t['min_km']} - {t['max_km']} km",
                str(t['factor_total']),
                str(t['factor_step']),
                str(t['delay_total']),
                str(t['delay_step'])
            ]
            for i, val in enumerate(values):
                draw.text((x + 10, y + 20), val, fill=row_text_color, font=row_font)
                x += col_widths[i]
            y += row_height

        # Save to BytesIO
        with BytesIO() as buffer:
            image.save(buffer, format="PNG")
            image.close()
            buffer.seek(0)
            # Create File with buffer contents
            return File(BytesIO(buffer.getvalue()), filename="thresholds_table.png")

# --------------------
# Sensitivity Help Button
# --------------------
class SensitivityHelpButton(Button):
    def __init__(self):
        super().__init__(label="Info", style=BotStyles.SECONDARY)

    async def callback(self, interaction: discord.Interaction):
        if _shutting_down:
            return
            
        embed = discord.Embed(
            title="Threshold Guide",
            description=(
                "**Definitions with Examples:**\n"
                "- **Route Delay Allowance:**\n"
                "    Extra minutes over normal route time before flagging route.\n"
                "    Example: 30 min + 5 min = 35 min → route flagged if >35 min\n\n"
                "- **Segment Delay Allowance:**\n"
                "    Extra minutes per segment before flagging segment.\n"
                "    Example: 5 min + 2 min = 7 min → segment flagged if >7 min\n\n"
                "- **Route Time Multiplier:**\n"
                "    Multiplies route normal time to set threshold.\n"
                "    Example: 30 min × 1.5 = 45 min → route flagged if >45 min\n\n"
                "- **Segment Time Multiplier:**\n"
                "    Multiplies segment normal time to flag that segment individually.\n"
                "    Example: 5 min × 2 = 10 min → segment flagged if >10 min\n\n"
                "**Sensitivity Helper:**\n"
                "- **More Sensitive (flags traffic earlier):**\n"
                "    Increase multipliers\n"
                "    Decrease delay allowances\n\n"
                "- **Less Sensitive (flags traffic later):**\n"
                "    Decrease multipliers\n"
                "    Increase delay allowances"
            ),
            color=BotStyles.INFO_COLOR,
            timestamp=datetime.now(timezone.utc)
        )

        try:
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except (discord.NotFound, discord.HTTPException):
            pass

# --------------------
# Threshold Modal + select
# --------------------
class SelectThreshold(Select):
    def __init__(self, options, thresholds):
        super().__init__(placeholder="Select a threshold to edit", min_values=1, max_values=1, options=options)
        self.thresholds = thresholds

    async def callback(self, interaction: discord.Interaction):
        if _shutting_down:
            return
            
        try:
            selected_label = self.values[0]
            threshold = next(t for t in self.thresholds if f"{t['min_km']}-{t['max_km']} km" == selected_label)
            modal = EditThresholdModal(threshold, self.thresholds)
            await interaction.response.send_modal(modal)
        except (discord.NotFound, discord.HTTPException):
            pass

class EditThresholdModal(Modal):
    def __init__(self, threshold, all_thresholds):
        super().__init__(title=f"Edit Threshold {threshold['min_km']}-{threshold['max_km']} km")
        self.threshold = threshold
        self.all_thresholds = all_thresholds

        self.factor_total = TextInput(label="Route Time Multiplier", default=str(threshold["factor_total"]))
        self.factor_step = TextInput(label="Segment Time Multiplier", default=str(threshold["factor_step"]))
        self.delay_total = TextInput(label="Route Delay Allowance", default=str(threshold["delay_total"]))
        self.delay_step = TextInput(label="Segment Delay Allowance", default=str(threshold["delay_step"]))

        self.add_item(self.factor_total)
        self.add_item(self.factor_step)
        self.add_item(self.delay_total)
        self.add_item(self.delay_step)

    async def on_submit(self, interaction: discord.Interaction):
        if _shutting_down:
            return
            
        try:
            await show_loading_state(interaction, "Updating Threshold", "Saving changes...")

            for key, field in [("factor_total", self.factor_total),
                               ("factor_step", self.factor_step),
                               ("delay_total", self.delay_total),
                               ("delay_step", self.delay_step)]:
                try:
                    self.threshold[key] = float(field.value)
                except ValueError:
                    embed = discord.Embed(
                        title="Invalid Input",
                        description=f"Invalid input for {key}. Must be a number.",
                        color=BotStyles.ERROR_COLOR
                    )
                    await interaction.edit_original_response(embed=embed, view=BackToMenuView())
                    return

            await async_set_thresholds(self.all_thresholds)

            view = ThresholdsView(self.all_thresholds)
            file = await run_in_thread(view.generate_thresholds_image, self.all_thresholds)
            
            await interaction.edit_original_response(
                content="Threshold updated successfully.\nTraffic Thresholds Configuration",
                embed=view.embed,
                attachments=[file],
                view=view
            )

        except RuntimeError as e:
            if "shutdown" in str(e).lower():
                return
        except Exception as e:
            embed = discord.Embed(
                title="Update Failed",
                description=f"Failed to update threshold: {str(e)}",
                color=BotStyles.ERROR_COLOR
            )
            try:
                await interaction.edit_original_response(embed=embed, view=BackToMenuView())
            except (discord.NotFound, discord.HTTPException):
                pass

# ResetThresholdsButton
class ResetThresholdsButton(Button):
    def __init__(self):
        super().__init__(label="Reset to Default", style=BotStyles.DANGER)

    async def callback(self, interaction: discord.Interaction):
        if _shutting_down:
            return
            
        try:
            await show_loading_state(interaction, "Resetting Thresholds", "Restoring default values...")

            await async_reset_thresholds()
            thresholds = await async_get_thresholds()
            view = ThresholdsView(thresholds)
            
            if thresholds:
                file = await run_in_thread(view.generate_thresholds_image, thresholds)
                await interaction.edit_original_response(
                    content="Thresholds reset to default.\nTraffic Thresholds Configuration",
                    embed=view.embed,
                    attachments=[file],
                    view=view
                )
            else:
                await interaction.edit_original_response(
                    content="Thresholds reset to default.\nTraffic Thresholds Configuration",
                    embed=view.embed,
                    view=view
                )

        except RuntimeError as e:
            if "shutdown" in str(e).lower():
                return
        except Exception as e:
            embed = discord.Embed(
                title="Reset Failed",
                description=f"Failed to reset thresholds: {str(e)}",
                color=BotStyles.ERROR_COLOR
            )
            try:
                await interaction.edit_original_response(embed=embed, view=BackToMenuView())
            except (discord.NotFound, discord.HTTPException):
                pass

# --------------------
# Main Menu
# --------------------
def get_main_menu_view():
    """Return a new MainMenu view instance."""
    return MainMenu()

class MainMenu(View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(AddRouteButton())
        self.add_item(ListRoutesButton())
        self.add_item(RemoveRouteButton())
        self.add_item(TrafficStatusMainButton())
        self.add_item(ThresholdsButton())

# --------------------
# Bot events & commands
# --------------------
def attach_bot_events(bot):
    @bot.event
    async def on_ready():
        logging.info(f"Bot is online as {bot.user}")
        health_monitor.record_heartbeat()  # Record successful connection
        
        # Initialize database and ensure thread pool exists
        try:
            await run_in_thread(init_db)
            logging.info("Database initialized successfully")
        except Exception as e:
            logging.error(f"Failed to initialize database: {e}")
        ensure_thread_pool()

    @bot.event
    async def on_connect():
        logging.info("Bot connected to Discord")
        health_monitor.record_heartbeat()

    @bot.event
    async def on_resumed():
        logging.info("Bot session resumed")
        health_monitor.record_heartbeat()

    @bot.event
    async def on_message(message):
        # Update heartbeat on message activity
        health_monitor.record_heartbeat()
        await bot.process_commands(message)

    @bot.command()
    async def menu(ctx):
        health_monitor.record_heartbeat()
        await ctx.send("Traffic Manager Menu", view=get_main_menu_view())

    @bot.event
    async def on_error(event, *args, **kwargs):
        logging.error(f"Bot error in {event}: {args}")

    @bot.event
    async def on_command_error(ctx, error):
        logging.error(f"Command error: {error}")

    @bot.event
    async def on_disconnect():
        logging.warning("Bot disconnected. Will attempt reconnect automatically.")
        health_monitor.record_failure()

# =========================
# Bot runner with restart
# =========================
async def run_discord_bot():
    """Discord bot runner that stays online indefinitely.
    Restarts only on real crashes. Only stops on container shutdown."""
    global bot, _shutting_down, _force_permanent_shutdown, health_monitor

    logging.info("Discord bot runner starting...")
    
    if not _force_permanent_shutdown:
        _shutting_down = False

    while not _force_permanent_shutdown:
        try:
            bot = create_bot_instance()
            attach_bot_events(bot)
            ensure_thread_pool()

            logging.info("Starting Discord Bot...")
            await bot.start(TOKEN)  # NO timeout

        except asyncio.CancelledError:
            logging.warning("Bot start was cancelled")
            break

        except Exception as e:
            logging.error(f"Bot crashed unexpectedly: {type(e).__name__}: {e}")
            if _force_permanent_shutdown:
                logging.info("Permanent shutdown requested, stopping restarts")
                break
            # Brief pause before restarting
            await asyncio.sleep(5)

        finally:
            # Soft cleanup: close bot if needed, but keep thread pool for restart
            try:
                if bot and not bot.is_closed():
                    await bot.close()
            except Exception as e:
                logging.error(f"Error during bot close: {e}")
            bot = None

    # If container is stopping, do final cleanup
    await soft_cleanup()
    logging.info("Discord Bot stopped permanently")

if __name__ == "__main__":
    import signal
    import atexit

    def signal_handler(signum, frame):
        logger.info(f"Received signal {signum}, shutting down permanently")
        force_permanent_shutdown()
        sys.exit(0)

    def cleanup_on_exit():
        if not _force_permanent_shutdown:
            logger.info("Container exiting, performing permanent shutdown")
            force_permanent_shutdown()

    # Register signal handlers and atexit
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    atexit.register(cleanup_on_exit)

    try:
        logger.info("Starting Discord Bot...")
        asyncio.run(run_discord_bot())
    except KeyboardInterrupt:
        logger.info("Bot manually stopped")
    except Exception as e:
        logger.error(f"Discord Bot crashed: {type(e).__name__}: {e}")
        raise
    finally:
        logger.info("Discord Bot shutdown complete")
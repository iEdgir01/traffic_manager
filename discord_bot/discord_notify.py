"""Discord webhook notification service for traffic alerts.

This module handles sending traffic condition notifications to Discord
channels via webhooks when significant traffic changes are detected.
"""

import os
import json
import asyncio
import logging
import sys
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

import aiohttp
from traffic_utils import (
    with_db, summarize_segments, get_routes,
    calculate_baseline, check_route_traffic, update_route_time
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/app/data/discord_notify.log") if os.path.exists("/app/data") else logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
if not WEBHOOK_URL:
    raise ValueError("Missing environment variable: DISCORD_WEBHOOK_URL")

logger.info("Discord Notify module loaded")


# ---------------------
# DB helpers wrapped for async
# ---------------------
async def run_in_thread(fn, *args, **kwargs):
    return await asyncio.to_thread(fn, *args, **kwargs)


@with_db
def get_last_state(route_id, conn=None):
    with conn.cursor() as cur:
        cur.execute("SELECT last_state FROM routes WHERE id = %s", (route_id,))
        row = cur.fetchone()
        return row["last_state"] if row else None


@with_db
def update_last_state(route_id, new_state, conn=None):
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE routes SET last_state = %s WHERE id = %s",
            (new_state, route_id),
        )


# ---------------------
# Async traffic alert posting
# ---------------------
async def post_traffic_alerts_async():
    try:
        logger.info("Starting processing of all routes...")
        routes = await run_in_thread(get_routes)

        if not routes:
            logger.info("No routes found in the database.")
            return

        alerts_posted = 0
        logger.info(f"Total routes to process: {len(routes)}")

        async with aiohttp.ClientSession() as session:
            for route in routes:
                try:

                    route_id = route["id"]
                    name = route["name"]
                    start_lat = route["start_lat"]
                    start_lng = route["start_lng"]
                    end_lat = route["end_lat"]
                    end_lng = route["end_lng"]
                    historical_json = route.get("historical_times", "[]")

                    logger.info(f"Processing route '{name}'")

                    historical_data = json.loads(historical_json) if historical_json else []
                    baseline = calculate_baseline(historical_data)
                    logger.debug(f"Baseline calculated for {name}")


                    # Run traffic check in a thread (blocking function)
                    traffic = await run_in_thread(
                        check_route_traffic,
                        f"{start_lat},{start_lng}",
                        f"{end_lat},{end_lng}",
                        baseline
                    )

                    if not traffic:
                        logger.warning(f"No traffic data returned for {name}")
                        continue

                    current_state = traffic["state"]
                    prev_state = await run_in_thread(get_last_state, route_id)

                    # Determine if alert should be posted
                    should_post = False
                    if current_state.lower() == "heavy":
                        should_post = True
                    elif prev_state and prev_state.lower() == "heavy" and current_state.lower() == "normal":
                        should_post = True

                    if should_post:
                        color = 0x00FF00 if current_state.lower() == "normal" else 0xFF0000

                        # Generate read-aloud sentence
                        if current_state.lower() == "heavy":
                            sentence = f"Heavy traffic detected on {name}, current delay is {traffic['total_delay']} minutes."
                        elif prev_state and prev_state.lower() == "heavy" and current_state.lower() == "normal":
                            sentence = f"You can expect normal travel times on {name}."
                        else:
                            sentence = f"Traffic status update for {name}."

                        embed = {
                            "title": "Traffic Status",
                            "description": f"**Route:** {name}",
                            "color": color,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "fields": [
                                {"name": "State", "value": current_state, "inline": True},
                                {"name": "Distance", "value": f"{traffic['distance_km']:.2f} km", "inline": True},
                                {"name": "Live Time", "value": f"{traffic['total_live']} min", "inline": True},
                                {"name": "Normal Time", "value": f"{traffic['total_normal']} min", "inline": True},
                                {"name": "Delay", "value": f"{traffic['total_delay']} min", "inline": True},
                                {"name": "Heavy Segments", "value": summarize_segments(traffic['heavy_segments']) or 'None', "inline": False},
                                {"name": "Summary", "value": sentence, "inline": False}
                            ]
                        }

                        async with session.post(WEBHOOK_URL, json={"embeds": [embed]}, timeout=10) as resp:
                            if resp.status not in (200, 204):
                                text = await resp.text()
                                logger.error(f"Failed to post alert for {name}: {resp.status} - {text}")
                            else:
                                logger.info(f"Alert posted for {name}")
                                alerts_posted += 1

                    # Update DB asynchronously
                    await run_in_thread(update_route_time, route_id, traffic["total_normal"], current_state)
                    await run_in_thread(update_last_state, route_id, current_state)

                except Exception as e:
                    logger.error(f"Error processing route '{route.get('name','Unknown')}': {e}")

        if alerts_posted == 0:
            logger.info("No traffic alerts were necessary")
        else:
            logger.info(f"Completed processing. {alerts_posted} alert(s) posted")

    except Exception as e:
        logger.error(f"Traffic processing failed: {e}")


# ---------------------
# Run standalone
# ---------------------
if __name__ == "__main__":
    logger.info("Starting traffic alert processing...")
    try:
        asyncio.run(post_traffic_alerts_async())
    except KeyboardInterrupt:
        logger.info("Traffic alert processing stopped by user")
    except Exception as e:
        logger.error(f"Traffic alert processing failed: {e}")
        raise
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
from typing import List, Dict

import aiohttp
from traffic_utils import (
    with_db, summarize_segments, get_routes, get_route_priority,
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

# Gotify configuration for Android notifications
GOTIFY_URL = os.environ.get("GOTIFY_URL")
GOTIFY_TOKEN = os.environ.get("GOTIFY_TOKEN")
GOTIFY_PRIORITY = int(os.environ.get("GOTIFY_PRIORITY", "5")) if os.environ.get("GOTIFY_PRIORITY") else 5

logger.info("Discord Notify module loaded")


def create_simple_summary(route_data: List[Dict]) -> str:
    """Create a simple traffic summary for notifications"""
    heavy_routes = [r for r in route_data if r['status'].lower() == 'heavy']
    normal_routes = [r for r in route_data if r['status'].lower() == 'normal']

    summary_parts = []

    if heavy_routes:
        heavy_details = []
        for route in heavy_routes:
            delay_info = f"{route['delay']} minutes delay" if route['delay'] > 0 else "heavy traffic"
            heavy_details.append(f"{route['name']} has {delay_info}")

        if len(heavy_routes) == 1:
            summary_parts.append(f"Traffic alert: {heavy_details[0]}.")
        else:
            summary_parts.append(f"Traffic alert: {', '.join(heavy_details[:-1])}, and {heavy_details[-1]}.")

    if normal_routes:
        if len(normal_routes) == 1:
            summary_parts.append(f"{normal_routes[0]['name']} is running normally.")
        elif len(normal_routes) == 2:
            summary_parts.append(f"{normal_routes[0]['name']} and {normal_routes[1]['name']} are running normally.")
        else:
            normal_names = [r['name'] for r in normal_routes]
            summary_parts.append(f"{', '.join(normal_names[:-1])}, and {normal_names[-1]} are all running normally.")

    if not heavy_routes and not normal_routes:
        # Handle other statuses (Unknown, Error, etc.)
        other_routes = [f"{r['name']} status {r['status'].lower()}" for r in route_data]
        if len(other_routes) == 1:
            summary_parts.append(f"Route update: {other_routes[0]}.")
        else:
            summary_parts.append(f"Route update: {', '.join(other_routes)}.")

    return " ".join(summary_parts)


# ---------------------
# Gotify helper for Android notifications
# ---------------------
async def send_gotify_notification(title: str, message: str):
    """Send traffic alert notification to Gotify server"""
    if not GOTIFY_URL or not GOTIFY_TOKEN:
        logger.warning("GOTIFY_URL or GOTIFY_TOKEN not configured, skipping Gotify notification")
        return

    try:
        payload = {
            "title": title,
            "message": message,
            "priority": GOTIFY_PRIORITY
        }

        headers = {
            "Content-Type": "application/json"
        }

        url = f"{GOTIFY_URL}/message?token={GOTIFY_TOKEN}"

        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=10) as resp:
                if resp.status not in (200, 201):
                    text = await resp.text()
                    raise Exception(f"Gotify API returned {resp.status}: {text}")

    except Exception as e:
        raise Exception(f"Gotify notification failed: {e}")

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

        logger.info(f"Total routes to process: {len(routes)}")
        route_data = []

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
                        route_data.append({
                            "name": name,
                            "status": "Unknown",
                            "delay": 0,
                            "distance": 0
                        })
                        continue

                    current_state = traffic["state"]
                    prev_state = await run_in_thread(get_last_state, route_id)

                    route_data.append({
                        "name": name,
                        "status": current_state,
                        "delay": traffic["total_delay"],
                        "distance": traffic["distance_km"],
                        "prev_state": prev_state
                    })

                    # Update DB asynchronously
                    await run_in_thread(update_route_time, route_id, traffic["total_normal"], current_state)
                    await run_in_thread(update_last_state, route_id, current_state)

                except Exception as e:
                    logger.error(f"Error processing route '{route.get('name','Unknown')}': {e}")
                    route_data.append({
                        "name": route.get('name', 'Unknown'),
                        "status": "Error",
                        "delay": 0,
                        "distance": 0
                    })

            # Check if Discord alert should be posted (only on traffic state changes)
            discord_alert_needed = False
            for route in route_data:
                current = route["status"].lower()
                prev = route.get("prev_state", "").lower() if route.get("prev_state") else ""

                if (current == "heavy") or (prev == "heavy" and current == "normal"):
                    discord_alert_needed = True
                    break

            # Generate Discord table only when alert needed
            if discord_alert_needed and route_data:
                logger.info("Traffic state change detected - posting Discord alert")

                # Calculate column widths for alignment
                max_name_len = max(len(r["name"]) for r in route_data)
                max_status_len = max(len(r["status"]) for r in route_data)
                max_delay_len = max(len(f"{r['delay']} min") for r in route_data)
                max_distance_len = max(len(f"{r['distance']:.1f} km") for r in route_data)

                # Ensure minimum column widths for headers
                max_name_len = max(max_name_len, len("Route Name"))
                max_status_len = max(max_status_len, len("Status"))
                max_delay_len = max(max_delay_len, len("Delay"))
                max_distance_len = max(max_distance_len, len("Distance"))

                # Build table
                table_lines = []
                header = f"{'Route Name':<{max_name_len}} | {'Status':<{max_status_len}} | {'Delay':<{max_delay_len}} | {'Distance':<{max_distance_len}}"
                separator = f"{'-' * max_name_len}-+-{'-' * max_status_len}-+-{'-' * max_delay_len}-+-{'-' * max_distance_len}"

                table_lines.append(header)
                table_lines.append(separator)

                for route in route_data:
                    delay_str = f"{route['delay']} min"
                    distance_str = f"{route['distance']:.1f} km"
                    row = f"{route['name']:<{max_name_len}} | {route['status']:<{max_status_len}} | {delay_str:<{max_delay_len}} | {distance_str:<{max_distance_len}}"
                    table_lines.append(row)

                table_content = "\n".join(table_lines)

                embed = {
                    "title": "Traffic Alert - Status Change",
                    "color": 0xFF0000 if any(r["status"].lower() == "heavy" for r in route_data) else 0x00FF00,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "description": f"```\n{table_content}\n```"
                }

                async with session.post(WEBHOOK_URL, json={"embeds": [embed]}, timeout=10) as resp:
                    if resp.status not in (200, 204):
                        text = await resp.text()
                        logger.error(f"Failed to post traffic alert: {resp.status} - {text}")
                    else:
                        logger.info("Traffic alert posted successfully")
            else:
                logger.info("No traffic state changes detected - skipping Discord alert")

        # Priority-aware summary for Gotify TTS
        if route_data and GOTIFY_URL and GOTIFY_TOKEN:
            # Filter routes for Gotify processing based on priority logic
            gotify_routes = []
            for route in route_data:
                route_name = route["name"]
                current_state = route["status"].lower()
                prev_state = route.get("prev_state", "").lower() if route.get("prev_state") else ""

                # Get route priority from database
                route_priority = await run_in_thread(get_route_priority, route_name)

                # High Priority: Always include
                if route_priority == "High":
                    gotify_routes.append(route)
                    logger.debug(f"Including High priority route '{route_name}' in Gotify summary")
                # Normal Priority: Only when Heavy OR was Heavy→Normal
                elif route_priority == "Normal":
                    if current_state == "heavy" or (prev_state == "heavy" and current_state == "normal"):
                        gotify_routes.append(route)
                        logger.debug(f"Including Normal priority route '{route_name}' in Gotify summary (traffic condition met)")
                    else:
                        logger.debug(f"Skipping Normal priority route '{route_name}' from Gotify summary (no traffic condition)")

            if gotify_routes:
                logger.info(f"Generating summary for {len(gotify_routes)} eligible routes...")
                summary = create_simple_summary(gotify_routes)

                try:
                    await send_gotify_notification("Traffic Summary", summary)
                    logger.info("Gotify notification sent successfully")
                except Exception as gotify_error:
                    logger.error(f"Failed to send Gotify notification: {gotify_error}")
            else:
                logger.info("No routes meet criteria for Gotify notification - skipping")
        elif route_data:
            logger.info("Route data available but Gotify not configured, skipping notification")

        logger.info("Completed processing all routes")

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
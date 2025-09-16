"""Discord webhook notification service for traffic alerts.

This module handles sending traffic condition notifications to Discord
channels via webhooks when significant traffic changes are detected.
"""

import os
import json
import asyncio
import logging
import sys
import random
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

# Gotify configuration for Android notifications
GOTIFY_URL = os.environ.get("GOTIFY_URL")
GOTIFY_TOKEN = os.environ.get("GOTIFY_TOKEN")
GOTIFY_PRIORITY = int(os.environ.get("GOTIFY_PRIORITY", "5")) if os.environ.get("GOTIFY_PRIORITY") else 5

# Claude API configuration
CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY")
CLAUDE_SUMMARY_STYLE = os.environ.get("CLAUDE_SUMMARY_STYLE", "Generate a traffic summary in a random conversational style.")

logger.info("Discord Notify module loaded")


# ---------------------
# Claude API helper for generating summaries
# ---------------------
async def generate_claude_summary(route_data: List[Dict]) -> str:
    """Generate a coherent traffic summary using Claude API"""
    if not CLAUDE_API_KEY:
        logger.warning("CLAUDE_API_KEY not configured, using simple summary")
        return create_simple_summary(route_data)

    try:
        # Calculate dynamic word limit: minimum 8 words per route + 20 extra for style
        min_words_per_route = 8
        style_overhead = 20
        word_limit = (len(route_data) * min_words_per_route) + style_overhead

        # Create traffic data summary for Claude
        traffic_summary = "\n".join([
            f"Route: {route['name']} | Status: {route['status']} | Delay: {route['delay']} min | Distance: {route['distance']:.1f} km"
            for route in route_data
        ])

        # Choose random style
        styles = [
            "Serious / Professional: Neutral, clear, news-anchor style",
            "Local News Reporter: Adds place-specific context, like 'In downtown traffic...'",
            "Comedian / Sarcastic: Adds jokes or exaggerations, e.g., 'Route A is basically a parking lot'",
            "Friendly Advice / Casual: Conversational tone, like a friend talking",
            "Trump-Style: Over-the-top, hyperbolic, self-referential speech patterns",
            "Morgan Freeman Narrator: Calm, dramatic, storytelling style",
            "Epic / Adventure Style: Makes traffic sound like a quest",
            "Fairy Tale / Fantasy: 'The dragons of congestion guard Route A'"
        ]

        chosen_style = random.choice(styles)

        prompt = f"""{CLAUDE_SUMMARY_STYLE}

Current style to use: {chosen_style}

Traffic Data:
{traffic_summary}

Create a {word_limit}-word summary that's engaging for text-to-speech. Avoid paragraph format - use short, punchy sentences."""

        headers = {
            "Content-Type": "application/json",
            "x-api-key": CLAUDE_API_KEY,
            "anthropic-version": "2023-06-01"
        }

        payload = {
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": word_limit + 50,  # Allow some buffer
            "messages": [
                {
                    "role": "user",
                    "content": prompt
                }
            ]
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.anthropic.com/v1/messages",
                json=payload,
                headers=headers,
                timeout=30
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise Exception(f"Claude API returned {resp.status}: {text}")

                result = await resp.json()
                summary = result["content"][0]["text"].strip()
                logger.info(f"Generated Claude summary ({len(summary.split())} words) in style: {chosen_style.split(':')[0]}")
                return summary

    except Exception as e:
        logger.error(f"Failed to generate Claude summary: {e}")
        return create_simple_summary(route_data)


def create_simple_summary(route_data: List[Dict]) -> str:
    """Fallback simple summary when Claude API is unavailable"""
    heavy_routes = [r for r in route_data if r['status'].lower() == 'heavy']
    normal_routes = [r for r in route_data if r['status'].lower() == 'normal']

    if heavy_routes and normal_routes:
        return f"Traffic update: {len(heavy_routes)} routes with delays, {len(normal_routes)} routes running normal."
    elif heavy_routes:
        return f"Traffic alert: {len(heavy_routes)} routes experiencing delays."
    else:
        return f"All {len(route_data)} routes showing normal traffic conditions."


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

                    route_data.append({
                        "name": name,
                        "status": current_state,
                        "delay": traffic["total_delay"],
                        "distance": traffic["distance_km"]
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

            # Generate formatted table
            if route_data:
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
                    "title": "Traffic Status Summary",
                    "color": 0x3498db,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "description": f"```\n{table_content}\n```"
                }

                async with session.post(WEBHOOK_URL, json={"embeds": [embed]}, timeout=10) as resp:
                    if resp.status not in (200, 204):
                        text = await resp.text()
                        logger.error(f"Failed to post traffic summary: {resp.status} - {text}")
                    else:
                        logger.info("Traffic summary table posted successfully")

            # Generate Claude summary for Gotify TTS
            if route_data and GOTIFY_URL and GOTIFY_TOKEN:
                logger.info("Generating Claude summary for Gotify notification...")
                claude_summary = await generate_claude_summary(route_data)

                try:
                    await send_gotify_notification("Traffic Summary", claude_summary)
                    logger.info("Gotify notification with Claude summary sent successfully")
                except Exception as gotify_error:
                    logger.error(f"Failed to send Gotify notification: {gotify_error}")

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
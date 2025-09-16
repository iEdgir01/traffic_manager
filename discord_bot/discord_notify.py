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

# Import balance tracker
try:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'balance_tracker'))
    from balance_tracker import ClaudeBalanceTracker
    BALANCE_TRACKING_AVAILABLE = True
except ImportError as e:
    logging.warning(f"Balance tracking not available: {e}")
    BALANCE_TRACKING_AVAILABLE = False
    ClaudeBalanceTracker = None

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

# Initialize balance tracker
balance_tracker = None
if BALANCE_TRACKING_AVAILABLE and CLAUDE_API_KEY:
    try:
        balance_tracker = ClaudeBalanceTracker()
        logger.info("Balance tracker initialized")
    except Exception as e:
        logger.error(f"Failed to initialize balance tracker: {e}")
        balance_tracker = None

logger.info("Discord Notify module loaded")


# ---------------------
# Claude API helper for generating summaries
# ---------------------
async def generate_claude_summary(route_data: List[Dict]) -> str:
    """Generate a coherent traffic summary using Claude API"""
    if not CLAUDE_API_KEY:
        logger.warning("CLAUDE_API_KEY not configured, using simple summary")
        return create_simple_summary(route_data)

    # Check balance before making request
    if balance_tracker:
        can_make_request, reason = balance_tracker.can_make_request()
        if not can_make_request:
            logger.warning(f"Claude API request blocked: {reason}")
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

        prompt = f"""You are a creative traffic reporter providing a comprehensive traffic update. Create an engaging summary using the EXACT style specified below.

REQUIRED STYLE: {chosen_style}

TRAFFIC DATA TO SUMMARIZE:
{traffic_summary}

REQUIREMENTS:
- Use approximately {word_limit} words
- Mention ALL route names from the data above
- Include both delayed and normal routes in your summary
- Use the exact personality style specified
- Make it engaging for text-to-speech (TTS)
- Use short, punchy sentences (no paragraphs)
- Be creative and entertaining while staying factual
- Provide a complete traffic picture, not just problems

Example styles:
- Sarcastic: "Well folks, Highway-101 decided to become a parking lot with 15 minutes of delays, while Main-Street is actually behaving itself today."
- Morgan Freeman: "And so it was, that Highway-101 tested the patience of travelers with delays, while Main-Street flowed like a gentle river."
- Epic Adventure: "Today's quest reveals Highway-101 guarded by dragons of delay, while Main-Street offers safe passage to brave commuters."

Create your summary now using the {chosen_style.split(':')[0]} style:"""

        headers = {
            "Content-Type": "application/json",
            "x-api-key": CLAUDE_API_KEY,
            "anthropic-version": "2023-06-01"
        }

        payload = {
            "model": "claude-3-7-sonnet-20250219",
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

                # Track usage if balance tracker is available
                if balance_tracker:
                    cost = balance_tracker.track_claude_usage(result)
                    logger.info(f"Claude usage tracked: ${cost}")

                logger.info(f"Generated Claude summary ({len(summary.split())} words) in style: {chosen_style.split(':')[0]}")
                return summary

    except Exception as e:
        logger.error(f"Failed to generate Claude summary: {e}")
        return create_simple_summary(route_data)


def create_simple_summary(route_data: List[Dict]) -> str:
    """Fallback simple summary when Claude API is unavailable"""
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

        # Always generate Claude summary for Gotify TTS (regardless of traffic state)
        if route_data and GOTIFY_URL and GOTIFY_TOKEN:
            logger.info("Generating Claude summary for Gotify notification...")
            claude_summary = await generate_claude_summary(route_data)

            try:
                await send_gotify_notification("Traffic Summary", claude_summary)
                logger.info("Gotify notification with Claude summary sent successfully")
            except Exception as gotify_error:
                logger.error(f"Failed to send Gotify notification: {gotify_error}")
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
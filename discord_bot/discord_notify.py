import os
import json
import asyncio
from datetime import datetime
import aiohttp
from traffic_utils import (
    with_db, summarize_segments, get_routes,
    calculate_baseline, check_route_traffic, update_route_time
)

WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
if not WEBHOOK_URL:
    raise ValueError("Missing environment variable: DISCORD_WEBHOOK_URL")


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
        return row[0] if row else None


@with_db
def update_last_state(route_id, state, conn=None):
    with conn.cursor() as cur:
        cur.execute("UPDATE routes SET last_state = %s WHERE id = %s", (state, route_id))
        conn.commit()


# ---------------------
# Async traffic alert posting
# ---------------------
async def post_traffic_alerts_async():
    try:
        print("TRAFFIC: Starting processing of all routes...")
        routes = await run_in_thread(get_routes)

        if not routes:
            print("TRAFFIC: No routes found in the database.")
            return

        alerts_posted = 0
        print(f"TRAFFIC: Total routes to process: {len(routes)}\n")

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

                    print(f"TRAFFIC: Processing route '{name}'")

                    historical_data = json.loads(historical_json) if historical_json else []
                    baseline = calculate_baseline(historical_data)
                    print(f"TRAFFIC: Baseline calculated for {name}")

                    # Run traffic check in a thread (blocking function)
                    traffic = await run_in_thread(
                        check_route_traffic,
                        f"{start_lat},{start_lng}",
                        f"{end_lat},{end_lng}",
                        baseline
                    )
                    if not traffic:
                        print(f"TRAFFIC: No traffic data returned for {name}")
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
                        embed = {
                            "title": f"Traffic Status - {name}",
                            "color": color,
                            "timestamp": datetime.utcnow().isoformat(),
                            "fields": [
                                {"name": "State", "value": current_state, "inline": True},
                                {"name": "Distance", "value": f"{traffic['distance_km']:.2f} km", "inline": True},
                                {"name": "Live Time", "value": f"{traffic['total_live']} min", "inline": True},
                                {"name": "Normal Time", "value": f"{traffic['total_normal']} min", "inline": True},
                                {"name": "Delay", "value": f"{traffic['total_delay']} min", "inline": True},
                                {"name": "Heavy Segments", "value": summarize_segments(traffic['heavy_segments']) or 'None', "inline": False},
                            ]
                        }

                        async with session.post(WEBHOOK_URL, json={"embeds": [embed]}, timeout=10) as resp:
                            if resp.status not in (200, 204):
                                text = await resp.text()
                                print(f"❌ Failed to post alert for {name}: {resp.status} - {text}")
                            else:
                                print(f"✅ Alert posted for {name}")
                                alerts_posted += 1

                    # Update DB asynchronously
                    await run_in_thread(update_route_time, route_id, traffic["total_normal"], current_state)
                    await run_in_thread(update_last_state, route_id, current_state)

                except Exception as e:
                    print(f"❌ Error processing route '{route.get('name','Unknown')}': {e}")

        if alerts_posted == 0:
            print("TRAFFIC: No traffic alerts were necessary")
        else:
            print(f"TRAFFIC: Completed processing. {alerts_posted} alert(s) posted")

    except Exception as e:
        print(f"ERROR: Traffic processing failed: {e}")


# ---------------------
# Run standalone
# ---------------------
if __name__ == "__main__":
    print("Starting traffic alert processing...")
    asyncio.run(post_traffic_alerts_async())
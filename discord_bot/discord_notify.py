import os
import json
import discord
import requests
from traffic_utils import (
    with_db, summarize_segments, init_db, get_routes,
    calculate_baseline, check_route_traffic, update_route_time
)

WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
if not WEBHOOK_URL:
    raise ValueError("Missing environment variable: DISCORD_WEBHOOK_URL")


# ---------------------
# DB helpers
# ---------------------
@with_db
def get_last_state(route_id, conn=None):
    cursor = conn.cursor()
    cursor.execute("SELECT last_state FROM routes WHERE id = ?", (route_id,))
    row = cursor.fetchone()
    return row[0] if row else None


@with_db
def update_last_state(route_id, state, conn=None):
    cursor = conn.cursor()
    cursor.execute("UPDATE routes SET last_state = ? WHERE id = ?", (state, route_id))
    conn.commit()


# ---------------------
# Traffic check & posting
# ---------------------
def post_traffic_alerts():
    """
    Fetches all routes, checks traffic, and posts alerts to Discord.
    Logs meaningful messages while processing, including progress.
    """
    try:
        print("TRAFFIC: Starting processing of all routes...")
        results = get_routes()
        if not results:
            print("TRAFFIC: No routes found in the database.")
            return

        alerts_posted = 0
        print(f"TRAFFIC: Total routes to process: {len(results)}\n")

        for route in results:
            try:
                route_id, name, start_lat, start_lng, end_lat, end_lng, last_normal, last_state, historical_json = route
                print(f"TRAFFIC: Processing route '{name}'")

                # Calculate baseline
                baseline = calculate_baseline([] if not historical_json else json.loads(historical_json))
                print(f"TRAFFIC: Baseline calculated for {name}")

                # Check traffic
                traffic = check_route_traffic(f"{start_lat},{start_lng}", f"{end_lat},{end_lng}", baseline)
                if not traffic:
                    print(f"TRAFFIC: No traffic data returned for {name}")
                    continue

                current_state = traffic["state"]
                prev_state = get_last_state(route_id)

                # Determine if alert should be posted
                should_post = False
                if current_state.lower() == "heavy":
                    should_post = True
                elif prev_state and prev_state.lower() == "heavy" and current_state.lower() == "normal":
                    should_post = True

                if should_post:
                    color = 0x00FF00 if current_state.lower() == "normal" else 0xFF0000
                    embed = discord.Embed(
                        title=f"Traffic Status - {name}",
                        color=color,
                        timestamp=discord.utils.utcnow()
                    )
                    embed.add_field(name="State", value=current_state, inline=True)
                    embed.add_field(name="Distance", value=f"{traffic['distance_km']:.2f} km", inline=True)
                    embed.add_field(name="Live Time", value=f"{traffic['total_live']} min", inline=True)
                    embed.add_field(name="Normal Time", value=f"{traffic['total_normal']} min", inline=True)
                    embed.add_field(name="Delay", value=f"{traffic['total_delay']} min", inline=True)
                    segments_summary = summarize_segments(traffic['heavy_segments']) or 'None'
                    embed.add_field(name="Heavy Segments", value=segments_summary, inline=False)

                    payload = {"embeds": [embed.to_dict()]}
                    response = requests.post(WEBHOOK_URL, json=payload, timeout=10)
                    if response.status_code not in (200, 204):
                        print(f"❌ Failed to post alert for {name}: {response.status_code} - {response.text}")
                    else:
                        print(f"✅ Alert posted for {name}")
                        alerts_posted += 1

                # Update DB regardless of whether an alert was posted
                update_route_time(route_id, traffic["total_normal"], current_state)
                update_last_state(route_id, current_state)

            except Exception as e:
                print(f"❌ Error processing route '{route[1] if len(route) > 1 else 'Unknown'}': {e}")

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
    post_traffic_alerts()

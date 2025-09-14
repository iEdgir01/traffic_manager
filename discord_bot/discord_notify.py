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
    """Fetch last known state from DB"""
    cursor = conn.cursor()
    cursor.execute("SELECT last_state FROM routes WHERE id = ?", (route_id,))
    row = cursor.fetchone()
    return row[0] if row else None

@with_db
def update_last_state(route_id, state, conn=None):
    """Update last_state in DB"""
    cursor = conn.cursor()
    cursor.execute("UPDATE routes SET last_state = ? WHERE id = ?", (state, route_id))
    conn.commit()

# ---------------------
# Traffic check & posting
# ---------------------
def post_traffic_alerts():
    """
    Posts traffic alerts to Discord using check_route_traffic() and matching bot format.
    Includes detailed debug logging for each route.
    """
    init_db()
    routes = get_routes()
    if not routes:
        print("No routes found.")
        return

    alerts_posted = 0

    print("=== Starting Discord traffic alert check ===")
    print(f"Total routes to check: {len(routes)}\n")

    for route in routes:
        try:
            route_id, name, start_lat, start_lng, end_lat, end_lng, last_normal, last_state, historical_json = route

            print(f"--- Checking route: {name} ---")
            print(f"Start: {start_lat},{start_lng} | End: {end_lat},{end_lng}")

            # Get baseline
            baseline = calculate_baseline([] if not historical_json else json.loads(historical_json))
            print(f"[DEBUG] Baseline (historical normal time): {baseline}")

            # Traffic check
            traffic = check_route_traffic(f"{start_lat},{start_lng}", f"{end_lat},{end_lng}", baseline)
            
            if not traffic:
                print(f"[DEBUG] No traffic data returned for {name}. Possible API/network issue.")
                continue

            current_state = traffic["state"]
            prev_state = get_last_state(route_id)

            print(f"[DEBUG] Previous state: {prev_state}")
            print(f"[DEBUG] Current state: {current_state}")
            print(f"[DEBUG] Total normal: {traffic.get('total_normal')}, Total live: {traffic.get('total_live')}, Total delay: {traffic.get('total_delay')}")
            print(f"[DEBUG] Heavy segments: {traffic.get('heavy_segments')}\n")

            should_post = False

            # Posting rules
            if current_state.lower() == "heavy":
                should_post = True
                print(f"[DEBUG] Decision: Heavy traffic → will post alert")
            elif prev_state and prev_state.lower() == "heavy" and current_state.lower() == "normal":
                should_post = True
                print(f"[DEBUG] Decision: Traffic cleared → will post alert")
            else:
                print(f"[DEBUG] Decision: No alert needed for {name}")

            if should_post:
                color = 0x00FF00 if current_state == "Normal" else 0xFF0000
                state_text = current_state

                embed = discord.Embed(
                    title=f"Traffic Status - {name}",
                    color=color,
                    timestamp=discord.utils.utcnow()
                )
                embed.add_field(name="State", value=state_text, inline=True)
                embed.add_field(name="Distance", value=f"{traffic['distance_km']:.2f} km", inline=True)
                embed.add_field(name="Live Time", value=f"{traffic['total_live']} min", inline=True)
                embed.add_field(name="Normal Time", value=f"{traffic['total_normal']} min", inline=True)
                embed.add_field(name="Delay", value=f"{traffic['total_delay']} min", inline=True)

                segments_summary = summarize_segments(traffic['heavy_segments']) or 'None'
                embed.add_field(name="Heavy Segments", value=segments_summary, inline=False)

                # Post to Discord
                payload = {"embeds": [embed.to_dict()]}
                response = requests.post(WEBHOOK_URL, json=payload, timeout=10)

                if response.status_code not in (200, 204):
                    print(f"❌ Failed to post alert for {name}: {response.status_code} - {response.text}")
                else:
                    print(f"✅ Alert posted for {name}")
                    alerts_posted += 1

            # Update database
            update_route_time(route_id, traffic["total_normal"], current_state)
            update_last_state(route_id, current_state)

        except Exception as e:
            print(f"❌ Error processing route {route[1] if len(route) > 1 else 'Unknown'}: {e}")

    if alerts_posted == 0:
        print("TRAFFIC: No traffic alerts to send")
    else:
        print(f"TRAFFIC: Posted {alerts_posted} alert(s)")

# ---------------------
# Run standalone
# ---------------------
if __name__ == "__main__":
    print("Starting traffic alert check...")
    post_traffic_alerts()
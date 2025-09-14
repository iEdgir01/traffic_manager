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

def post_traffic_alerts():
    """
    Posts traffic alerts to Discord using check_route_traffic() and matching bot format.
    """
    init_db()
    routes = get_routes()
    if not routes:
        print("No routes found.")
        return
    
    alerts_posted = 0
    
    for route in routes:
        try:
            route_id, name, start_lat, start_lng, end_lat, end_lng, last_normal, last_state, historical_json = route
            
            # Get baseline the same way as the bot
            baseline = calculate_baseline([] if not historical_json else json.loads(historical_json))
            
            # Use the SAME function as manual checks
            traffic = check_route_traffic(f"{start_lat},{start_lng}", f"{end_lat},{end_lng}", baseline)
            
            if not traffic:
                print(f"⚠️  No traffic data for {name}")
                continue
            
            current_state = traffic["state"]
            prev_state = get_last_state(route_id)
            should_post = False
            
            # Posting rules
            if current_state.lower() == "heavy":
                should_post = True
                print(f"🔴 Posting {name}: Heavy traffic detected")
            elif prev_state and prev_state.lower() == "heavy" and current_state.lower() == "normal":
                should_post = True
                print(f"🟢 Posting {name}: Traffic cleared")
            
            if should_post:
                # Use the EXACT same format as your bot
                if "error" in traffic:
                    color = 0xFF0000  # Red for error
                    state_text = "Error"
                else:
                    color = 0x00FF00 if traffic['state'] == 'Normal' else 0xFF0000  # Green/Red
                    state_text = "Normal" if traffic['state'] == 'Normal' else "Heavy"
                
                embed = discord.Embed(
                    title=f"Traffic Status - {name}",
                    color=color,
                    timestamp=discord.utils.utcnow()
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
                
                # Post to Discord
                payload = {"embeds": [embed.to_dict()]}
                response = requests.post(WEBHOOK_URL, json=payload, timeout=10)
                
                if response.status_code not in (200, 204):
                    print(f"❌ Failed to post alert for {name}: {response.status_code} - {response.text}")
                else:
                    print(f"✅ Alert posted for {name}")
                    alerts_posted += 1
            else:
                print(f"ℹ️  No alert needed for {name}: {current_state} traffic")
            
            # Update database
            update_route_time(route_id, traffic["total_normal"], current_state)
            update_last_state(route_id, current_state)
            
        except Exception as e:
            print(f"❌ Error processing route {route[1] if len(route) > 1 else 'Unknown'}: {e}")
    
    if alerts_posted == 0:
        print("TRAFFIC: No traffic alerts to send")
    else:
        print(f"TRAFFIC: Posted {alerts_posted} alert(s)")

# For backwards compatibility - this is what your main script calls
def process_all_routes_for_discord():
    """
    Wrapper function for backwards compatibility.
    Your existing code can still call this.
    """
    return post_traffic_alerts()

if __name__ == "__main__":
    # Can be run directly for testing
    print("Starting traffic alert check...")
    post_traffic_alerts()
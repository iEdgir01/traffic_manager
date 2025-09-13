import os
import json
import discord
import requests
from traffic_utils import DB_PATH, with_db, summarize_segments

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


def format_embed(route_result):
    """Convert a route result into a Discord Embed"""
    state = route_result["state"]
    embed_color = 0x00FF00 if state.lower() == "normal" else 0xFF0000

    embed = discord.Embed(
        title=f"Traffic Status - {route_result['name']}",
        color=embed_color,
        timestamp=discord.utils.utcnow()
    )

    embed.add_field(name="State", value=state, inline=True)
    embed.add_field(name="Distance", value=route_result["distance"], inline=True)
    embed.add_field(name="Live Time", value=route_result["live"], inline=True)
    embed.add_field(name="Delay", value=route_result["delay"], inline=True)

    segments_summary = summarize_segments(route_result.get("heavy_segments")) or "None"
    embed.add_field(name="Heavy Segments", value=segments_summary, inline=False)

    # Optional: add a map URL if available
    if route_result.get("map_url"):
        embed.set_image(url=route_result["map_url"])

    return embed


def post_traffic_alerts(results):
    """
    Posts traffic alerts to Discord if rules are met:
    - If state == 'Heavy'
    - If previous state was 'Heavy' and now 'Normal'
    """
    if not results:
        print("No traffic results to process.")
        return

    for r in results:
        try:
            route_id = r["route_id"]
            current_state = r["state"]
            prev_state = get_last_state(route_id)

            post = False
            if current_state.lower() == "heavy":
                post = True
            elif prev_state and prev_state.lower() == "heavy" and current_state.lower() == "normal":
                post = True

            if post:
                embed = format_embed(r)

                files = None
                if r.get("map_path") and os.path.isfile(r["map_path"]):
                    # Attach local file
                    files = {"file": open(r["map_path"], "rb")}
                    # For embeds, set image url to "attachment://filename"
                    embed.set_image(url=f"attachment://{os.path.basename(r['map_path'])}")

                payload = {"embeds": [embed.to_dict()]}

                response = requests.post(WEBHOOK_URL, data={"payload_json": json.dumps(payload)}, files=files, timeout=10)
                if response.status_code not in (200, 204):
                    print(f"❌ Failed to post alert for {r['name']}: {response.status_code} - {response.text}")
                else:
                    print(f"✅ Alert posted for {r['name']}")

                # Close the file handle if used
                if files:
                    files["file"].close()

            update_last_state(route_id, current_state)

        except Exception as e:
            print(f"❌ Error processing route {r.get('name', 'Unknown')}: {e}")
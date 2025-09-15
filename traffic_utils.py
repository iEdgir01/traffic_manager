import os
import json
import psycopg2
import psycopg2.extras
from pathlib import Path
from datetime import datetime
import requests
import html
import re

# ---------------------
# Environment variables
# ---------------------
API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")
DATA_DIR = Path(os.getenv("DATA_DIR"))
MAPS_DIR = Path(os.getenv("MAPS_DIR"))

# PostgreSQL connection parameters
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "traffic_db")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD")

# Ensure directories exist
DATA_DIR.mkdir(parents=True, exist_ok=True)
MAPS_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------
# DB Connection
# ---------------------
def get_db_connection():
    """Get a PostgreSQL database connection"""
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        cursor_factory=psycopg2.extras.RealDictCursor
    )

def with_db(fn):
    """
    Decorator that injects a managed PostgreSQL connection.
    Commits automatically and ensures cleanup.
    """
    def wrapper(*args, **kwargs):
        with get_db_connection() as conn:
            try:
                result = fn(*args, conn=conn, **kwargs)
                conn.commit()
                return result
            except Exception:
                conn.rollback()
                raise
    return wrapper

# ---------------------
# DB helpers
# ---------------------
@with_db
def init_db(conn=None):
    with conn.cursor() as c:
        c.execute('''
            CREATE TABLE IF NOT EXISTS routes (
                id SERIAL PRIMARY KEY,
                name TEXT UNIQUE,
                start_lat REAL,
                start_lng REAL,
                end_lat REAL,
                end_lng REAL,
                last_normal_time INTEGER,
                last_state TEXT,
                historical_times TEXT
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS config (
                id SERIAL PRIMARY KEY,
                name TEXT UNIQUE,
                value TEXT
            )
        ''')

@with_db
def get_routes(conn=None):
    with conn.cursor() as c:
        c.execute('SELECT * FROM routes')
        return c.fetchall()

@with_db
def update_route_time(route_id, normal_time, state, conn=None):
    with conn.cursor() as c:
        if route_id is None:
            return
            
        c.execute('SELECT historical_times FROM routes WHERE id=%s', (route_id,))
        row = c.fetchone()
        historical = json.loads(row['historical_times']) if row and row['historical_times'] else []

        entry = {"timestamp": datetime.now().isoformat(), "normal_time": normal_time, "state": state}
        historical.append(entry)
        historical = historical[-20:]  # keep last 20 entries

        c.execute(
            'UPDATE routes SET last_normal_time=%s, last_state=%s, historical_times=%s WHERE id=%s',
            (normal_time, state, json.dumps(historical), route_id)
        )

@with_db
def add_route(name, start_lat, start_lng, end_lat, end_lng, conn=None):
    with conn.cursor() as c:
        c.execute("""
            INSERT INTO routes (name, start_lat, start_lng, end_lat, end_lng, last_normal_time, last_state, historical_times)
            VALUES (%s, %s, %s, %s, %s, NULL, 'Normal', '[]')
        """, (name, start_lat, start_lng, end_lat, end_lng))

@with_db
def delete_route(name, conn=None):
    with conn.cursor() as c:
        c.execute("DELETE FROM routes WHERE name=%s", (name,))

def calculate_baseline(historical_times):
    if not historical_times:
        return None
    times = [entry["normal_time"] for entry in historical_times if "normal_time" in entry]
    return sum(times) / len(times) if times else None

# ---------------------
# Thresholds (config table)
# ---------------------
@with_db
def get_config(name, conn=None):
    with conn.cursor() as c:
        c.execute('SELECT value FROM config WHERE name=%s', (name,))
        row = c.fetchone()
        return json.loads(row['value']) if row and row['value'] else None

@with_db
def set_config(name, value, conn=None):
    with conn.cursor() as c:
        json_value = json.dumps(value)
        c.execute('''
            INSERT INTO config (name, value) VALUES (%s, %s)
            ON CONFLICT(name) DO UPDATE SET value=%s
        ''', (name, json_value, json_value))

# Default thresholds
DEFAULT_THRESHOLDS = [
    {"min_km": 0, "max_km": 2, "factor_total": 3, "factor_step": 1, "delay_total": 5, "delay_step": 1},
    {"min_km": 2, "max_km": 5, "factor_total": 2.5, "factor_step": 2, "delay_total": 10, "delay_step": 2},
    {"min_km": 5, "max_km": 20, "factor_total": 2, "factor_step": 3, "delay_total": 15, "delay_step": 5},
    {"min_km": 20, "max_km": 50, "factor_total": 1.5, "factor_step": 4, "delay_total": 30, "delay_step": 10}
]

def get_thresholds():
    thresholds = get_config("thresholds")
    if not thresholds:
        set_config("thresholds", DEFAULT_THRESHOLDS)
        thresholds = DEFAULT_THRESHOLDS
    return thresholds

def set_thresholds(thresholds):
    set_config("thresholds", thresholds)

def reset_thresholds():
    set_config("thresholds", DEFAULT_THRESHOLDS)

def get_dynamic_thresholds(distance_km):
    thresholds = get_thresholds()
    for t in thresholds:
        if t["min_km"] <= distance_km <= t["max_km"]:
            return t["factor_total"], t["factor_step"], t["delay_total"], t["delay_step"]
    return thresholds[-1]["factor_total"], thresholds[-1]["factor_step"], thresholds[-1]["delay_total"], thresholds[-1]["delay_step"]

# ---------------------
# Traffic checking
# ---------------------
def check_route_traffic(origin, destination, baseline=None):
    resp = requests.get(
        "https://maps.googleapis.com/maps/api/directions/json",
        params={
            "origin": origin,
            "destination": destination,
            "departure_time": "now",
            "alternatives": "true",
            "key": API_KEY
        }
    )

    try:
        data = resp.json()
    except Exception:
        print("Invalid response:", resp.text)
        return None

    if not data.get("routes"):
        return None

    # Pick the fastest route
    fastest = min(data["routes"], key=lambda r: r["legs"][0]["duration_in_traffic"]["value"])
    leg = fastest["legs"][0]

    total_normal = leg["duration"]["value"] // 60
    total_live = leg["duration_in_traffic"]["value"] // 60
    total_delay = max(0, total_live - total_normal)
    distance_km = leg["distance"]["value"] / 1000

    # Baseline (historical or fallback)
    effective_normal = baseline if baseline else total_normal

    # Thresholds
    factor_total, factor_step, delay_total, delay_step = get_dynamic_thresholds(distance_km)

    # -----------------------
    # Heavy traffic detection
    # -----------------------
    is_heavy = False
    heavy_segments = []

    # a) Route-level check
    if total_delay >= delay_total:
        is_heavy = True
    elif effective_normal > 0 and total_live >= effective_normal * factor_total:
        is_heavy = True

    # b) Step-level check
    for step in leg["steps"]:
        if "duration_in_traffic" not in step:
            continue
        normal = step["duration"]["value"] // 60
        live = step["duration_in_traffic"]["value"] // 60
        delay = max(0, live - normal)

        if normal == 0:
            continue

        if delay >= delay_step or live >= normal * factor_step:
            is_heavy = True
            heavy_segments.append({
                "instruction": html.unescape(re.sub(r"<.*?>", "", step.get("html_instructions", ""))),
                "normal": normal,
                "live": live,
                "delay": delay,
                "factor": round(live / normal, 2)
            })

    state = "Heavy" if is_heavy else "Normal"

    return {
        "summary": fastest.get("summary"),
        "start_address": leg.get("start_address"),
        "end_address": leg.get("end_address"),
        "total_normal": total_normal,
        "total_live": total_live,
        "total_delay": total_delay,
        "distance_km": distance_km,
        "heavy_segments": heavy_segments,
        "state": state
    }

def summarize_segments(segments, limit=4):
    if not segments:
        return ""
    lines = [f"- {s['instruction']}: {s['normal']}→{s['live']} (+{s['delay']}m)" for s in segments[:limit]]
    if len(segments) > limit:
        lines.append(f"...and {len(segments)-limit} more segments")
    return "\n".join(lines)

# ---------------------
# Process all routes
# ---------------------
def process_all_routes(include_segments=False):
    """
    Processes all routes, updates DB, and returns results.
    Set include_segments=True if you want heavy_segments in the result.
    """
    init_db()
    routes = get_routes()
    if not routes:
        print("No routes found.")
        return []

    results = []
    for route in routes:
        route_id, name, start_lat, start_lng, end_lat, end_lng = route['id'], route['name'], route['start_lat'], route['start_lng'], route['end_lat'], route['end_lng']
        last_normal, last_state, historical_json = route.get('last_normal_time'), route.get('last_state'), route.get('historical_times')
        
        historical_times = json.loads(historical_json) if historical_json else []
        baseline = calculate_baseline(historical_times)

        traffic = check_route_traffic(f"{start_lat},{start_lng}", f"{end_lat},{end_lng}", baseline)
        if not traffic:
            continue

        result = {
            "route_id": route_id,
            "name": name,
            "state": traffic["state"],
            "distance": f"{traffic['distance_km']:.2f} km",
            "live": f"{traffic['total_live']} min",
            "delay": f"+{traffic['total_delay']} min",
            "total_normal": traffic["total_normal"]
        }

        if include_segments:
            result["heavy_segments"] = traffic["heavy_segments"]

        results.append(result)

    # ---------------------
    # Update DB
    # ---------------------
    for r in results:
        try:
            update_route_time(r["route_id"], r["total_normal"], r["state"])
        except Exception as e:
            print(f"Failed to update route {r['name']} in DB: {e}")

    return results

# ---------------------
# Map generation
# ---------------------
def get_route_map(route_name, start_lat, start_lng, end_lat, end_lng):
    """
    Generate a road-following map PNG for a route.
    Saves to ./data/maps/{route_name}.png
    """
    map_path = os.path.join(MAPS_DIR, f"{route_name}.png")

    # Skip if already exists
    if os.path.exists(map_path):
        return map_path
    api_key = os.getenv("GOOGLE_MAPS_API_KEY")
    if not api_key:
        raise ValueError("Google Maps API key not set in GOOGLE_MAPS_API_KEY")

    # Get directions
    directions_url = "https://maps.googleapis.com/maps/api/directions/json"
    params = {
        "origin": f"{start_lat},{start_lng}",
        "destination": f"{end_lat},{end_lng}",
        "mode": "driving",
        "key": api_key
    }
    resp = requests.get(directions_url, params=params)
    if resp.status_code != 200:
        raise RuntimeError(f"Directions API failed {resp.status_code}")
    data = resp.json()
    if data.get("status") != "OK":
        raise RuntimeError(f"Directions API error: {data.get('status')}")
    encoded_poly = data["routes"][0]["overview_polyline"]["points"]
    
    # Build static map URL
    static_url = "https://maps.googleapis.com/maps/api/staticmap"
    url = (
        f"{static_url}?size=800x400&maptype=roadmap"
        f"&path=enc:{encoded_poly}"
        f"&markers=color:green|label:S|{start_lat},{start_lng}"
        f"&markers=color:red|label:E|{end_lat},{end_lng}"
        f"&key={api_key}"
    )

    #Fetch static map
    r = requests.get(url)
    if r.status_code != 200:
        raise RuntimeError(f"Static Maps API failed {r.status_code}")
    with open(map_path, "wb") as f:
        f.write(r.content)
    return map_path

# ---------------------
# CLI
# ---------------------
if __name__ == "__main__":
    process_all_routes()
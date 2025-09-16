"""Traffic monitoring utilities and database operations."""

import os
import json
import html
import re
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Tuple

import psycopg2
import psycopg2.extras
import requests

# ---------------------
# Environment variables
# ---------------------
API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")
DATA_DIR = Path(os.getenv("DATA_DIR"))
MAPS_DIR = Path(os.getenv("MAPS_DIR"))

# PostgreSQL connection parameters
POSTGRES_HOST = os.getenv("POSTGRES_HOST")
POSTGRES_PORT = os.getenv("POSTGRES_PORT") 
POSTGRES_USER = os.getenv("POSTGRES_USER")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD")
POSTGRES_DB = os.getenv("POSTGRES_DB")

# Ensure directories exist
DATA_DIR.mkdir(parents=True, exist_ok=True)
MAPS_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------
# DB Connection
# ---------------------
def get_db_connection() -> psycopg2.extensions.connection:
    """Get a PostgreSQL database connection."""
    return psycopg2.connect(
        host=POSTGRES_HOST,
        port=POSTGRES_PORT,
        database=POSTGRES_DB,
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
        cursor_factory=psycopg2.extras.RealDictCursor
    )

def with_db(func):
    """
    Decorator that injects a managed PostgreSQL connection.
    Commits automatically and ensures cleanup.

    Args:
        func: Function to decorate

    Returns:
        Wrapped function with database connection management
    """
    def wrapper(*args, **kwargs):
        with get_db_connection() as conn:
            try:
                result = func(*args, conn=conn, **kwargs)
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
def init_db(conn=None) -> None:
    """Initialize the database tables and apply migrations.

    Args:
        conn: Database connection (injected by decorator)
    """
    with conn.cursor() as cursor:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS routes (
                id SERIAL PRIMARY KEY,
                name TEXT UNIQUE,
                start_lat REAL,
                start_lng REAL,
                end_lat REAL,
                end_lng REAL,
                last_normal_time INTEGER,
                last_state TEXT,
                historical_times TEXT,
                priority VARCHAR(10) DEFAULT 'Normal' CHECK (priority IN ('High', 'Normal'))
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS config (
                id SERIAL PRIMARY KEY,
                name TEXT UNIQUE,
                value TEXT
            )
        ''')

    # Apply database migrations for existing installations
    try:
        from migrations import migrate_database
        migrate_database(conn)
    except ImportError:
        logger.warning("Migration module not found, skipping migrations")

@with_db
def get_routes(conn=None) -> List[Dict[str, Any]]:
    """Get all routes from the database with coordinate type casting.

    Args:
        conn: Database connection (injected by decorator)

    Returns:
        List of route dictionaries with float coordinates
    """
    with conn.cursor() as cursor:
        cursor.execute('SELECT * FROM routes')
        rows = cursor.fetchall()
        # Explicitly cast coordinates to float for each route
        for row in rows:
            row['start_lat'] = float(row['start_lat'])
            row['start_lng'] = float(row['start_lng'])
            row['end_lat'] = float(row['end_lat'])
            row['end_lng'] = float(row['end_lng'])
        return rows

@with_db
def get_route_priority(route_name: str, conn=None) -> str:
    """Get priority level for a specific route by name.

    Args:
        route_name: Name of the route
        conn: Database connection (injected by decorator)

    Returns:
        Priority level ('High' or 'Normal'), defaults to 'Normal' if not found
    """
    with conn.cursor() as cursor:
        cursor.execute('SELECT priority FROM routes WHERE name = %s', (route_name,))
        row = cursor.fetchone()
        return row['priority'] if row else 'Normal'

@with_db
def update_route_time(route_id: Optional[int], normal_time: int, state: str, conn=None) -> None:
    """Update route's traffic timing and historical data.

    Args:
        route_id: Route database ID
        normal_time: Normal travel time in minutes
        state: Traffic state ('Normal', 'Heavy', etc.)
        conn: Database connection (injected by decorator)
    """
    if route_id is None:
        return

    with conn.cursor() as cursor:
        cursor.execute('SELECT historical_times FROM routes WHERE id=%s', (route_id,))
        row = cursor.fetchone()
        historical = json.loads(row['historical_times']) if row and row['historical_times'] else []

        entry = {
            "timestamp": datetime.now().isoformat(),
            "normal_time": normal_time,
            "state": state
        }
        historical.append(entry)
        historical = historical[-20:]  # keep last 20 entries

        cursor.execute(
            'UPDATE routes SET last_normal_time=%s, last_state=%s, historical_times=%s WHERE id=%s',
            (normal_time, state, json.dumps(historical), route_id)
        )


@with_db
def add_route(name: str, start_lat: float, start_lng: float,
              end_lat: float, end_lng: float, priority: str = "Normal", conn=None) -> None:
    """Add a new route to the database.

    Args:
        name: Route name
        start_lat: Starting latitude
        start_lng: Starting longitude
        end_lat: Ending latitude
        end_lng: Ending longitude
        priority: Route priority ('High' or 'Normal')
        conn: Database connection (injected by decorator)
    """
    # Ensure the coordinates are floats
    start_lat = float(start_lat)
    start_lng = float(start_lng)
    end_lat = float(end_lat)
    end_lng = float(end_lng)

    with conn.cursor() as cursor:
        cursor.execute("""
            INSERT INTO routes (name, start_lat, start_lng, end_lat, end_lng,
                              last_normal_time, last_state, historical_times, priority)
            VALUES (%s, %s, %s, %s, %s, NULL, 'Normal', '[]', %s)
        """, (name, start_lat, start_lng, end_lat, end_lng, priority))

@with_db
def update_route_priority(name: str, priority: str, conn=None) -> None:
    """Update a route's priority.

    Args:
        name: Route name
        priority: New priority ('High' or 'Normal')
        conn: Database connection (injected by decorator)
    """
    with conn.cursor() as cursor:
        cursor.execute("""
            UPDATE routes SET priority = %s WHERE name = %s
        """, (priority, name))

@with_db
def delete_route(name: str, conn=None) -> None:
    """Delete a route from the database.

    Args:
        name: Route name to delete
        conn: Database connection (injected by decorator)
    """
    with conn.cursor() as cursor:
        cursor.execute("DELETE FROM routes WHERE name=%s", (name,))

def calculate_baseline(historical_times: List[Dict[str, Any]]) -> Optional[float]:
    """Calculate baseline travel time from historical data.

    Args:
        historical_times: List of historical traffic entries

    Returns:
        Average normal time in minutes, or None if no data
    """
    if not historical_times:
        return None
    times = [entry["normal_time"] for entry in historical_times if "normal_time" in entry]
    return sum(times) / len(times) if times else None

# ---------------------
# Thresholds (config table)
# ---------------------
@with_db
def get_config(name: str, conn=None) -> Optional[Any]:
    """Retrieve configuration value from database.

    Args:
        name: Configuration key name
        conn: Database connection (injected by decorator)

    Returns:
        Parsed JSON configuration value, or None if not found
    """
    with conn.cursor() as cursor:
        cursor.execute('SELECT value FROM config WHERE name=%s', (name,))
        row = cursor.fetchone()
        return json.loads(row['value']) if row and row['value'] else None

@with_db
def set_config(name: str, value: Any, conn=None) -> None:
    """Store configuration value in database.

    Args:
        name: Configuration key name
        value: Value to store (will be JSON serialized)
        conn: Database connection (injected by decorator)
    """
    with conn.cursor() as cursor:
        json_value = json.dumps(value)
        cursor.execute('''
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

def get_thresholds() -> List[Dict[str, Any]]:
    """Get traffic detection thresholds, creating defaults if needed.

    Returns:
        List of threshold dictionaries with min_km, max_km, factor_total,
        factor_step, delay_total, and delay_step keys
    """
    thresholds = get_config("thresholds")
    if not thresholds:
        set_config("thresholds", DEFAULT_THRESHOLDS)
        thresholds = DEFAULT_THRESHOLDS
    return thresholds

def set_thresholds(thresholds: List[Dict[str, Any]]) -> None:
    """Store traffic detection thresholds.

    Args:
        thresholds: List of threshold dictionaries to store
    """
    set_config("thresholds", thresholds)

def reset_thresholds() -> None:
    """Reset traffic detection thresholds to default values."""
    print("Resetting thresholds to defaults...")
    set_config("thresholds", DEFAULT_THRESHOLDS)
    print("Thresholds reset successfully")

def get_dynamic_thresholds(distance_km: float) -> Tuple[float, float, int, int]:
    """Get appropriate traffic thresholds for route distance.

    Args:
        distance_km: Route distance in kilometers

    Returns:
        Tuple of (factor_total, factor_step, delay_total, delay_step)
        for the matching distance range
    """
    thresholds = get_thresholds()
    for threshold in thresholds:
        if threshold["min_km"] <= distance_km <= threshold["max_km"]:
            return (threshold["factor_total"], threshold["factor_step"],
                   threshold["delay_total"], threshold["delay_step"])

    # Fallback to last threshold if no match found
    if thresholds:
        last_threshold = thresholds[-1]
        return (last_threshold["factor_total"], last_threshold["factor_step"],
                last_threshold["delay_total"], last_threshold["delay_step"])

    # Ultimate fallback if no thresholds exist
    return (2.0, 3.0, 15, 5)

# ---------------------
# Traffic checking
# ---------------------
def check_route_traffic(origin: str, destination: str, baseline: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Check current traffic conditions for a route using Google Maps API.

    Args:
        origin: Starting coordinate as "lat,lng" string
        destination: Ending coordinate as "lat,lng" string
        baseline: Historical baseline travel time in minutes (optional)

    Returns:
        Dictionary containing traffic analysis results with keys:
        - summary: Route description
        - start_address, end_address: Human-readable addresses
        - total_normal, total_live, total_delay: Travel times in minutes
        - distance_km: Route distance
        - heavy_segments: List of congested segments
        - state: "Heavy" or "Normal"

        Returns None if API call fails or no routes found.
    """
    if not API_KEY:
        raise ValueError("GOOGLE_MAPS_API_KEY environment variable not set")

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
    except Exception as exc:
        print(f"Invalid response from Google Maps API: {resp.text}")
        raise RuntimeError(f"Failed to parse Google Maps API response: {exc}") from exc

    if not data.get("routes"):
        if data.get("error_message"):
            raise RuntimeError(f"Google Maps API error: {data['error_message']}")
        return None

    # Filter routes that have traffic data
    routes_with_traffic = [r for r in data["routes"] if r["legs"][0].get("duration_in_traffic")]
    if not routes_with_traffic:
        raise RuntimeError("No routes with traffic data available")

    fastest = min(routes_with_traffic, key=lambda r: r["legs"][0]["duration_in_traffic"]["value"])
    leg = fastest["legs"][0]

    total_normal = leg["duration"]["value"] // 60
    total_live = leg["duration_in_traffic"]["value"] // 60
    total_delay = max(0, total_live - total_normal)
    distance_km = leg["distance"]["value"] / 1000

    effective_normal = baseline if baseline else total_normal

    factor_total, factor_step, delay_total, delay_step = get_dynamic_thresholds(distance_km)

    is_heavy = False
    heavy_segments = []

    if total_delay >= delay_total:
        is_heavy = True
    elif effective_normal > 0 and total_live >= effective_normal * factor_total:
        is_heavy = True

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

def summarize_segments(segments: List[Dict[str, Any]], limit: int = 4) -> str:
    """Create a formatted summary of heavy traffic segments.

    Args:
        segments: List of segment dictionaries from traffic analysis
        limit: Maximum number of segments to include in summary

    Returns:
        Formatted string summarizing heavy traffic segments,
        or empty string if no segments provided
    """
    if not segments:
        return ""
    lines = [f"- {s['instruction']}: {s['normal']}→{s['live']} (+{s['delay']}m)" for s in segments[:limit]]
    if len(segments) > limit:
        lines.append(f"...and {len(segments)-limit} more segments")
    return "\n".join(lines)

# ---------------------
# Process all routes
# ---------------------
def process_all_routes(include_segments: bool = False) -> List[Dict[str, Any]]:
    """Process traffic conditions for all routes and update database.

    Args:
        include_segments: Whether to include heavy_segments data in results

    Returns:
        List of dictionaries containing route traffic analysis results.
        Each dictionary contains route_id, name, state, distance, live time,
        delay, and total_normal time. If include_segments=True, also includes
        heavy_segments list.
    """
    init_db()
    routes = get_routes()
    if not routes:
        print("No routes found.")
        return []

    results = []
    for route in routes:
        route_id = route['id']
        name = route['name']
        start_lat = route['start_lat']
        start_lng = route['start_lng']
        end_lat = route['end_lat']
        end_lng = route['end_lng']
        historical_json = route.get('historical_times')

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

    for result in results:
        try:
            update_route_time(result["route_id"], result["total_normal"], result["state"])
        except Exception as exc:
            print(f"Failed to update route {result['name']} in DB: {exc}")

    return results

# ---------------------
# Map generation
# ---------------------
def get_route_map(route_name: str, start_lat: float, start_lng: float,
                  end_lat: float, end_lng: float) -> str:
    """Generate a road-following map PNG for a route using Google Maps APIs.

    Args:
        route_name: Name of the route for file naming
        start_lat: Starting latitude coordinate
        start_lng: Starting longitude coordinate
        end_lat: Ending latitude coordinate
        end_lng: Ending longitude coordinate

    Returns:
        Path to the generated map image file

    Raises:
        ValueError: If Google Maps API key is not configured
        RuntimeError: If Google Maps API calls fail
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
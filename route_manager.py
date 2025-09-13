import os
import sys
import sqlite3
import json
import re
from pathlib import Path

from traffic_utils import (
    init_db,
    get_routes,
    update_route_time,
    calculate_baseline,
    check_route_traffic,
    summarize_segments,
    get_thresholds,
    set_thresholds,
    reset_thresholds,
    get_route_map,
    process_all_routes
)

# ---------------------
# Paths from Docker environment
# ---------------------
DATA_DIR = Path(os.environ["DATA_DIR"])
MAPS_DIR = Path(os.environ["MAPS_DIR"])
DB_PATH = Path(os.environ["DB_PATH"])

# Ensure directories exist
DATA_DIR.mkdir(parents=True, exist_ok=True)
MAPS_DIR.mkdir(parents=True, exist_ok=True)

print(f"Using DB at: {DB_PATH}")

# ---------------------
# DB Connection Wrapper
# ---------------------
def with_db(fn):
    """
    Decorator that provides a managed sqlite3 connection.
    Ensures proper commit/rollback and closing of the DB.
    """
    def wrapper(*args, **kwargs):
        with sqlite3.connect(DB_PATH) as conn:
            return fn(*args, conn=conn, **kwargs)
    return wrapper

# ---------------------
# CLI Colors
# ---------------------
class Colors:
    RESET = "\033[0m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    CYAN = "\033[36m"
    BOLD = "\033[1m"

# ---------------------
# Helper: DMS parsing
# ---------------------
def dms_to_decimal(dms_str):
    """
    Convert a single DMS coordinate to decimal degrees.
    Strictly expects format: DD°MM'SS.S"D
    Example: 29°53'55.8"S
    """
    dms_str = dms_str.strip()
    # Regex to match strict DMS format
    match = re.fullmatch(r"(\d{1,3})°(\d{1,2})'(\d{1,2}(?:\.\d+)?)\"([NSEW])", dms_str, re.IGNORECASE)
    if not match:
        raise ValueError(f"Invalid DMS format: {dms_str}")
    
    degrees, minutes, seconds, direction = match.groups()
    decimal = float(degrees) + float(minutes)/60 + float(seconds)/3600
    if direction.upper() in ['S', 'W']:
        decimal *= -1
    return decimal

def parse_dms_pair(dms_pair):
    """
    Parse a coordinate pair separated by whitespace.
    Strict format: 29°53'55.8"S 30°58'34.3"E
    """
    parts = dms_pair.strip().split()
    if len(parts) != 2:
        raise ValueError(f"Invalid coordinate pair: {dms_pair}")
    
    lat = dms_to_decimal(parts[0])
    lng = dms_to_decimal(parts[1])
    return lat, lng

# ---------------------
# CRUD Routes
# ---------------------
def add_route():
    os.system('cls' if os.name=='nt' else 'clear')
    print(f"{Colors.BLUE}=== Add Route ==={Colors.RESET}\n")
    name = input("Route name (or Enter to cancel): ").strip()
    if not name:
        return
    start_dms = input("Start coordinate (DMS) (or Enter to cancel): ").strip()
    if not start_dms:
        return
    end_dms = input("End coordinate (DMS) (or Enter to cancel): ").strip()
    if not end_dms:
        return

    try:
        start_lat, start_lng = parse_dms_pair(start_dms)
        end_lat, end_lng = parse_dms_pair(end_dms)
    except Exception as e:
        print(f"{Colors.RED}⚠ Invalid DMS input: {e}{Colors.RESET}")
        input(f"\n{Colors.CYAN}Press Enter to return to menu...{Colors.RESET}")
        return

    init_db()
    routes = get_routes()
    if any(r[1] == name for r in routes):
        print(f"{Colors.RED}⚠ Route '{name}' already exists.{Colors.RESET}")
        input(f"\n{Colors.CYAN}Press Enter to return to menu...{Colors.RESET}")
        return

    @with_db
    def insert_route(name, start_lat, start_lng, end_lat, end_lng, conn=None):
        c = conn.cursor()
        c.execute('''
            INSERT INTO routes (name, start_lat, start_lng, end_lat, end_lng, last_normal_time, last_state, historical_times)
            VALUES (?, ?, ?, ?, ?, NULL, 'Normal', '[]')
        ''', (name, start_lat, start_lng, end_lat, end_lng))
        conn.commit()
    
    insert_route(name, start_lat, start_lng, end_lat, end_lng)

    # Generate route map
    get_route_map(name, start_lat, start_lng, end_lat, end_lng)

    print(f"{Colors.GREEN}✅ Route '{name}' added successfully.{Colors.RESET}")
    input(f"\n{Colors.CYAN}Press Enter to return to menu...{Colors.RESET}")

def list_routes():
    os.system('cls' if os.name=='nt' else 'clear')
    print(f"{Colors.BLUE}=== All Routes ==={Colors.RESET}\n")
    init_db()
    routes = get_routes()
    if not routes:
        print(f"{Colors.RED}No routes found.{Colors.RESET}")
        input(f"\n{Colors.CYAN}Press Enter to return to menu...{Colors.RESET}")
        return

    for idx, r in enumerate(routes, start=1):
        route_name = r[1]
        map_path = os.path.join(MAPS_DIR, f"{route_name}.png")

        # Generate map if missing
        if not os.path.exists(map_path):
            try:
                get_route_map(
                    route_name=route_name,
                    start_lat=r[2],
                    start_lng=r[3],
                    end_lat=r[4],
                    end_lng=r[5]
                )
                map_status = f"{Colors.GREEN}Generated{Colors.RESET}"
            except Exception as e:
                map_status = f"{Colors.RED}Failed{Colors.RESET}"
        else:
            map_status = f"{Colors.GREEN}Exists{Colors.RESET}"

        print(f"{Colors.YELLOW}{idx}.{Colors.RESET} {route_name} | Start: ({r[2]}, {r[3]}) | "
              f"End: ({r[4]}, {r[5]}) | Last state: {r[7]} | Map: {map_status}")

    input(f"\n{Colors.CYAN}Press Enter to return to menu...{Colors.RESET}")

def remove_route():
    os.system('cls' if os.name=='nt' else 'clear')
    print(f"{Colors.BLUE}=== Remove Route ==={Colors.RESET}\n")
    init_db()
    routes = get_routes()
    if not routes:
        print(f"{Colors.RED}No routes found.{Colors.RESET}")
        input(f"\n{Colors.CYAN}Press Enter to return to menu...{Colors.RESET}")
        return

    # Display sequential numbering
    for idx, r in enumerate(routes, start=1):
        print(f"{Colors.YELLOW}[{idx}]{Colors.RESET} {r[1]} | Start: ({r[2]}, {r[3]}) | End: ({r[4]}, {r[5]}) | Last state: {r[7]}")
    print(f"{Colors.YELLOW}[0]{Colors.RESET} Return to Menu")

    sel = input("\nEnter route number to remove: ").strip()
    if sel == "0":
        return
    if not sel.isdigit() or not (1 <= int(sel) <= len(routes)):
        print(f"{Colors.RED}⚠ Invalid input.{Colors.RESET}")
        input(f"\n{Colors.CYAN}Press Enter to return to menu...{Colors.RESET}")
        return

    route = routes[int(sel)-1]  # map menu number to route object
    confirm = input(f"Are you sure you want to delete route '{route[1]}'? (y/n): ").strip().lower()
    if confirm != 'y':
        print(f"{Colors.RED}Cancelled.{Colors.RESET}")
        input(f"\n{Colors.CYAN}Press Enter to return to menu...{Colors.RESET}")
        return

    # Delete map if exists
    map_path = os.path.join(MAPS_DIR, f"{route[1]}.png")
    if os.path.exists(map_path):
        os.remove(map_path)

    @with_db
    def delete_route(route_id, conn=None):
        c = conn.cursor()
        c.execute('DELETE FROM routes WHERE id=?', (route_id,))
        conn.commit()
    
    delete_route(route[0])

    print(f"{Colors.GREEN}✅ Route '{route[1]}' removed.{Colors.RESET}")
    input(f"\n{Colors.CYAN}Press Enter to return to menu...{Colors.RESET}")

# ---------------------
# Traffic Checks
# ---------------------
def check_single_route(route_id):
    os.system('cls' if os.name=='nt' else 'clear')
    init_db()
    routes = get_routes()
    route = next((r for r in routes if r[0] == route_id), None)
    if not route:
        print(f"{Colors.RED}Route not found.{Colors.RESET}")
        input(f"\n{Colors.CYAN}Press Enter to return to menu...{Colors.RESET}")
        return

    route_id, name, start_lat, start_lng, end_lat, end_lng, last_normal_time, last_state, historical_json = route
    baseline = calculate_baseline(json.loads(historical_json) if historical_json else [])
    result = check_route_traffic(f"{start_lat},{start_lng}", f"{end_lat},{end_lng}", baseline=baseline)
    if not result:
        print(f"{Colors.RED}Traffic check failed.{Colors.RESET}")
        input(f"\n{Colors.CYAN}Press Enter to return to menu...{Colors.RESET}")
        return

    current_state = result["state"]
    update_route_time(route_id, result["total_normal"], current_state)

    print(f"{Colors.BLUE}=== Traffic Check:{Colors.RESET} {Colors.YELLOW}{name}{Colors.RESET} {Colors.BLUE}==={Colors.RESET}\n")
    print(f"State: {Colors.RED if current_state=='Heavy' else Colors.GREEN}{current_state}{Colors.RESET}")
    print(f"Distance: {result['distance_km']:.2f} km")
    print(f"Live: {result['total_live']} min | Normal: {result['total_normal']} min | Delay: {result['total_delay']} min")
    segments_text = summarize_segments(result["heavy_segments"])
    if segments_text:
        print(f"\nHeavy segments:\n{segments_text}")

    input(f"\n{Colors.CYAN}Press Enter to return to menu...{Colors.RESET}")

def check_all_routes():
    import os
    os.system('cls' if os.name == 'nt' else 'clear')

    results = process_all_routes()
    if not results:
        print(f"{Colors.RED}No routes found.{Colors.RESET}")
        input(f"\n{Colors.CYAN}Press Enter to return to menu...{Colors.RESET}")
        return

    # ---------------------
    # Determine column widths dynamically
    # ---------------------
    headers = ["Route", "State", "Distance", "Live", "Delay"]
    columns = {h: len(h) for h in headers}

    for r in results:
        columns["Route"] = max(columns["Route"], len(r["name"]))
        columns["State"] = max(columns["State"], len(r["state"]))
        columns["Distance"] = max(columns["Distance"], len(r["distance"]))
        columns["Live"] = max(columns["Live"], len(r["live"]))
        columns["Delay"] = max(columns["Delay"], len(r["delay"]))

    # ---------------------
    # Print header
    # ---------------------
    header_fmt = (
        f"{{:<{columns['Route']}}} | "
        f"{{:<{columns['State']}}} | "
        f"{{:<{columns['Distance']}}} | "
        f"{{:<{columns['Live']}}} | "
        f"{{:<{columns['Delay']}}}"
    )
    print(f"{Colors.BLUE}=== Traffic Status for All Routes ==={Colors.RESET}\n")
    print(f"{Colors.CYAN}{header_fmt.format(*headers)}{Colors.RESET}")
    print("-" * (sum(columns.values()) + len(columns) * 3 - 1))  # 3 for " | "

    # ---------------------
    # Print rows
    # ---------------------
    for r in results:
        state_color = (
            Colors.RED if r["state"] == "Heavy" else
            Colors.GREEN if r["state"] == "Normal" else
            Colors.YELLOW
        )
        print(
            header_fmt.format(
                r["name"],
                f"{state_color}{r['state']}{Colors.RESET}",
                r["distance"],
                r["live"],
                r["delay"]
            )
        )

    # ---------------------
    # Options
    # ---------------------
    print(f"\n{Colors.YELLOW}[D]{Colors.RESET} View heavy segments for a route")
    print(f"{Colors.YELLOW}[0]{Colors.RESET} Return to Menu")
    choice = input("\nSelect option: ").strip().upper()

    if choice == "D":
        route_names = [r["name"] for r in results]
        print("\nSelect a route:")
        for idx, name in enumerate(route_names, start=1):
            print(f"{Colors.YELLOW}[{idx}]{Colors.RESET} {name}")
        sel = input("\nEnter route number: ").strip()
        if sel.isdigit() and 1 <= int(sel) <= len(route_names):
            selected_name = route_names[int(sel) - 1]
            selected_route = next(r for r in results if r["name"] == selected_name)
            check_single_route(selected_route["route_id"])

# ---------------------
# Traffic Thresholds CLI
# ---------------------
def show_thresholds():
    os.system('cls' if os.name == 'nt' else 'clear')
    print(f"{Colors.BLUE}=== Traffic Thresholds Configuration ==={Colors.RESET}\n")

    thresholds = get_thresholds()
    if not thresholds:
        print(f"{Colors.RED}No thresholds found. Consider resetting to defaults.{Colors.RESET}\n")
        thresholds = []

    # Table header
    header_cols = ["Distance Range", "Route Time Multiplier", "Segment Time Multiplier",
                   "Route Delay Allowance", "Segment Delay Allowance"]
    col_widths = [15, 21, 23, 21, 23]  # fixed widths for alignment

    header = " | ".join(f"{col:<{col_widths[idx]}}" for idx, col in enumerate(header_cols))
    print(f"{Colors.CYAN}{header}{Colors.RESET}")
    print("-" * (sum(col_widths) + len(col_widths) - 1))  # separator line

    # Table entries
    for t in thresholds:
        row = (
            f"{t['min_km']}–{t['max_km']} km".ljust(col_widths[0]) + " | " +
            f"{t['factor_total']}".ljust(col_widths[1]) + " | " +
            f"{t['factor_step']}".ljust(col_widths[2]) + " | " +
            f"{t['delay_total']}".ljust(col_widths[3]) + " | " +
            f"{t['delay_step']}".ljust(col_widths[4])
        )
        print(row)

    # Definitions with examples
    print(f"\n{Colors.BLUE}Definitions:{Colors.RESET}")
    print("  - Route Time Multiplier:")
    print("      Multiplies route normal time to set threshold.")
    print("      Example: 30 min × 1.5 = 45 min → route flagged if >45 min\n")

    print("  - Segment Time Multiplier:")
    print("      Multiplies segment normal time to flag that segment individually.")
    print("      Example: 5 min × 2 = 10 min → segment flagged if >10 min\n")

    print("  - Route Delay Allowance:")
    print("      Extra minutes over normal route time before flagging route.")
    print("      Example: 30 min + 5 min = 35 min → route flagged if >35 min\n")

    print("  - Segment Delay Allowance:")
    print("      Extra minutes per segment before flagging segment.")
    print("      Example: 5 min + 2 min = 7 min → segment flagged if >7 min\n")

    # Options
    print(f"{Colors.BLUE}Options:{Colors.RESET}")
    print(f"  {Colors.YELLOW}[E]{Colors.RESET} Edit existing threshold")
    print(f"  {Colors.YELLOW}[R]{Colors.RESET} Reset to default thresholds")
    print(f"  {Colors.YELLOW}[0]{Colors.RESET} Return to Menu\n")

    choice = input(f"{Colors.CYAN}Select option: {Colors.RESET}").strip().upper()
    if choice == "0":
        return  # exits thresholds menu to main menu
    elif choice == "E":
        edit_threshold()  # will return here after editing
    elif choice == "R":
        confirm = input(f"{Colors.RED}Are you sure you want to reset thresholds to default? (y/n): {Colors.RESET}").strip().lower()
        if confirm == 'y':
            reset_thresholds()
            print(f"{Colors.GREEN}Thresholds reset to default.{Colors.RESET}")
            input(f"{Colors.CYAN}Press Enter to continue...{Colors.RESET}")
    else:
        input(f"{Colors.RED}Invalid choice. Press Enter to continue...{Colors.RESET}")

def edit_threshold():
    thresholds = get_thresholds()
    while True:  # loop to stay in edit menu until exit
        os.system('cls' if os.name=='nt' else 'clear')
        print(f"{Colors.BLUE}=== Edit Traffic Threshold ==={Colors.RESET}\n")

        for idx, t in enumerate(thresholds, start=1):
            print(f"{Colors.YELLOW}[{idx}]{Colors.RESET} {t['min_km']}–{t['max_km']} km")
        print(f"{Colors.YELLOW}[0]{Colors.RESET} Return to Thresholds Menu\n")

        sel = input(f"{Colors.CYAN}Select threshold to edit: {Colors.RESET}").strip()
        if sel == "0":
            return  # return to thresholds menu
        if not sel.isdigit() or not (1 <= int(sel) <= len(thresholds)):
            input(f"{Colors.RED}Invalid selection. Press Enter to continue...{Colors.RESET}")
            continue
        sel_idx = int(sel) - 1
        t = thresholds[sel_idx]

        # Show current values and sensitivity helper
        os.system('cls' if os.name=='nt' else 'clear')
        print(f"{Colors.BLUE}Editing Threshold: {Colors.CYAN}{t['min_km']}–{t['max_km']} km{Colors.RESET}\n")
        print(f"{Colors.CYAN}Current values:{Colors.RESET}")
        print(f"  Route Time Multiplier: {Colors.GREEN}{t['factor_total']}{Colors.RESET}")
        print(f"  Segment Time Multiplier: {Colors.GREEN}{t['factor_step']}{Colors.RESET}")
        print(f"  Route Delay Allowance: {Colors.GREEN}{t['delay_total']}{Colors.RESET}")
        print(f"  Segment Delay Allowance: {Colors.GREEN}{t['delay_step']}{Colors.RESET}\n")

        print(f"{Colors.CYAN}Sensitivity Helper:{Colors.RESET}")
        print(f"  - More Sensitive (flags traffic earlier):")
        print("      Increase multipliers")
        print("      Decrease delay allowances")
        print(f"  - Less Sensitive (flags traffic later):")
        print("      Decrease multipliers")
        print("      Increase delay allowances\n")

        print(f"{Colors.YELLOW}[0]{Colors.RESET} Cancel editing and return to Thresholds Menu\n")

        # Input loop for all four values
        for key, desc in [("factor_total","Route Time Multiplier"), 
                          ("factor_step","Segment Time Multiplier"),
                          ("delay_total","Route Delay Allowance"),
                          ("delay_step","Segment Delay Allowance")]:
            while True:
                val = input(f"{desc} [{t[key]}]: ").strip()
                if val == "0":
                    print(f"\n{Colors.RED}Editing canceled. Returning to Thresholds Menu...{Colors.RESET}")
                    input(f"{Colors.CYAN}Press Enter to continue...{Colors.RESET}")
                    return
                if val:  # only update if something entered
                    try:
                        t[key] = float(val)  # allow floats for all, even delay
                        break
                    except ValueError:
                        print(f"{Colors.RED}Invalid input. Enter a number.{Colors.RESET}")
                else:
                    break  # keep current value

        thresholds[sel_idx] = t
        set_thresholds(thresholds)
        print(f"\n{Colors.GREEN}Threshold updated successfully.{Colors.RESET}")
        input(f"{Colors.CYAN}Press Enter to return to Thresholds Menu...{Colors.RESET}")
        return  # go back to thresholds menu after update

# ---------------------
# Main CLI Menu
# ---------------------
def main_menu():
    while True:
        os.system('cls' if os.name=='nt' else 'clear')
        print(f"{Colors.BLUE}=== Traffic Route Manager ==={Colors.RESET}\n")
        print(f"{Colors.YELLOW}[1]{Colors.RESET} Traffic Thresholds")
        print(f"{Colors.YELLOW}[2]{Colors.RESET} Add route")
        print(f"{Colors.YELLOW}[3]{Colors.RESET} List all routes")
        print(f"{Colors.YELLOW}[4]{Colors.RESET} Check traffic for a route")
        print(f"{Colors.YELLOW}[5]{Colors.RESET} Check traffic for all routes")
        print(f"{Colors.YELLOW}[6]{Colors.RESET} Remove a route")
        print(f"{Colors.YELLOW}[7]{Colors.RESET} Exit")

        choice = input("\nSelect option: ").strip()
        if choice == "1":
            show_thresholds()
        elif choice == "2":
            add_route()
        elif choice == "3":
            list_routes()
        elif choice == "4":
            os.system('cls' if os.name=='nt' else 'clear')
            routes = get_routes()
            if not routes:
                print(f"{Colors.RED}No routes found.{Colors.RESET}")
                input(f"\n{Colors.CYAN}Press Enter to return to menu...{Colors.RESET}")
                continue

            print(f"{Colors.BLUE}=== Select Route ==={Colors.RESET}\n")
            for idx, r in enumerate(routes, start=1):
                print(f"{Colors.YELLOW}[{idx}]{Colors.RESET} {r[1]}")
            print(f"{Colors.YELLOW}[0]{Colors.RESET} Return to Menu")

            sel = input("\nEnter route number: ").strip()
            if sel == "0":
                continue
            if sel.isdigit() and 1 <= int(sel) <= len(routes):
                route = routes[int(sel)-1]  # get DB route object
                check_single_route(route[0])
            else:
                input(f"\n{Colors.RED}Invalid selection. Press Enter to return to menu...{Colors.RESET}")
        elif choice == "5":
            check_all_routes()
        elif choice == "6":
            remove_route()
        elif choice == "7":
            sys.exit(0)
        else:
            input(f"\n{Colors.RED}Invalid choice.{Colors.RESET} Press Enter to continue...")

if __name__ == "__main__":
    main_menu()
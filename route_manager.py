"""CLI interface for traffic route management."""

import os
import sys
import re
from pathlib import Path
from typing import Optional, Tuple

from traffic_utils import (
    init_db,
    get_routes,
    update_route_time,
    calculate_baseline,
    check_route_traffic,
    summarize_segments,
    get_route_map,
    process_all_routes,
    add_route,
    delete_route,
    update_route_priority
)

# ---------------------
# Paths from Docker environment
# ---------------------
DATA_DIR = Path(os.environ["DATA_DIR"])
MAPS_DIR = Path(os.environ["MAPS_DIR"])

DATA_DIR.mkdir(parents=True, exist_ok=True)
MAPS_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------
# CLI Colors
# ---------------------
class Colors:
    """ANSI color codes for terminal output."""
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
def dms_to_decimal(dms_str: str) -> float:
    """Convert DMS (Degrees Minutes Seconds) string to decimal degrees.

    Args:
        dms_str: DMS coordinate string (e.g., "33°55'12\"S")

    Returns:
        Decimal degrees as float

    Raises:
        ValueError: If DMS format is invalid
    """
    dms_str = dms_str.strip()
    match = re.fullmatch(r"(\d{1,3})°(\d{1,2})'(\d{1,2}(?:\.\d+)?)\"([NSEW])",
                         dms_str, re.IGNORECASE)
    if not match:
        raise ValueError(f"Invalid DMS format: {dms_str}")

    degrees, minutes, seconds, direction = match.groups()
    decimal = float(degrees) + float(minutes)/60 + float(seconds)/3600

    if direction.upper() in ['S', 'W']:
        decimal *= -1

    return decimal

def parse_dms_pair(dms_pair: str) -> Tuple[float, float]:
    """Parse a lat/lng pair from DMS strings.

    Args:
        dms_pair: Space-separated DMS coordinate pair

    Returns:
        Tuple of (latitude, longitude) as floats

    Raises:
        ValueError: If coordinate pair format is invalid
    """
    parts = dms_pair.strip().split()
    if len(parts) != 2:
        raise ValueError(f"Invalid coordinate pair: {dms_pair}")

    return dms_to_decimal(parts[0]), dms_to_decimal(parts[1])

# ---------------------
# CRUD Routes
# ---------------------
def add_route_cli() -> None:
    """Interactive CLI interface for adding a new route.

    Prompts user for route name and DMS coordinates, validates input,
    checks for duplicates, adds route to database, and generates map.
    """
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

    # Priority prompt
    while True:
        priority_input = input("Priority (H for High, N for Normal, or Enter for Normal): ").strip().upper()
        if not priority_input or priority_input == 'N':
            priority = 'Normal'
            break
        elif priority_input == 'H':
            priority = 'High'
            break
        else:
            print(f"{Colors.YELLOW}⚠ Please enter 'H' for High, 'N' for Normal, or press Enter for Normal.{Colors.RESET}")

    try:
        start_lat, start_lng = parse_dms_pair(start_dms)
        end_lat, end_lng = parse_dms_pair(end_dms)
    except Exception as exc:
        print(f"{Colors.RED}⚠ Invalid DMS input: {exc}{Colors.RESET}")
        input(f"\n{Colors.CYAN}Press Enter to return to menu...{Colors.RESET}")
        return

    init_db()
    routes = get_routes()
    if any(r["name"] == name for r in routes):
        print(f"{Colors.RED}⚠ Route '{name}' already exists.{Colors.RESET}")
        input(f"\n{Colors.CYAN}Press Enter to return to menu...{Colors.RESET}")
        return

    add_route(name, start_lat, start_lng, end_lat, end_lng, priority)
    get_route_map(name, start_lat, start_lng, end_lat, end_lng)
    print(f"{Colors.GREEN}✅ Route '{name}' added successfully.{Colors.RESET}")
    input(f"\n{Colors.CYAN}Press Enter to return to menu...{Colors.RESET}")

def list_routes() -> None:
    """Display all routes with coordinates and map generation status.

    Shows numbered list of routes with coordinates, last traffic state,
    and map generation status. Attempts to generate missing maps.
    """
    os.system('cls' if os.name=='nt' else 'clear')
    print(f"{Colors.BLUE}=== All Routes ==={Colors.RESET}\n")
    init_db()
    routes = get_routes()
    if not routes:
        print(f"{Colors.RED}No routes found.{Colors.RESET}")
        input(f"\n{Colors.CYAN}Press Enter to return to menu...{Colors.RESET}")
        return

    for idx, route in enumerate(routes, start=1):
        route_name = route["name"]
        map_path = MAPS_DIR / f"{route_name}.png"
        if not map_path.exists():
            try:
                get_route_map(route_name, route["start_lat"], route["start_lng"],
                             route["end_lat"], route["end_lng"])
                map_status = f"{Colors.GREEN}Generated{Colors.RESET}"
            except Exception:
                map_status = f"{Colors.RED}Failed{Colors.RESET}"
        else:
            map_status = f"{Colors.GREEN}Exists{Colors.RESET}"

        priority = route.get('priority', 'Normal')
        priority_color = Colors.RED if priority == 'High' else Colors.GREEN
        print(f"{Colors.YELLOW}{idx}.{Colors.RESET} {route_name} | "
              f"Priority: {priority_color}{priority}{Colors.RESET} | "
              f"Start: ({route['start_lat']},{route['start_lng']}) | "
              f"End: ({route['end_lat']},{route['end_lng']}) | "
              f"Last state: {route['last_state']} | Map: {map_status}")

    input(f"\n{Colors.CYAN}Press Enter to return to menu...{Colors.RESET}")

def update_priority_cli() -> None:
    """Interactive CLI interface for updating route priority."""
    os.system('cls' if os.name=='nt' else 'clear')
    print(f"{Colors.BLUE}=== Update Route Priority ==={Colors.RESET}\n")
    init_db()
    routes = get_routes()

    if not routes:
        print(f"{Colors.RED}No routes found.{Colors.RESET}")
        input(f"\n{Colors.CYAN}Press Enter to return to menu...{Colors.RESET}")
        return

    # Display routes with current priorities
    for idx, route in enumerate(routes, start=1):
        priority = route.get('priority', 'Normal')
        priority_color = Colors.RED if priority == 'High' else Colors.GREEN
        print(f"{Colors.YELLOW}{idx}.{Colors.RESET} {route['name']} | "
              f"Priority: {priority_color}{priority}{Colors.RESET}")

    while True:
        try:
            choice = input(f"\nSelect route number (or Enter to cancel): ").strip()
            if not choice:
                return

            route_idx = int(choice) - 1
            if 0 <= route_idx < len(routes):
                break
            else:
                print(f"{Colors.RED}⚠ Invalid selection. Please choose 1-{len(routes)}.{Colors.RESET}")
        except ValueError:
            print(f"{Colors.RED}⚠ Please enter a valid number.{Colors.RESET}")

    selected_route = routes[route_idx]
    current_priority = selected_route.get('priority', 'Normal')

    print(f"\nRoute: {Colors.CYAN}{selected_route['name']}{Colors.RESET}")
    print(f"Current priority: {Colors.RED if current_priority == 'High' else Colors.GREEN}{current_priority}{Colors.RESET}")

    while True:
        priority_input = input("New priority (H for High, N for Normal, or Enter to cancel): ").strip().upper()
        if not priority_input:
            return
        elif priority_input == 'H':
            new_priority = 'High'
            break
        elif priority_input == 'N':
            new_priority = 'Normal'
            break
        else:
            print(f"{Colors.YELLOW}⚠ Please enter 'H' for High or 'N' for Normal.{Colors.RESET}")

    try:
        update_route_priority(selected_route['name'], new_priority)
        print(f"{Colors.GREEN}✅ Priority updated to {new_priority} for '{selected_route['name']}'{Colors.RESET}")
    except Exception as e:
        print(f"{Colors.RED}⚠ Failed to update priority: {e}{Colors.RESET}")

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

    for idx, r in enumerate(routes, start=1):
        print(f"{Colors.YELLOW}[{idx}]{Colors.RESET} {r['name']} | Start: ({r['start_lat']},{r['start_lng']}) | End: ({r['end_lat']},{r['end_lng']}) | Last state: {r['last_state']}")
    print(f"{Colors.YELLOW}[0]{Colors.RESET} Return to Menu")

    sel = input("\nEnter route number to remove: ").strip()
    if sel == "0":
        return
    if not sel.isdigit() or not (1 <= int(sel) <= len(routes)):
        print(f"{Colors.RED}⚠ Invalid input.{Colors.RESET}")
        input(f"\n{Colors.CYAN}Press Enter to return to menu...{Colors.RESET}")
        return

    route = routes[int(sel)-1]
    confirm = input(f"Are you sure you want to delete route '{route['name']}'? (y/n): ").strip().lower()
    if confirm != 'y':
        print(f"{Colors.RED}Cancelled.{Colors.RESET}")
        input(f"\n{Colors.CYAN}Press Enter to return to menu...{Colors.RESET}")
        return

    # Delete map if exists
    map_path = MAPS_DIR / f"{route['name']}.png"
    if map_path.exists():
        map_path.unlink()

    # Delete route using the function from traffic_utils
    delete_route(route["name"])

    print(f"{Colors.GREEN}✅ Route '{route['name']}' removed.{Colors.RESET}")
    input(f"\n{Colors.CYAN}Press Enter to return to menu...{Colors.RESET}")

# ---------------------
# Traffic Checks
# ---------------------
def check_single_route(route_id):
    os.system('cls' if os.name=='nt' else 'clear')
    init_db()
    routes = get_routes()
    route = next((r for r in routes if r["id"]==route_id), None)
    if not route:
        print(f"{Colors.RED}Route not found.{Colors.RESET}")
        input(f"\n{Colors.CYAN}Press Enter to return to menu...{Colors.RESET}")
        return

    historical_times = []
    if route.get("historical_times"):
        import json
        historical_times = json.loads(route["historical_times"])
    
    baseline = calculate_baseline(historical_times)
    result = check_route_traffic(f"{route['start_lat']},{route['start_lng']}", f"{route['end_lat']},{route['end_lng']}", baseline=baseline)
    if not result:
        print(f"{Colors.RED}Traffic check failed.{Colors.RESET}")
        input(f"\n{Colors.CYAN}Press Enter to return to menu...{Colors.RESET}")
        return

    current_state = result["state"]
    update_route_time(route_id, result["total_normal"], current_state)

    print(f"{Colors.BLUE}=== Traffic Check:{Colors.RESET} {Colors.YELLOW}{route['name']}{Colors.RESET} {Colors.BLUE}==={Colors.RESET}\n")
    print(f"State: {Colors.RED if current_state=='Heavy' else Colors.GREEN}{current_state}{Colors.RESET}")
    print(f"Distance: {result['distance_km']:.2f} km")
    print(f"Live: {result['total_live']} min | Normal: {result['total_normal']} min | Delay: {result['total_delay']} min")
    segments_text = summarize_segments(result["heavy_segments"])
    if segments_text:
        print(f"\nHeavy segments:\n{segments_text}")
    input(f"\n{Colors.CYAN}Press Enter to return to menu...{Colors.RESET}")

def check_all_routes():
    os.system('cls' if os.name=='nt' else 'clear')
    results = process_all_routes()
    if not results:
        print(f"{Colors.RED}No routes found.{Colors.RESET}")
        input(f"\n{Colors.CYAN}Press Enter to return to menu...{Colors.RESET}")
        return

    headers = ["Route","State","Distance","Live","Delay"]
    columns = {h: len(h) for h in headers}
    for r in results:
        columns["Route"] = max(columns["Route"], len(r["name"]))
        columns["State"] = max(columns["State"], len(r["state"]))
        columns["Distance"] = max(columns["Distance"], len(r["distance"]))
        columns["Live"] = max(columns["Live"], len(r["live"]))
        columns["Delay"] = max(columns["Delay"], len(r["delay"]))

    header_fmt = f"{{:<{columns['Route']}}} | {{:<{columns['State']}}} | {{:<{columns['Distance']}}} | {{:<{columns['Live']}}} | {{:<{columns['Delay']}}}"
    print(f"{Colors.BLUE}=== Traffic Status for All Routes ==={Colors.RESET}\n")
    print(f"{Colors.CYAN}{header_fmt.format(*headers)}{Colors.RESET}")
    print("-"*(sum(columns.values())+len(columns)*3-1))

    for r in results:
        state_color = Colors.RED if r["state"]=="Heavy" else Colors.GREEN if r["state"]=="Normal" else Colors.YELLOW
        print(header_fmt.format(r["name"], f"{state_color}{r['state']}{Colors.RESET}", r["distance"], r["live"], r["delay"]))

    print(f"\n{Colors.YELLOW}[D]{Colors.RESET} View heavy segments for a route")
    print(f"{Colors.YELLOW}[0]{Colors.RESET} Return to Menu")
    choice = input("\nSelect option: ").strip().upper()

    if choice=="D":
        route_names = [r["name"] for r in results]
        print("\nSelect a route:")
        for idx, name in enumerate(route_names, start=1):
            print(f"{Colors.YELLOW}[{idx}]{Colors.RESET} {name}")
        sel = input("\nEnter route number: ").strip()
        if sel.isdigit() and 1<=int(sel)<=len(route_names):
            selected_name = route_names[int(sel)-1]
            selected_route = next(r for r in results if r["name"]==selected_name)
            check_single_route(selected_route["route_id"])

# ---------------------
# Thresholds Management
# ---------------------
def show_thresholds():
    from traffic_utils import get_thresholds, set_thresholds, reset_thresholds
    
    while True:
        os.system('cls' if os.name=='nt' else 'clear')
        print(f"{Colors.BLUE}=== Traffic Thresholds ==={Colors.RESET}\n")
        
        thresholds = get_thresholds()
        
        # Display current thresholds
        print(f"{Colors.CYAN}Current Thresholds:{Colors.RESET}")
        print(f"{'Distance (km)':<15} {'Route Factor':<15} {'Segment Factor':<15} {'Route Delay':<15} {'Segment Delay':<15}")
        print("-" * 75)
        
        for t in thresholds:
            print(f"{t['min_km']}-{t['max_km']:<10} {t['factor_total']:<15} {t['factor_step']:<15} {t['delay_total']:<15} {t['delay_step']:<15}")
        
        print(f"\n{Colors.YELLOW}[1]{Colors.RESET} Edit thresholds")
        print(f"{Colors.YELLOW}[2]{Colors.RESET} Reset to defaults")
        print(f"{Colors.YELLOW}[0]{Colors.RESET} Return to main menu")
        
        choice = input("\nSelect option: ").strip()
        
        if choice == "0":
            break
        elif choice == "1":
            edit_thresholds(thresholds)
        elif choice == "2":
            confirm = input("Reset all thresholds to default values? (y/n): ").strip().lower()
            if confirm == 'y':
                reset_thresholds()
                print(f"{Colors.GREEN}✅ Thresholds reset to defaults.{Colors.RESET}")
                input(f"\n{Colors.CYAN}Press Enter to continue...{Colors.RESET}")

def edit_thresholds(thresholds):
    from traffic_utils import set_thresholds
    
    os.system('cls' if os.name=='nt' else 'clear')
    print(f"{Colors.BLUE}=== Edit Thresholds ==={Colors.RESET}\n")
    
    for idx, t in enumerate(thresholds):
        print(f"{Colors.YELLOW}[{idx+1}]{Colors.RESET} {t['min_km']}-{t['max_km']} km")
    print(f"{Colors.YELLOW}[0]{Colors.RESET} Back to thresholds menu")
    
    sel = input("\nSelect threshold to edit: ").strip()
    if sel == "0":
        return
    
    if not sel.isdigit() or not (1 <= int(sel) <= len(thresholds)):
        print(f"{Colors.RED}⚠ Invalid selection.{Colors.RESET}")
        input(f"\n{Colors.CYAN}Press Enter to continue...{Colors.RESET}")
        return
    
    threshold = thresholds[int(sel)-1]
    
    print(f"\n{Colors.CYAN}Editing threshold for {threshold['min_km']}-{threshold['max_km']} km routes:{Colors.RESET}")
    print(f"Current values:")
    print(f"  Route Factor: {threshold['factor_total']}")
    print(f"  Segment Factor: {threshold['factor_step']}")  
    print(f"  Route Delay: {threshold['delay_total']}")
    print(f"  Segment Delay: {threshold['delay_step']}")
    print()
    
    try:
        new_factor_total = input(f"Route Factor [{threshold['factor_total']}]: ").strip()
        if new_factor_total:
            threshold['factor_total'] = float(new_factor_total)
        
        new_factor_step = input(f"Segment Factor [{threshold['factor_step']}]: ").strip()
        if new_factor_step:
            threshold['factor_step'] = float(new_factor_step)
        
        new_delay_total = input(f"Route Delay [{threshold['delay_total']}]: ").strip()
        if new_delay_total:
            threshold['delay_total'] = int(new_delay_total)
        
        new_delay_step = input(f"Segment Delay [{threshold['delay_step']}]: ").strip()
        if new_delay_step:
            threshold['delay_step'] = int(new_delay_step)
        
        set_thresholds(thresholds)
        print(f"{Colors.GREEN}✅ Threshold updated successfully.{Colors.RESET}")
        
    except ValueError as e:
        print(f"{Colors.RED}⚠ Invalid input: {e}{Colors.RESET}")
    
    input(f"\n{Colors.CYAN}Press Enter to continue...{Colors.RESET}")

# ---------------------
# Main CLI
# ---------------------
def main_menu():
    while True:
        os.system('cls' if os.name=='nt' else 'clear')
        print(f"{Colors.BLUE}=== Traffic Route Manager ==={Colors.RESET}\n")
        print(f"{Colors.YELLOW}[1]{Colors.RESET} Traffic Thresholds")
        print(f"{Colors.YELLOW}[2]{Colors.RESET} Add route")
        print(f"{Colors.YELLOW}[3]{Colors.RESET} List all routes")
        print(f"{Colors.YELLOW}[4]{Colors.RESET} Update route priority")
        print(f"{Colors.YELLOW}[5]{Colors.RESET} Check traffic for a route")
        print(f"{Colors.YELLOW}[6]{Colors.RESET} Check traffic for all routes")
        print(f"{Colors.YELLOW}[7]{Colors.RESET} Remove a route")
        print(f"{Colors.YELLOW}[8]{Colors.RESET} Exit")

        choice = input("\nSelect option: ").strip()
        if choice=="1": show_thresholds()
        elif choice=="2": add_route_cli()
        elif choice=="3": list_routes()
        elif choice=="4": update_priority_cli()
        elif choice=="5":
            os.system('cls' if os.name=='nt' else 'clear')
            routes = get_routes()
            if not routes:
                print(f"{Colors.RED}No routes found.{Colors.RESET}")
                input(f"\n{Colors.CYAN}Press Enter to return to menu...{Colors.RESET}")
                continue
            print(f"{Colors.BLUE}=== Select Route ==={Colors.RESET}\n")
            for idx,r in enumerate(routes,start=1):
                print(f"{Colors.YELLOW}[{idx}]{Colors.RESET} {r['name']}")
            print(f"{Colors.YELLOW}[0]{Colors.RESET} Return to Menu")
            sel = input("\nEnter route number: ").strip()
            if sel=="0": continue
            if sel.isdigit() and 1<=int(sel)<=len(routes):
                route = routes[int(sel)-1]
                check_single_route(route["id"])
            else:
                input(f"\n{Colors.RED}Invalid selection. Press Enter to return to menu...{Colors.RESET}")
        elif choice=="5": check_all_routes()
        elif choice=="6": remove_route()
        elif choice=="7": sys.exit(0)
        else:
            input(f"\n{Colors.RED}Invalid choice.{Colors.RESET} Press Enter to continue...")

if __name__=="__main__":
    main_menu()
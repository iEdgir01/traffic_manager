# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Traffic Manager is a dockerized Python application that monitors traffic conditions on predefined routes using Google Maps APIs. The system consists of three main components running in Docker containers:

1. **Ignition Subscriber**: Monitors MQTT messages for vehicle ignition events and triggers traffic checks
2. **Discord Bot**: Provides Discord interface for route management and traffic notifications
3. **PostgreSQL Database**: Stores route data, traffic history, and configuration

## Architecture

### Core Components

- `main.py`: Application entry point that orchestrates both ignition monitoring and Discord bot services with automatic restart capabilities
- `traffic_utils.py`: Core traffic checking logic, database operations, and Google Maps API integration
- `route_manager.py`: CLI interface for route management (add/remove/list routes, configure thresholds)
- `discord_bot/`: Discord bot implementation for route management and notifications
- `ignition_subscriber/`: MQTT subscriber that triggers traffic checks when vehicle ignition is detected

### Data Flow

1. MQTT messages trigger ignition events → `ignition_subscriber/subscriber.py`
2. Ignition events trigger traffic checks → `discord_bot/discord_notify.py`
3. Traffic data fetched from Google Maps APIs → `traffic_utils.py`
4. Results stored in PostgreSQL and notifications sent to Discord
5. Route maps generated and cached in `/data/maps/`

### Database Schema

Routes table stores:
- Route coordinates (start/end lat/lng)
- Historical traffic times (JSON array)
- Last known traffic state
- Route name and metadata

Config table stores:
- Traffic detection thresholds (configurable per distance range)

## Development Commands

### Running the Application

```bash
# Start all services via Docker Compose
docker-compose up -d

# Run route manager CLI locally (requires environment variables)
python route_manager.py

# Run traffic check for all routes
python traffic_utils.py

# Test Discord notifications
python discord_bot/discord_notify.py
```

### Testing

```bash
# Run ignition subscriber test
python test/ignition_subscriber/subscriber.py

# Test MQTT publishing/subscribing
python test/mqtt_test/pub.py
python test/mqtt_test/sub.py
```

### Docker Commands

```bash
# Build individual service images
docker build -f Dockerfile -t traffic_manager_main .
docker build -f discord_bot.dockerfile -t traffic_manager_discord_bot .
docker build -f ignition_subscriber.dockerfile -t traffic_manager_ignition_subscriber .

# View logs for specific service
docker logs -f ignition_subscriber
docker logs -f discord_bot
```

## Environment Variables

Required for all components:
- `GOOGLE_MAPS_API_KEY`: Google Maps API key for traffic data and map generation
- `DATA_DIR`: Directory for data storage (default: `/app/data`)
- `MAPS_DIR`: Directory for generated route maps (default: `/app/data/maps`)

Database configuration:
- `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`

MQTT configuration (ignition subscriber):
- `MQTT_BROKER`, `MQTT_PORT`, `MQTT_TOPIC`
- `IGNITION_TIMEOUT`: Timeout in seconds for ignition off detection

Discord configuration:
- `DISCORD_BOT_TOKEN`: Discord bot token
- `DISCORD_WEBHOOK_URL`: Webhook URL for traffic notifications

## Key Features

### Traffic Detection
- Dynamic thresholds based on route distance
- Heavy traffic detection via both total delay and segment-level analysis
- Historical baseline calculations for improved accuracy
- Configurable thresholds per distance range (0-2km, 2-5km, 5-20km, 20-50km)

### Route Management
- DMS coordinate input support (Degrees Minutes Seconds)
- Automatic route map generation via Google Static Maps API
- Route CRUD operations through CLI and Discord bot
- Traffic threshold configuration system

### Monitoring & Notifications
- MQTT-based ignition monitoring with configurable timeouts
- Discord webhook notifications for traffic state changes
- Automatic service restart with exponential backoff
- Comprehensive logging to files and stdout

## Development Notes

- All database operations use the `@with_db` decorator for automatic connection management
- Coordinates are stored as floats and explicitly cast to prevent type issues
- Heavy traffic segments include HTML instruction parsing and delay calculations
- The system uses PostgreSQL with RealDictCursor for easier data access
- Map generation is cached - existing maps are not regenerated
- Historical traffic times are limited to the last 20 entries per route
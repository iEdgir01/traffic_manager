# Traffic Manager

A dockerized Python application that monitors traffic conditions on predefined routes using Google Maps APIs, with MQTT-triggered monitoring and Discord bot interface.

## Overview

Traffic Manager is a comprehensive traffic monitoring system that automatically checks traffic conditions when vehicle ignition events are detected via MQTT. It provides both a Discord bot interface for route management and a CLI tool for local administration.

## Features

### 🚗 Smart Traffic Detection
- **Dynamic Thresholds**: Configurable traffic detection based on route distance
- **Heavy Traffic Analysis**: Both total delay and segment-level traffic analysis
- **Historical Baselines**: Improved accuracy using historical traffic data
- **Distance-Based Configuration**: Separate thresholds for different route lengths (0-2km, 2-5km, 5-20km, 20-50km)

### 🤖 Discord Bot Interface
- **Route Management**: Add, remove, and list routes via Discord
- **Live Traffic Checks**: Check individual or all routes
- **Visual Maps**: Automatic route map generation using Google Static Maps API
- **Threshold Configuration**: Manage traffic detection sensitivity
- **Real-time Notifications**: Traffic state change alerts via webhooks

### 📡 MQTT Integration
- **Ignition Monitoring**: Automatic traffic checks when vehicle starts
- **Configurable Timeouts**: Ignition off detection with custom timeouts
- **Reliable Messaging**: Robust MQTT subscriber with reconnection

### 🗄️ Data Management
- **PostgreSQL Database**: Persistent storage for routes and traffic history
- **Route Coordinates**: Support for DMS (Degrees Minutes Seconds) input
- **Traffic History**: Historical data for baseline calculations
- **Map Caching**: Generated route maps are cached for performance

## Architecture

### Components

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│  Ignition       │    │   Discord Bot   │    │   PostgreSQL    │
│  Subscriber     │◄──►│                 │◄──►│   Database      │
│                 │    │                 │    │                 │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         │                        │                        │
         │                        │                        │
         ▼                        ▼                        ▼
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   MQTT Broker   │    │  Google Maps    │    │   Route Maps    │
│                 │    │     APIs        │    │     Cache       │
└─────────────────┘    └─────────────────┘    └─────────────────┘
```

1. **Ignition Subscriber**: Monitors MQTT messages for vehicle ignition events
2. **Discord Bot**: Provides user interface for route management and notifications
3. **PostgreSQL Database**: Stores route data, traffic history, and configuration
4. **Google Maps Integration**: Traffic data and route map generation

### Data Flow

1. **MQTT Message** → Ignition detected by subscriber
2. **Traffic Check** → Google Maps API queried for all routes
3. **Analysis** → Traffic conditions compared against dynamic thresholds
4. **Storage** → Results saved to PostgreSQL with historical data
5. **Notification** → Discord webhook alerts for traffic state changes
6. **Visualization** → Route maps generated and cached

## Quick Start

### Prerequisites

- Docker and Docker Compose
- Google Maps API Key
- Discord Bot Token (optional)
- MQTT Broker access (optional)

### Environment Variables

Create a `.env` file with the following variables:

```bash
# Required - Google Maps API
GOOGLE_MAPS_API_KEY=your_google_maps_api_key

# Database Configuration
POSTGRES_HOST=postgres_db
POSTGRES_PORT=5432
POSTGRES_USER=traffic_user
POSTGRES_PASSWORD=your_db_password
POSTGRES_DB=traffic_db

# Data Directories
DATA_DIR=/app/data
MAPS_DIR=/app/data/maps

# Discord Bot (Optional)
DISCORD_BOT_TOKEN=your_discord_bot_token
DISCORD_WEBHOOK_URL=your_webhook_url

# MQTT Configuration (Optional)
MQTT_BROKER=your_mqtt_broker
MQTT_PORT=1883
MQTT_TOPIC=your_topic
IGNITION_TIMEOUT=300
```

### Installation

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd traffic_manager
   ```

2. **Configure environment**
   ```bash
   cp .env.example .env
   # Edit .env with your configuration
   ```

3. **Start the services**
   ```bash
   docker-compose up -d
   ```

4. **Initialize the system**
   ```bash
   # The database will be automatically initialized
   # Add your first route using the CLI or Discord bot
   ```

## Usage

### CLI Interface

Use the route manager for local administration:

```bash
docker exec -it traffic_manager python route_manager.py
```

**Available Commands:**
- Add routes with DMS coordinates
- List all routes with map generation status
- Check traffic for individual or all routes
- Configure traffic detection thresholds
- Remove routes

### Discord Bot Interface

Invite the bot to your Discord server and use the `!menu` command to access:

- **Add Route**: Interactive modal for route creation
- **List Routes**: Paginated route browser with maps
- **Remove Route**: Safe route deletion with confirmation
- **Traffic Status**: Check individual or all routes
- **Manage Thresholds**: Configure traffic detection sensitivity

### API Integration

The system can be triggered via MQTT messages for automated traffic checks:

```json
{
  "ignition": "on",
  "vehicle_id": "your_vehicle",
  "timestamp": "2024-01-01T12:00:00Z"
}
```

## Configuration

### Traffic Thresholds

Configure traffic detection sensitivity based on route distance:

| Distance Range | Route Factor | Segment Factor | Route Delay | Segment Delay |
|----------------|--------------|----------------|-------------|---------------|
| 0-2 km         | 3.0          | 1.0            | 5 min       | 1 min         |
| 2-5 km         | 2.5          | 2.0            | 10 min      | 2 min         |
| 5-20 km        | 2.0          | 3.0            | 15 min      | 5 min         |
| 20-50 km       | 1.5          | 4.0            | 30 min      | 10 min        |

**Sensitivity Adjustment:**
- **More Sensitive**: Increase factors, decrease delays
- **Less Sensitive**: Decrease factors, increase delays

### Route Input Format

Routes support DMS (Degrees Minutes Seconds) coordinate input:

```
Start: 33°55'12"S 18°25'36"E
End: 33°56'00"S 18°22'00"E
```

## Development

### Project Structure

```
traffic_manager/
├── main.py                     # Application orchestrator
├── traffic_utils.py           # Core traffic logic & database
├── route_manager.py           # CLI interface
├── discord_bot/
│   ├── traffic_helper.py      # Discord bot implementation
│   └── discord_notify.py      # Notification service
├── ignition_subscriber/
│   └── subscriber.py          # MQTT subscriber
├── test/                      # Test files
├── docker-compose.yml         # Service orchestration
├── Dockerfile                 # Main service container
├── discord_bot.dockerfile     # Discord bot container
├── ignition_subscriber.dockerfile  # MQTT subscriber container
└── CLAUDE.md                  # Development guidelines
```

### Running Tests

```bash
# Test MQTT integration
docker exec -it traffic_manager python test/mqtt_test/pub.py
docker exec -it traffic_manager python test/mqtt_test/sub.py

# Test ignition subscriber
docker exec -it traffic_manager python test/ignition_subscriber/subscriber.py
```

### Development Commands

```bash
# View logs
docker logs -f discord_bot
docker logs -f ignition_subscriber
docker logs -f postgres_db

# Database access
docker exec -it postgres_db psql -U traffic_user -d traffic_db

# Restart services
docker-compose restart
```

### Code Quality

The project follows Python best practices:

- **PEP 8**: Code style compliance
- **pylint**: Static analysis compliance
- **Type hints**: For better code documentation
- **Error handling**: Comprehensive exception management
- **Logging**: Structured logging throughout

## Troubleshooting

### Common Issues

1. **"must be real number, not str" Error**
   - Ensure database coordinates are properly typed
   - Check that route data access uses dictionary keys, not tuple unpacking

2. **Discord Bot Not Responding**
   - Verify bot token and permissions
   - Check bot logs: `docker logs discord_bot`

3. **Traffic Check Failures**
   - Validate Google Maps API key and quota
   - Ensure coordinates are in valid format

4. **MQTT Connection Issues**
   - Verify broker credentials and network access
   - Check subscriber logs: `docker logs ignition_subscriber`

### Performance Optimization

- Route maps are cached automatically
- Historical traffic data is limited to last 20 entries
- Database connections use connection pooling
- Background tasks use thread pools for concurrency

## Contributing

1. Follow the coding standards defined in `CLAUDE.md`
2. Ensure all tests pass before submitting PRs
3. Update documentation for new features
4. Use meaningful commit messages

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Support

For issues and questions:

1. Check the troubleshooting section
2. Review logs for error details
3. Open an issue with detailed information about your setup
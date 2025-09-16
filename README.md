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
- **Route Management**: Add, remove, and list routes via Discord with priority assignment
- **Priority System**: High/Normal priority routes with intelligent processing
- **Live Traffic Checks**: Check individual or all routes
- **Visual Maps**: Automatic route map generation using Google Static Maps API
- **Threshold Configuration**: Manage traffic detection sensitivity
- **Real-time Notifications**: Traffic state change alerts via webhooks
- **Claude AI Summaries**: Engaging, conversational traffic summaries in random styles
- **Gotify Integration**: Priority-aware push notifications with LLM-generated content

### 📡 MQTT Integration
- **Ignition Monitoring**: Automatic traffic checks when vehicle starts
- **Configurable Timeouts**: Ignition off detection with custom timeouts
- **Reliable Messaging**: Robust MQTT subscriber with reconnection

### 🗄️ Data Management
- **PostgreSQL Database**: Persistent storage for routes, priorities, and traffic history
- **Route Coordinates**: Support for DMS (Degrees Minutes Seconds) input
- **Priority System**: High/Normal priority levels with automated migration
- **Traffic History**: Historical data for baseline calculations
- **Map Caching**: Generated route maps are cached for performance

## Priority System

### Overview

Traffic Manager implements a sophisticated priority system that intelligently determines which routes receive LLM-generated traffic summaries and push notifications. This allows you to focus on critical routes while reducing noise from less important ones.

### Priority Levels

#### 🔴 High Priority Routes
- **Always Processed**: Included in every traffic summary generation
- **Immediate Notifications**: Get LLM-generated summaries regardless of traffic state
- **Use Cases**: Critical commute routes, emergency routes, VIP routes

#### 🟢 Normal Priority Routes
- **Conditional Processing**: Only processed when traffic conditions warrant attention
- **Smart Filtering**: Included in summaries when Heavy OR was Heavy→Normal
- **Use Cases**: Optional routes, secondary paths, occasional destinations

### Notification Logic

#### Discord Notifications (Priority-Agnostic)
- **Trigger**: Only on traffic state changes (Heavy→Normal OR Normal→Heavy)
- **Content**: All routes displayed regardless of priority
- **Purpose**: Complete traffic visibility for monitoring

#### Gotify/LLM Processing (Priority-Aware)
- **High Priority**: Always included in Claude AI traffic summaries
- **Normal Priority**: Only when Heavy OR transitioned from Heavy→Normal
- **Result**: Focused, relevant push notifications to your mobile device

### Examples

**Scenario 1**: Route A (High), Route B (Normal, currently Normal)
- **Discord**: No notification (no state changes)
- **Gotify**: Only Route A summary (High priority always processed)

**Scenario 2**: Route A (High), Route B (Normal, currently Heavy)
- **Discord**: Alert posted with both routes (state change detected)
- **Gotify**: Both routes in summary (High always + Normal meets Heavy criteria)

**Scenario 3**: Route A (High), Route B (Normal, was Heavy→now Normal)
- **Discord**: Alert posted with both routes (state change detected)
- **Gotify**: Both routes in summary (High always + Normal meets Heavy→Normal criteria)

## Claude AI Traffic Summaries

### Overview

Traffic Manager integrates with Claude AI to generate engaging, conversational traffic summaries that make traffic updates more interesting and suitable for text-to-speech (TTS) systems. Instead of dry technical reports, you get creative, personality-driven summaries.

### AI-Generated Summary Features

#### 🎭 Random Personality Styles
Each summary uses a randomly selected conversational style:
- **Professional/News Anchor**: Neutral, clear reporting style
- **Local News Reporter**: Adds place-specific context and local flavor
- **Comedian/Sarcastic**: Humorous takes with jokes and exaggerations
- **Friendly Casual**: Conversational tone like talking to a friend
- **Trump-Style**: Over-the-top, hyperbolic speech patterns
- **Morgan Freeman Narrator**: Calm, dramatic storytelling approach
- **Epic Adventure**: Makes traffic sound like a heroic quest
- **Fairy Tale/Fantasy**: Whimsical takes with dragons and magic

#### 📝 Smart Content Generation
- **Dynamic Length**: Word count scales with number of routes (8 words per route + 20 style overhead)
- **Complete Coverage**: Mentions ALL routes in the summary (both heavy and normal)
- **TTS Optimized**: Short, punchy sentences perfect for voice synthesis
- **Factual Base**: Creative style while maintaining accurate traffic information

#### 🔄 Fallback System
- **Primary**: Claude AI generates creative summaries
- **Fallback**: Simple technical summaries if API unavailable
- **Reliability**: Ensures notifications always work regardless of AI service status

### Example AI Summaries

**Sarcastic Style:**
> "Well folks, Highway-101 decided to become a parking lot with 15 minutes of delays, while Main-Street is actually behaving itself today."

**Morgan Freeman Style:**
> "And so it was, that Highway-101 tested the patience of travelers with delays, while Main-Street flowed like a gentle river."

**Epic Adventure Style:**
> "Today's quest reveals Highway-101 guarded by dragons of delay, while Main-Street offers safe passage to brave commuters."

### Configuration

#### Environment Variables
```bash
# Claude AI Configuration
CLAUDE_API_KEY=your_anthropic_api_key
CLAUDE_SUMMARY_STYLE="Generate a traffic summary in a random conversational style. Choose from: Professional, Local News Reporter, Sarcastic, Friendly Casual, Trump-Style, Morgan Freeman Narrator, Epic Adventure, or Fairy Tale. Make it engaging for TTS and avoid paragraph format."
```

#### Integration Points
- **Priority-Aware**: Only processes routes meeting priority criteria
- **Gotify Delivery**: AI summaries sent as push notifications
- **Automatic Failover**: Falls back to simple summaries if Claude API fails
- **Performance Optimized**: Async processing with 30-second timeout

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
         │                        │                        │
         │                        │                        │
         ▼                        ▼                        ▼
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Claude AI     │    │ Gotify Push     │    │   Discord       │
│   LLM Engine    │    │ Notifications   │    │   Webhooks      │
└─────────────────┘    └─────────────────┘    └─────────────────┘
```

1. **Ignition Subscriber**: Monitors MQTT messages for vehicle ignition events
2. **Discord Bot**: Provides user interface for route management and notifications
3. **PostgreSQL Database**: Stores route data, priorities, traffic history, and configuration
4. **Google Maps Integration**: Traffic data and route map generation
5. **Claude AI LLM Engine**: Generates engaging, conversational traffic summaries
6. **Gotify Push Notifications**: Delivers AI-generated summaries to mobile devices
7. **Discord Webhooks**: Posts structured traffic alerts to Discord channels

### Data Flow

1. **MQTT Message** → Ignition detected by subscriber
2. **Traffic Check** → Google Maps API queried for all routes
3. **Analysis** → Traffic conditions compared against dynamic thresholds
4. **Storage** → Results saved to PostgreSQL with historical data
5. **Priority Filtering** → Routes filtered based on priority criteria
6. **AI Summary Generation** → Claude AI creates conversational traffic summaries
7. **Discord Notification** → Structured alerts posted via webhooks (priority-agnostic)
8. **Gotify Push** → AI-generated summaries sent as push notifications (priority-aware)
9. **Visualization** → Route maps generated and cached

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

# Gotify Android Integration (Optional)
GOTIFY_URL=https://your-gotify-server.com
GOTIFY_TOKEN=your_gotify_app_token
GOTIFY_PRIORITY=5

# Claude AI Integration (Optional)
CLAUDE_API_KEY=your_anthropic_api_key
CLAUDE_SUMMARY_STYLE="Generate a traffic summary in a random conversational style. Choose from: Professional, Local News Reporter, Sarcastic, Friendly Casual, Trump-Style, Morgan Freeman Narrator, Epic Adventure, or Fairy Tale. Make it engaging for TTS and avoid paragraph format."
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
   # The database will be automatically initialized and migrated
   # Existing installations will automatically upgrade to support priorities
   # Add your first route using the CLI or Discord bot
   ```

## Usage

### CLI Interface

Use the route manager for local administration:

```bash
docker exec -it traffic_manager python route_manager.py
```

**Available Commands:**
- Add routes with DMS coordinates and priority assignment
- List all routes with priority status and map generation status
- Update route priorities (High/Normal)
- Check traffic for individual or all routes
- Configure traffic detection thresholds
- Remove routes

### Discord Bot Interface

Invite the bot to your Discord server and use the `!menu` command to access:

- **Add Route**: Interactive modal for route creation with priority selection
- **List Routes**: Paginated route browser with maps and priority display
- **Update Priority**: Toggle route priority between High/Normal
- **Remove Route**: Safe route deletion with confirmation
- **Traffic Status**: Check individual or all routes
- **Manage Thresholds**: Configure traffic detection sensitivity

### API Integration

The system can be triggered via MQTT messages for automated traffic checks:

```json
{
  "Ignition On": true,
  "device_id": "your_device_id",
  "message_time": "2025-01-01T12:00:00Z",
  "location": {
    "latitude": -25.123456,
    "longitude": 28.123456
  }
}
```

The system will trigger traffic alerts when it receives the first ignition ON message after a period of no messages. Continuous messages keep the ignition state alive, and after the configured timeout (default 300 seconds) without messages, the ignition is considered OFF.

### Android Gotify Integration

Traffic Manager sends intelligent, AI-generated summaries as push notifications to a Gotify server for Android integration. These notifications use Claude AI to create engaging, conversational summaries instead of dry technical reports.

**Gotify Notification Format:**
```
Title: "Traffic Summary"
Message: {Claude AI generated conversational summary}
Priority: {configured_priority}
```

**AI-Generated Message Examples:**
- **Sarcastic Style**: `"Well folks, Highway-101 decided to become a parking lot with 15 minutes of delays, while Main-Street is actually behaving itself today."`
- **Morgan Freeman Style**: `"And so it was, that Highway-101 tested the patience of travelers with delays, while Main-Street flowed like a gentle river."`
- **Epic Adventure Style**: `"Today's quest reveals Highway-101 guarded by dragons of delay, while Main-Street offers safe passage to brave commuters."`

**Fallback Messages (when Claude AI unavailable):**
- **Heavy Traffic**: `"Heavy traffic detected on {route}, current delay is {delay} minutes."`
- **Traffic Cleared**: `"You can expect normal travel times on {route}."`

**Priority-Aware Processing:**
- **High Priority Routes**: Always included in summaries
- **Normal Priority Routes**: Only when Heavy OR was Heavy→Normal

Install the Gotify Android app and configure it to connect to your Gotify server to receive creative, TTS-friendly traffic summaries.

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

### Priority Configuration

Routes can be assigned one of two priority levels that control notification behavior:

#### Setting Priorities

**Via Discord Bot:**
1. Use "Add Route" modal - select priority during creation
2. Use "Update Priority" button in route browser to toggle High/Normal

**Via CLI:**
1. During route creation - prompted for priority selection
2. Use "Update route priority" menu option to change existing routes

#### Priority Recommendations

**High Priority Routes:**
- Daily commute routes
- Critical business travel paths
- Emergency or hospital routes
- Routes with frequent heavy traffic

**Normal Priority Routes:**
- Weekend leisure routes
- Alternative backup paths
- Infrequently used routes
- Routes with generally light traffic

### Route Input Format

Routes support DMS (Degrees Minutes Seconds) coordinate input:

```
Start: 25°30'00"S 28°15'00"E
End: 25°35'00"S 28°20'00"E
```

## Development

### Project Structure

```
traffic_manager/
├── main.py                     # Application orchestrator
├── traffic_utils.py           # Core traffic logic & database
├── migrations.py              # Database schema migrations
├── route_manager.py           # CLI interface
├── discord_bot/
│   ├── traffic_helper.py      # Discord bot implementation
│   └── discord_notify.py      # Priority-aware notification service
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

# CLI access in containers
docker exec -it traffic_manager menu           # Route management CLI
docker exec -it traffic_manager test_ignition  # MQTT ignition testing
docker exec -it discord_bot menu               # Route management from bot container
docker exec -it ignition_subscriber menu       # Route management from subscriber
docker exec -it ignition_subscriber test_ignition  # MQTT testing from subscriber

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

5. **Claude AI Summary Issues**
   - Verify Claude API key is valid and has sufficient credits
   - Check that CLAUDE_API_KEY environment variable is set
   - Monitor logs for Claude API failures - system automatically falls back to simple summaries
   - Verify internet connectivity for API calls

### Performance Optimization

- Route maps are cached automatically
- Historical traffic data is limited to last 20 entries
- Database connections use connection pooling
- Background tasks use thread pools for concurrency
- Claude AI summaries use async processing with 30-second timeout
- Priority-based filtering reduces API calls to essential routes only
- Automatic fallback ensures notifications work even if AI service fails

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
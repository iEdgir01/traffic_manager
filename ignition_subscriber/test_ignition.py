#!/usr/bin/env python3
"""Standalone ignition test script - run this from inside the container"""

import asyncio
import sys
import os

# Add the current directory to Python path so imports work
sys.path.insert(0, '/app')
sys.path.insert(0, '/app/discord_bot')

try:
    from discord_bot.discord_notify import post_traffic_alerts_async
    print("✅ Successfully imported discord_notify")
except ImportError as e:
    print(f"❌ Failed to import discord_notify: {e}")
    sys.exit(1)

async def test_traffic_alerts():
    """Test traffic alerts manually"""
    print("🚀 Starting manual ignition test...")
    print("=" * 50)

    try:
        await post_traffic_alerts_async()
        print("=" * 50)
        print("✅ Test completed successfully!")

    except Exception as exc:
        print("=" * 50)
        print(f"❌ Test failed: {exc}")
        import traceback
        print("\n📍 Full traceback:")
        traceback.print_exc()

def main():
    print("Manual Ignition Test Tool")
    print("This will trigger the same traffic alerts as MQTT ignition")
    print("-" * 60)

    try:
        asyncio.run(test_traffic_alerts())
    except KeyboardInterrupt:
        print("\n⏹️  Test interrupted by user")
    except Exception as exc:
        print(f"\n💥 Unexpected error: {exc}")

if __name__ == "__main__":
    main()
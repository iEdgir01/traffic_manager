"""Test script for balance tracker functionality."""

import os
import sys
import logging
from decimal import Decimal

# Add current directory to path for imports
sys.path.insert(0, os.path.dirname(__file__))

from balance_tracker import ClaudeBalanceTracker

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

def test_balance_tracker():
    """Test basic balance tracker functionality."""
    print("🧪 Testing Claude Balance Tracker")
    print("=" * 50)

    # Initialize tracker
    tracker = ClaudeBalanceTracker()

    # Test 1: Initialize balance
    print("\n1️⃣ Testing balance initialization...")
    if tracker.initialize():
        print("✅ Balance initialized successfully")
    else:
        print("ℹ️ Balance already initialized (this is normal)")

    # Test 2: Check current status
    print("\n2️⃣ Testing balance status...")
    status = tracker.get_balance_status()
    if 'error' in status:
        print(f"❌ Error getting balance: {status['error']}")
        return False

    print(f"✅ Current balance: ${status['current_balance']}")
    print(f"✅ Usable balance: ${status['usable_balance']}")
    print(f"✅ Can make requests: {status['can_make_requests']}")

    # Test 3: Simulate Claude API usage
    print("\n3️⃣ Testing usage tracking...")
    mock_claude_response = {
        'usage': {
            'input_tokens': 150,
            'output_tokens': 50
        },
        'model': 'claude-3-5-sonnet-20241022'
    }

    cost = tracker.track_claude_usage(mock_claude_response)
    if cost:
        print(f"✅ Usage tracked successfully: ${cost}")
    else:
        print("❌ Failed to track usage")
        return False

    # Test 4: Check updated status
    print("\n4️⃣ Testing updated balance after usage...")
    new_status = tracker.get_balance_status()
    print(f"✅ Updated balance: ${new_status['current_balance']}")
    print(f"✅ Today's usage: {new_status['usage_today']['requests']} requests, ${new_status['usage_today']['total_cost']}")

    # Test 5: Generate alert message
    print("\n5️⃣ Testing alert message generation...")
    alert_msg = tracker.generate_balance_alert_message(new_status)
    print("✅ Alert message generated:")
    print("-" * 30)
    print(alert_msg)
    print("-" * 30)

    # Test 6: Test balance update
    print("\n6️⃣ Testing balance update...")
    current_balance = new_status['current_balance']
    test_new_balance = current_balance + Decimal('1.00')  # Add $1

    if tracker.update_balance(test_new_balance, source='test'):
        print(f"✅ Balance updated to ${test_new_balance}")

        # Verify update
        final_status = tracker.get_balance_status()
        print(f"✅ Verified balance: ${final_status['current_balance']}")
    else:
        print("❌ Failed to update balance")

    print("\n🎉 All tests completed!")
    return True

def test_can_make_request():
    """Test request permission checking."""
    print("\n🔐 Testing request permissions...")
    tracker = ClaudeBalanceTracker()

    can_make, reason = tracker.can_make_request()
    print(f"Can make request: {can_make}")
    print(f"Reason: {reason}")

if __name__ == "__main__":
    # Ensure we have required environment variables for testing
    required_env = [
        'POSTGRES_HOST', 'POSTGRES_USER', 'POSTGRES_PASSWORD', 'POSTGRES_DB'
    ]

    missing_env = [var for var in required_env if not os.getenv(var)]
    if missing_env:
        print(f"❌ Missing required environment variables: {missing_env}")
        print("Please set these variables or run within the Docker environment")
        sys.exit(1)

    try:
        if test_balance_tracker():
            test_can_make_request()
            print("\n✨ Balance tracker is working correctly!")
        else:
            print("\n💥 Some tests failed!")
            sys.exit(1)
    except Exception as e:
        print(f"\n💥 Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
"""Main balance tracking logic and utilities."""

import os
import logging
from decimal import Decimal
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple

from balance_database import BalanceDatabase

logger = logging.getLogger(__name__)

class ClaudeBalanceTracker:
    """Main balance tracking and management class."""

    def __init__(self):
        self.db = BalanceDatabase()
        self.starting_balance = Decimal(os.getenv('CLAUDE_STARTING_BALANCE', '5.00'))

    def initialize(self) -> bool:
        """Initialize balance tracking system."""
        logger.info("Initializing Claude balance tracker...")
        return self.db.initialize_balance(self.starting_balance)

    def track_claude_usage(self, usage_response: Dict) -> Optional[Decimal]:
        """Track usage from Claude API response.

        Args:
            usage_response: Dict containing 'usage' key with 'input_tokens' and 'output_tokens'

        Returns:
            Total cost of the request, or None if tracking failed
        """
        try:
            usage = usage_response.get('usage', {})
            input_tokens = usage.get('input_tokens', 0)
            output_tokens = usage.get('output_tokens', 0)
            model = usage_response.get('model', 'claude-3-5-sonnet-20241022')

            if input_tokens == 0 and output_tokens == 0:
                logger.warning("No token usage found in response")
                return None

            cost = self.db.log_usage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                model=model,
                request_type='traffic_summary',
                success=True
            )

            logger.info(f"Tracked usage: {input_tokens}in/{output_tokens}out tokens, cost: ${cost}")
            return cost

        except Exception as e:
            logger.error(f"Failed to track Claude usage: {e}")
            return None

    def can_make_request(self) -> Tuple[bool, str]:
        """Check if Claude API request can be made based on balance.

        Returns:
            (can_make_request, reason)
        """
        thresholds = self.db.check_balance_thresholds()

        if not thresholds['balance_available']:
            return False, "No balance available"

        if thresholds['below_disable']:
            return False, f"Balance below disable threshold (${self.db.disable_threshold})"

        return True, "Balance sufficient"

    def get_balance_status(self) -> Dict:
        """Get comprehensive balance status."""
        balance_info = self.db.get_current_balance()
        if not balance_info:
            return {'error': 'No balance information available'}

        thresholds = self.db.check_balance_thresholds()
        usage_today = self.db.get_usage_summary(days=1)
        usage_week = self.db.get_usage_summary(days=7)

        # Calculate runway (days remaining at current usage rate)
        daily_cost = usage_today.get('total_cost', 0) or Decimal('0')
        weekly_avg_cost = (usage_week.get('total_cost', 0) or Decimal('0')) / Decimal('7')

        # Use the higher of today's usage or weekly average for projection
        projected_daily_cost = max(daily_cost, weekly_avg_cost)

        runway_days = None
        runway_months = None
        if projected_daily_cost > 0:
            runway_days = int(thresholds['usable_balance'] / projected_daily_cost)
            runway_months = runway_days / 30.44  # Average days per month

        return {
            'current_balance': balance_info['current_calculated_balance'],
            'usable_balance': thresholds['usable_balance'],
            'buffer_percent': balance_info['buffer_percent'],
            'below_alert': thresholds['below_alert'],
            'below_disable': thresholds['below_disable'],
            'can_make_requests': not thresholds['below_disable'],
            'usage_today': {
                'requests': usage_today.get('total_requests', 0),
                'total_cost': usage_today.get('total_cost', Decimal('0')),
                'avg_cost_per_request': usage_today.get('avg_cost_per_request', Decimal('0'))
            },
            'usage_week': {
                'requests': usage_week.get('total_requests', 0),
                'total_cost': usage_week.get('total_cost', Decimal('0')),
                'avg_cost_per_request': usage_week.get('avg_cost_per_request', Decimal('0'))
            },
            'runway': {
                'days': runway_days,
                'months': round(runway_months, 1) if runway_months else None,
                'projected_daily_cost': projected_daily_cost
            },
            'last_updated': balance_info['last_updated']
        }

    def update_balance(self, new_balance: Decimal, source: str = 'manual') -> bool:
        """Update balance to new total amount."""
        return self.db.topup_balance(new_balance, reason='topup', source=source)

    def generate_balance_alert_message(self, status: Dict) -> str:
        """Generate Discord alert message based on balance status."""
        if status.get('error'):
            return f"⚠️ **Claude Console Balance Error** ⚠️\n\nError: {status['error']}"

        current = status['current_balance']
        usable = status['usable_balance']
        today_cost = status['usage_today']['total_cost']
        week_cost = status['usage_week']['total_cost']
        runway = status['runway']

        alert_type = "🚨 CRITICAL" if status['below_disable'] else "⚠️ LOW"

        message = f"{alert_type} **Claude Console Balance {alert_type.split()[1]}** {alert_type.split()[0]}\n\n"
        message += f"**Estimated Balance:** ${current:.2f}\n"
        message += f"**Usable Balance:** ${usable:.2f} ({status['buffer_percent']}% buffer)\n\n"

        message += "**Usage Summary:**\n"
        message += f"• Today: {status['usage_today']['requests']} requests, ${today_cost:.4f}\n"
        message += f"• This week: {status['usage_week']['requests']} requests, ${week_cost:.4f}\n\n"

        if runway['days'] is not None:
            if runway['days'] < 7:
                message += f"**⏰ Estimated runway: {runway['days']} days**\n"
            elif runway['months'] and runway['months'] < 2:
                message += f"**📅 Estimated runway: {runway['months']} months**\n"
            else:
                message += f"**📅 Estimated runway: {runway['months']} months**\n"

        message += f"• Daily cost projection: ${runway['projected_daily_cost']:.4f}\n\n"

        if status['below_disable']:
            message += "🔴 **LLM usage disabled - balance too low**\n"
        else:
            message += "🟡 **LLM usage still active**\n"

        message += "\n[Console Balance & Usage](https://console.anthropic.com/account/billing)"

        return message

    def generate_daily_report_message(self, status: Dict) -> str:
        """Generate daily usage report message."""
        current = status['current_balance']
        usable = status['usable_balance']
        today_cost = status['usage_today']['total_cost']
        today_requests = status['usage_today']['requests']
        runway = status['runway']

        message = "📊 **Daily Claude Usage Report** 📊\n\n"
        message += f"**Current Balance:** ${current:.2f} (${usable:.2f} usable)\n\n"

        if today_requests > 0:
            message += f"**Today's Usage:**\n"
            message += f"• {today_requests} API requests\n"
            message += f"• ${today_cost:.4f} total cost\n"
            message += f"• ${status['usage_today']['avg_cost_per_request']:.4f} avg per request\n\n"
        else:
            message += "**Today's Usage:** No API requests\n\n"

        if runway['days'] is not None and runway['days'] > 0:
            if runway['days'] < 30:
                message += f"**📅 Estimated runway:** {runway['days']} days\n"
            else:
                message += f"**📅 Estimated runway:** {runway['months']} months\n"
        else:
            message += "**📅 Estimated runway:** Unable to calculate\n"

        return message
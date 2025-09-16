"""Database operations for Claude balance tracking."""

import os
import logging
from datetime import datetime, date
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Dict, List, Tuple
import psycopg2
from psycopg2.extras import RealDictCursor

logger = logging.getLogger(__name__)

class BalanceDatabase:
    """Database operations for Claude balance tracking."""

    def __init__(self):
        self.conn_params = {
            'host': os.getenv('POSTGRES_HOST', 'localhost'),
            'port': int(os.getenv('POSTGRES_PORT', 5432)),
            'user': os.getenv('POSTGRES_USER'),
            'password': os.getenv('POSTGRES_PASSWORD'),
            'database': os.getenv('POSTGRES_DB')
        }

        # Load pricing from environment
        self.input_cost_per_1k = Decimal(os.getenv('CLAUDE_SONNET_INPUT_COST', '0.003'))
        self.output_cost_per_1k = Decimal(os.getenv('CLAUDE_SONNET_OUTPUT_COST', '0.015'))
        self.buffer_percent = int(os.getenv('CLAUDE_BALANCE_BUFFER_PERCENT', '10'))
        self.alert_threshold = Decimal(os.getenv('CLAUDE_ALERT_THRESHOLD', '1.00'))
        self.disable_threshold = Decimal(os.getenv('CLAUDE_DISABLE_THRESHOLD', '0.50'))

    def get_connection(self):
        """Get database connection with RealDictCursor."""
        return psycopg2.connect(cursor_factory=RealDictCursor, **self.conn_params)

    def initialize_balance(self, starting_balance: Decimal) -> bool:
        """Initialize balance tracking with starting balance."""
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    # Check if balance already exists
                    cur.execute("SELECT id FROM claude_balance LIMIT 1")
                    if cur.fetchone():
                        logger.warning("Balance already initialized")
                        return False

                    usable_balance = starting_balance * (Decimal('100') - self.buffer_percent) / Decimal('100')

                    cur.execute("""
                        INSERT INTO claude_balance
                        (starting_balance, current_calculated_balance, usable_balance, buffer_percent)
                        VALUES (%s, %s, %s, %s)
                    """, (starting_balance, starting_balance, usable_balance, self.buffer_percent))

                    # Log initial topup
                    cur.execute("""
                        INSERT INTO claude_topups
                        (old_balance, new_balance, adjustment_amount, reason, source)
                        VALUES (%s, %s, %s, %s, %s)
                    """, (Decimal('0'), starting_balance, starting_balance, 'initial_balance', 'system'))

                    conn.commit()
                    logger.info(f"Initialized balance: ${starting_balance}, usable: ${usable_balance}")
                    return True

        except Exception as e:
            logger.error(f"Failed to initialize balance: {e}")
            return False

    def log_usage(self, input_tokens: int, output_tokens: int, model: str,
                  request_type: str = 'unknown', success: bool = True) -> Optional[Decimal]:
        """Log Claude API usage and return total cost."""
        try:
            # Calculate costs
            input_cost = (Decimal(input_tokens) / Decimal('1000')) * self.input_cost_per_1k
            output_cost = (Decimal(output_tokens) / Decimal('1000')) * self.output_cost_per_1k
            total_cost = input_cost + output_cost

            # Round to 6 decimal places
            input_cost = input_cost.quantize(Decimal('0.000001'), rounding=ROUND_HALF_UP)
            output_cost = output_cost.quantize(Decimal('0.000001'), rounding=ROUND_HALF_UP)
            total_cost = total_cost.quantize(Decimal('0.000001'), rounding=ROUND_HALF_UP)

            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO claude_usage
                        (input_tokens, output_tokens, input_cost, output_cost, total_cost,
                         model, request_type, input_cost_per_1k, output_cost_per_1k, success)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING id
                    """, (input_tokens, output_tokens, input_cost, output_cost, total_cost,
                          model, request_type, self.input_cost_per_1k, self.output_cost_per_1k, success))

                    usage_id = cur.fetchone()['id']

                    # Update current balance
                    self._update_current_balance(cur)
                    conn.commit()

                    logger.debug(f"Logged usage {usage_id}: {input_tokens}in/{output_tokens}out tokens, ${total_cost}")
                    return total_cost

        except Exception as e:
            logger.error(f"Failed to log usage: {e}")
            return None

    def _update_current_balance(self, cur):
        """Update current calculated balance based on usage."""
        # Get starting balance and total usage
        cur.execute("""
            SELECT cb.starting_balance,
                   COALESCE(SUM(ct.adjustment_amount), 0) as total_topups,
                   COALESCE(SUM(cu.total_cost), 0) as total_usage
            FROM claude_balance cb
            LEFT JOIN claude_topups ct ON ct.reason != 'initial_balance'
            LEFT JOIN claude_usage cu ON cu.success = true
            GROUP BY cb.starting_balance
        """)

        result = cur.fetchone()
        if not result:
            return

        starting_balance = result['starting_balance']
        total_topups = result['total_topups'] or Decimal('0')
        total_usage = result['total_usage'] or Decimal('0')

        # Calculate new balance
        current_balance = starting_balance + total_topups - total_usage
        usable_balance = current_balance * (Decimal('100') - self.buffer_percent) / Decimal('100')

        # Update balance record
        cur.execute("""
            UPDATE claude_balance
            SET current_calculated_balance = %s,
                usable_balance = %s,
                last_updated = NOW()
        """, (current_balance, usable_balance))

    def get_current_balance(self) -> Optional[Dict]:
        """Get current balance information."""
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT * FROM claude_balance
                        ORDER BY last_updated DESC
                        LIMIT 1
                    """)
                    return dict(cur.fetchone()) if cur.fetchone() else None

        except Exception as e:
            logger.error(f"Failed to get current balance: {e}")
            return None

    def topup_balance(self, new_total_balance: Decimal, reason: str = 'topup',
                      source: str = 'manual') -> bool:
        """Set new total balance (absolute, not additive)."""
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    # Get current balance
                    cur.execute("SELECT current_calculated_balance FROM claude_balance")
                    current = cur.fetchone()
                    if not current:
                        logger.error("No balance record found")
                        return False

                    old_balance = current['current_calculated_balance']
                    adjustment = new_total_balance - old_balance
                    usable_balance = new_total_balance * (Decimal('100') - self.buffer_percent) / Decimal('100')

                    # Update balance
                    cur.execute("""
                        UPDATE claude_balance
                        SET current_calculated_balance = %s,
                            usable_balance = %s,
                            last_updated = NOW()
                    """, (new_total_balance, usable_balance))

                    # Log topup
                    cur.execute("""
                        INSERT INTO claude_topups
                        (old_balance, new_balance, adjustment_amount, reason, source)
                        VALUES (%s, %s, %s, %s, %s)
                    """, (old_balance, new_total_balance, adjustment, reason, source))

                    conn.commit()
                    logger.info(f"Balance updated: ${old_balance} → ${new_total_balance} (${adjustment:+})")
                    return True

        except Exception as e:
            logger.error(f"Failed to topup balance: {e}")
            return False

    def check_balance_thresholds(self) -> Dict[str, bool]:
        """Check if balance is below alert/disable thresholds."""
        balance_info = self.get_current_balance()
        if not balance_info:
            return {'below_alert': True, 'below_disable': True, 'balance_available': False}

        usable_balance = balance_info['usable_balance']

        return {
            'below_alert': usable_balance < self.alert_threshold,
            'below_disable': usable_balance < self.disable_threshold,
            'balance_available': usable_balance > Decimal('0'),
            'usable_balance': usable_balance,
            'actual_balance': balance_info['current_calculated_balance']
        }

    def get_usage_summary(self, days: int = 1) -> Dict:
        """Get usage summary for the last N days."""
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT
                            COUNT(*) as total_requests,
                            SUM(input_tokens) as total_input_tokens,
                            SUM(output_tokens) as total_output_tokens,
                            SUM(total_cost) as total_cost,
                            AVG(total_cost) as avg_cost_per_request,
                            MIN(timestamp) as first_request,
                            MAX(timestamp) as last_request
                        FROM claude_usage
                        WHERE timestamp >= NOW() - INTERVAL '%s days'
                        AND success = true
                    """, (days,))

                    result = cur.fetchone()
                    return dict(result) if result else {}

        except Exception as e:
            logger.error(f"Failed to get usage summary: {e}")
            return {}
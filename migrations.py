"""Database migration module for Traffic Manager.

This module handles database schema changes and migrations
for existing installations to maintain backward compatibility.
"""

import logging
from typing import Optional
from .traffic_utils import with_db

logger = logging.getLogger(__name__)

@with_db
def migrate_database(conn=None) -> None:
    """Apply database migrations for existing installations.

    Args:
        conn: Database connection (injected by decorator)
    """
    with conn.cursor() as cursor:
        # Migration 1: Add priority column to routes table
        _migrate_add_priority_column(cursor)

def _migrate_add_priority_column(cursor) -> None:
    """Add priority column to existing routes table if it doesn't exist."""
    # Check if priority column exists
    cursor.execute("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'routes' AND column_name = 'priority'
    """)

    if not cursor.fetchone():
        logger.info("Adding priority column to existing routes table...")

        # Add priority column with default value
        cursor.execute("""
            ALTER TABLE routes ADD COLUMN priority VARCHAR(10) DEFAULT 'Normal'
        """)

        # Update any existing routes to have Normal priority
        cursor.execute("""
            UPDATE routes SET priority = 'Normal' WHERE priority IS NULL
        """)

        # Add constraint to ensure only High or Normal values
        cursor.execute("""
            ALTER TABLE routes ADD CONSTRAINT check_priority
            CHECK (priority IN ('High', 'Normal'))
        """)

        # Create index on priority for faster filtering
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_routes_priority ON routes(priority)
        """)

        logger.info("Priority column migration completed successfully")
    else:
        logger.debug("Priority column already exists, skipping migration")
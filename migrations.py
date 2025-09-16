"""Database migration module for Traffic Manager.

This module handles database schema changes and migrations
for existing installations to maintain backward compatibility.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

def migrate_database() -> None:
    """Apply database migrations for existing installations.

    This function will be called with @with_db decorator from traffic_utils.
    """
    # Import here to avoid circular imports
    from traffic_utils import with_db

    @with_db
    def _do_migration(conn=None):
        try:
            logger.info("Starting database migrations...")
            with conn.cursor() as cursor:
                # Migration 1: Add priority column to routes table
                _migrate_add_priority_column(cursor)
            logger.info("Database migrations completed successfully")
        except Exception as e:
            logger.error(f"Database migration failed: {e}")
            raise

    _do_migration()

def _migrate_add_priority_column(cursor) -> None:
    """Add priority column to existing routes table if it doesn't exist."""
    try:
        # Use a more direct approach - try to add the column and catch the error if it exists
        try:
            logger.info("Attempting to add priority column to routes table...")

            cursor.execute("""
                ALTER TABLE routes ADD COLUMN IF NOT EXISTS priority VARCHAR(10) DEFAULT 'Normal'
            """)

            # Update any existing routes to have Normal priority (in case column was added)
            cursor.execute("""
                UPDATE routes SET priority = 'Normal' WHERE priority IS NULL
            """)

            logger.info("Priority column added successfully")

        except Exception as add_error:
            logger.warning(f"Could not add priority column (may already exist): {add_error}")

        # Add constraint - use IF NOT EXISTS equivalent
        try:
            cursor.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint
                        WHERE conname = 'check_priority'
                    ) THEN
                        ALTER TABLE routes ADD CONSTRAINT check_priority
                        CHECK (priority IN ('High', 'Normal'));
                    END IF;
                END $$;
            """)
        except Exception as e:
            logger.warning(f"Could not add priority constraint: {e}")

        # Create index on priority for faster filtering
        try:
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_routes_priority ON routes(priority)
            """)
        except Exception as e:
            logger.warning(f"Could not create priority index: {e}")

        logger.info("Priority column migration completed")

    except Exception as e:
        logger.error(f"Failed to migrate priority column: {e}")
        raise
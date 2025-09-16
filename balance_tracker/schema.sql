-- Claude Balance Tracking Database Schema
-- This schema tracks Claude API usage, costs, and balance management

-- Balance tracking table - stores current balance state
CREATE TABLE IF NOT EXISTS claude_balance (
    id SERIAL PRIMARY KEY,
    starting_balance DECIMAL(10,4) NOT NULL,
    current_calculated_balance DECIMAL(10,4) NOT NULL,
    usable_balance DECIMAL(10,4) NOT NULL,  -- current_calculated_balance * (1 - buffer_percent)
    buffer_percent INTEGER DEFAULT 10,      -- safety buffer percentage
    last_updated TIMESTAMP DEFAULT NOW(),
    created_at TIMESTAMP DEFAULT NOW()
);

-- Usage tracking per API call
CREATE TABLE IF NOT EXISTS claude_usage (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMP DEFAULT NOW(),
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    input_cost DECIMAL(10,6) NOT NULL,      -- cost for input tokens
    output_cost DECIMAL(10,6) NOT NULL,     -- cost for output tokens
    total_cost DECIMAL(10,6) NOT NULL,      -- input_cost + output_cost
    model VARCHAR(50) NOT NULL,             -- e.g., 'claude-3-5-sonnet-20241022'
    request_type VARCHAR(100),              -- e.g., 'traffic_summary'
    input_cost_per_1k DECIMAL(10,6),       -- pricing at time of request
    output_cost_per_1k DECIMAL(10,6),      -- pricing at time of request
    success BOOLEAN DEFAULT TRUE           -- track failed requests
);

-- Balance top-ups and manual adjustments
CREATE TABLE IF NOT EXISTS claude_topups (
    id SERIAL PRIMARY KEY,
    old_balance DECIMAL(10,4),
    new_balance DECIMAL(10,4) NOT NULL,
    adjustment_amount DECIMAL(10,4),        -- new_balance - old_balance
    reason VARCHAR(200),                    -- 'topup', 'correction', 'initial'
    timestamp TIMESTAMP DEFAULT NOW(),
    source VARCHAR(50) DEFAULT 'manual'     -- 'discord_button', 'manual', 'api'
);

-- Daily usage summaries for reporting
CREATE TABLE IF NOT EXISTS claude_daily_usage (
    id SERIAL PRIMARY KEY,
    date DATE NOT NULL UNIQUE,
    total_requests INTEGER DEFAULT 0,
    total_input_tokens INTEGER DEFAULT 0,
    total_output_tokens INTEGER DEFAULT 0,
    total_cost DECIMAL(10,4) DEFAULT 0,
    average_cost_per_request DECIMAL(10,6),
    created_at TIMESTAMP DEFAULT NOW()
);

-- Balance alerts log
CREATE TABLE IF NOT EXISTS claude_balance_alerts (
    id SERIAL PRIMARY KEY,
    alert_type VARCHAR(50) NOT NULL,        -- 'low_balance', 'critical_balance', 'disabled'
    balance_at_alert DECIMAL(10,4),
    usable_balance_at_alert DECIMAL(10,4),
    message_sent TEXT,
    discord_message_id VARCHAR(100),        -- for updating messages
    resolved BOOLEAN DEFAULT FALSE,
    timestamp TIMESTAMP DEFAULT NOW()
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_claude_usage_timestamp ON claude_usage(timestamp);
CREATE INDEX IF NOT EXISTS idx_claude_usage_model ON claude_usage(model);
CREATE INDEX IF NOT EXISTS idx_claude_daily_usage_date ON claude_daily_usage(date);
CREATE INDEX IF NOT EXISTS idx_claude_balance_alerts_timestamp ON claude_balance_alerts(timestamp);
CREATE INDEX IF NOT EXISTS idx_claude_balance_alerts_resolved ON claude_balance_alerts(resolved);

-- Initial balance record (will be inserted by the application)
-- INSERT INTO claude_balance (starting_balance, current_calculated_balance, usable_balance)
-- VALUES (5.00, 5.00, 4.50);  -- 5.00 with 10% buffer = 4.50 usable
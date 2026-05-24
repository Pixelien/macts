-- =============================================================================
-- MACTS PostgreSQL Initialization Script
-- =============================================================================
-- docker-compose ile postgres ilk başlatıldığında otomatik çalışır.
-- Uretim ortamlarında Alembic migration'lar tercih edilmelidir.
-- =============================================================================

-- Trade history (executed trades)
CREATE TABLE IF NOT EXISTS trades (
    id BIGSERIAL PRIMARY KEY,
    client_order_id VARCHAR(64) NOT NULL,
    exchange_order_id BIGINT,
    symbol VARCHAR(32) NOT NULL,
    side VARCHAR(8) NOT NULL,
    quantity NUMERIC(36, 18) NOT NULL,
    price NUMERIC(36, 18) NOT NULL,
    fee NUMERIC(36, 18) DEFAULT 0,
    realized_pnl NUMERIC(36, 18) DEFAULT 0,
    executed_at TIMESTAMPTZ NOT NULL,
    mode VARCHAR(16) NOT NULL,                  -- testnet | paper | live
    parent_signal_id UUID,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_trades_symbol_time ON trades(symbol, executed_at DESC);
CREATE INDEX IF NOT EXISTS idx_trades_mode ON trades(mode);

-- Orders state machine
CREATE TABLE IF NOT EXISTS orders (
    id BIGSERIAL PRIMARY KEY,
    client_order_id VARCHAR(64) UNIQUE NOT NULL,
    exchange_order_id BIGINT,
    symbol VARCHAR(32) NOT NULL,
    side VARCHAR(8) NOT NULL,
    order_type VARCHAR(32) NOT NULL,
    quantity NUMERIC(36, 18) NOT NULL,
    price NUMERIC(36, 18),
    stop_price NUMERIC(36, 18),
    time_in_force VARCHAR(8) DEFAULT 'GTC',
    reduce_only BOOLEAN DEFAULT FALSE,
    status VARCHAR(32) NOT NULL,
    filled_quantity NUMERIC(36, 18) DEFAULT 0,
    average_fill_price NUMERIC(36, 18),
    parent_signal_id UUID,
    mode VARCHAR(16) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_symbol ON orders(symbol);

-- Positions
CREATE TABLE IF NOT EXISTS positions (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(32) NOT NULL,
    side VARCHAR(8) NOT NULL,
    entry_price NUMERIC(36, 18) NOT NULL,
    quantity NUMERIC(36, 18) NOT NULL,
    leverage INTEGER NOT NULL,
    margin NUMERIC(36, 18) NOT NULL,
    stop_loss NUMERIC(36, 18),
    take_profit NUMERIC(36, 18),
    unrealized_pnl NUMERIC(36, 18) DEFAULT 0,
    realized_pnl NUMERIC(36, 18) DEFAULT 0,
    opened_at TIMESTAMPTZ NOT NULL,
    closed_at TIMESTAMPTZ,
    mode VARCHAR(16) NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_positions_open ON positions(symbol)
    WHERE closed_at IS NULL;

-- Cooldowns (restart-resilient)
CREATE TABLE IF NOT EXISTS cooldowns (
    id BIGSERIAL PRIMARY KEY,
    reason VARCHAR(64) NOT NULL,                -- daily_loss | weekly_loss | flash_crash | ...
    started_at TIMESTAMPTZ DEFAULT now(),
    ends_at TIMESTAMPTZ NOT NULL,
    metadata JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_cooldowns_active ON cooldowns(ends_at);

-- Audit log (append-only)
CREATE TABLE IF NOT EXISTS audit_log (
    id BIGSERIAL PRIMARY KEY,
    event_type VARCHAR(64) NOT NULL,
    agent_name VARCHAR(64),
    severity VARCHAR(16) DEFAULT 'info',
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_audit_event_time ON audit_log(event_type, created_at DESC);

-- Signals (decisions audit trail)
CREATE TABLE IF NOT EXISTS signals (
    id UUID PRIMARY KEY,
    symbol VARCHAR(32) NOT NULL,
    side VARCHAR(8) NOT NULL,
    confidence NUMERIC(5, 4) NOT NULL,
    expected_return NUMERIC(10, 6),
    expected_rr NUMERIC(10, 4),
    suggested_entry NUMERIC(36, 18),
    suggested_sl NUMERIC(36, 18),
    suggested_tp NUMERIC(36, 18),
    horizon_minutes INTEGER,
    risk_approved BOOLEAN,
    rejection_reasons TEXT[],
    reasoning JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_signals_symbol_time ON signals(symbol, created_at DESC);

-- Model registry shadow copy (MLflow ile sync edilir, hızlı sorgu için)
CREATE TABLE IF NOT EXISTS model_versions (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(32) NOT NULL,
    model_type VARCHAR(32) NOT NULL,            -- tft | ppo
    version VARCHAR(32) NOT NULL,
    stage VARCHAR(16) NOT NULL,                 -- staging | production | archived
    mlflow_run_id VARCHAR(64),
    metrics JSONB DEFAULT '{}'::jsonb,
    deployed_at TIMESTAMPTZ,
    archived_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (symbol, model_type, version)
);

-- Performance metrics snapshot (zaman serisi)
CREATE TABLE IF NOT EXISTS performance_snapshots (
    id BIGSERIAL PRIMARY KEY,
    snapshot_date DATE NOT NULL,
    mode VARCHAR(16) NOT NULL,
    total_balance NUMERIC(36, 18),
    realized_pnl NUMERIC(36, 18),
    unrealized_pnl NUMERIC(36, 18),
    sharpe_ratio NUMERIC(10, 4),
    max_drawdown_pct NUMERIC(10, 4),
    win_rate NUMERIC(5, 4),
    profit_factor NUMERIC(10, 4),
    metadata JSONB DEFAULT '{}'::jsonb,
    UNIQUE (snapshot_date, mode)
);

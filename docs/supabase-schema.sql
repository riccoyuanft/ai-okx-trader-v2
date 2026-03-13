-- AI OKX Trader v2 — Supabase Schema
-- Run this in Supabase SQL Editor to create all required tables.

-- ============================================================
-- users
-- ============================================================
CREATE TABLE IF NOT EXISTS users (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    unique_id               text UNIQUE NOT NULL,
    totp_secret             text NOT NULL,
    -- Testnet (模拟盘) credentials
    okx_api_key             text NOT NULL DEFAULT '',
    okx_secret_key          text NOT NULL DEFAULT '',
    okx_passphrase          text NOT NULL DEFAULT '',
    -- Live (实盘) credentials (nullable until configured)
    okx_live_api_key        text,
    okx_live_secret_key     text,
    okx_live_passphrase     text,
    -- Active mode: true = testnet, false = live
    okx_testnet             boolean DEFAULT true,
    -- Webhook notifications (dingtalk / wecom / feishu)
    notify_provider         text DEFAULT 'dingtalk',
    notify_webhook          text,
    is_active               boolean DEFAULT true,
    created_at              timestamptz DEFAULT now(),
    last_login              timestamptz
);

-- Migration: add live credential columns if upgrading from earlier schema
ALTER TABLE users ADD COLUMN IF NOT EXISTS okx_live_api_key    text;
ALTER TABLE users ADD COLUMN IF NOT EXISTS okx_live_secret_key text;
ALTER TABLE users ADD COLUMN IF NOT EXISTS okx_live_passphrase text;

-- Migration: billing fields
ALTER TABLE users ADD COLUMN IF NOT EXISTS credits_balance  integer NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS plan_expires_at  timestamptz;

-- IMPORTANT: After running the migration above, activate existing users manually.
-- Run this in Supabase SQL Editor to give all current users a 1-year plan + 10000 credits:
--   UPDATE users SET plan_expires_at = now() + interval '1 year', credits_balance = 10000
--   WHERE plan_expires_at IS NULL;
-- Or for a specific user (replace <unique_id>):
--   UPDATE users SET plan_expires_at = now() + interval '1 year', credits_balance = 10000
--   WHERE unique_id = '<unique_id>';

CREATE INDEX IF NOT EXISTS idx_users_unique_id ON users(unique_id);

-- ============================================================
-- strategies
-- ============================================================
CREATE TABLE IF NOT EXISTS strategies (
    id                          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                     uuid REFERENCES users(id) ON DELETE CASCADE,
    name                        text NOT NULL,
    symbol                      text NOT NULL DEFAULT 'BTC-USDT-SWAP',
    timeframe                   text NOT NULL DEFAULT '15m',
    nl_strategy                 text,

    default_leverage            int NOT NULL DEFAULT 10,
    max_leverage                int NOT NULL DEFAULT 20,
    position_size_pct           float NOT NULL DEFAULT 30.0,

    ai_provider                 text NOT NULL DEFAULT 'qwen',
    ai_api_key                  text,
    ai_base_url                 text,
    ai_model                    text,

    max_daily_loss_pct          float NOT NULL DEFAULT 5.0,
    max_consecutive_losses      int NOT NULL DEFAULT 3,
    max_position_pct            float NOT NULL DEFAULT 50.0,
    stop_on_breach              boolean NOT NULL DEFAULT true,

    enable_news_analysis        boolean NOT NULL DEFAULT false,
    liq_guard_pct               float NOT NULL DEFAULT 30.0,

    is_active                   boolean NOT NULL DEFAULT false,
    created_at                  timestamptz DEFAULT now(),
    updated_at                  timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_strategies_user_id ON strategies(user_id);
CREATE INDEX IF NOT EXISTS idx_strategies_active ON strategies(user_id, is_active);

ALTER TABLE strategies ADD COLUMN IF NOT EXISTS liq_guard_pct float NOT NULL DEFAULT 30.0;

-- ============================================================
-- trade_logs
-- ============================================================
CREATE TABLE IF NOT EXISTS trade_logs (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         uuid REFERENCES users(id) ON DELETE CASCADE,
    strategy_id     uuid REFERENCES strategies(id) ON DELETE SET NULL,
    symbol          text NOT NULL,
    direction       text NOT NULL CHECK (direction IN ('long', 'short')),
    leverage        int NOT NULL,
    margin_mode     text NOT NULL DEFAULT 'isolated',

    entry_price     numeric NOT NULL,
    exit_price      numeric,
    qty             numeric NOT NULL,
    pnl_usdt        numeric,
    pnl_pct         numeric,

    open_time       timestamptz NOT NULL DEFAULT now(),
    close_time      timestamptz,
    close_reason    text CHECK (close_reason IN ('sl', 'tp', 'ai_close', 'manual', 'liquidation_guard', NULL)),

    algo_order_id   text,
    ai_reasoning    text,
    news_context    text,
    stop_loss       numeric
);

CREATE INDEX IF NOT EXISTS idx_trade_logs_user_id ON trade_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_trade_logs_open_time ON trade_logs(user_id, open_time DESC);
CREATE INDEX IF NOT EXISTS idx_trade_logs_open ON trade_logs(user_id, close_time) WHERE close_time IS NULL;

ALTER TABLE trade_logs ADD COLUMN IF NOT EXISTS stop_loss numeric;

ALTER TABLE strategies ADD COLUMN IF NOT EXISTS liq_guard_pct float NOT NULL DEFAULT 30.0;

ALTER TABLE users ADD COLUMN credits_balance INTEGER DEFAULT 0;
ALTER TABLE users ADD COLUMN plan_expires_at TIMESTAMPTZ;

-- ============================================================
-- ============================================================
-- credit_transactions  (audit log for every credit change)
-- ============================================================
CREATE TABLE IF NOT EXISTS credit_transactions (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         uuid REFERENCES users(id) ON DELETE CASCADE,
    amount          integer NOT NULL,       -- negative = deduction, positive = top-up
    balance_after   integer NOT NULL,
    note            text,
    created_at      timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_credit_tx_user ON credit_transactions(user_id, created_at DESC);

-- ============================================================
-- Row Level Security (optional but recommended for Supabase)
-- Disable if using service role key exclusively from the backend.
-- ============================================================
-- ALTER TABLE users ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE strategies ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE trade_logs ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE credit_transactions ENABLE ROW LEVEL SECURITY;

-- ============================================================
-- Migration: Add live (实盘) credentials + webhook notifications
-- Run this in Supabase SQL Editor on existing databases.
-- Safe to run multiple times (uses ADD COLUMN IF NOT EXISTS).
-- ============================================================

-- 1. Live (实盘) OKX credentials
ALTER TABLE users ADD COLUMN IF NOT EXISTS okx_live_api_key    text;
ALTER TABLE users ADD COLUMN IF NOT EXISTS okx_live_secret_key text;
ALTER TABLE users ADD COLUMN IF NOT EXISTS okx_live_passphrase text;

-- 2. Webhook notification config
ALTER TABLE users ADD COLUMN IF NOT EXISTS notify_provider text DEFAULT 'dingtalk';
ALTER TABLE users ADD COLUMN IF NOT EXISTS notify_webhook  text;

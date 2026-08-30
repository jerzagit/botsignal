-- SignalBot Database Initialization
-- Fresh installation schema only — no user/runtime data.
--
-- Fresh installs:
--   Execute this file against an empty MySQL instance (or let Docker
--   apply it via docker-entrypoint-initdb.d). You do NOT need to run
--   historical migrate_*.sql files afterward — their final definitions
--   are already incorporated below.
--
-- Existing installs:
--   Keep using db/migrate_*.sql to upgrade older databases.
--   Do not re-run non-idempotent ALTER migrations after columns exist.
--
-- Privacy:
--   Schema-only. No INSERT of trades, Telegram IDs, MT5 logins, or secrets.
--
-- Engine: MySQL 8.x (utf8mb4 / utf8mb4_unicode_ci)

CREATE DATABASE IF NOT EXISTS botsignal
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE botsignal;

-- --------------------------------------------------
-- Core signal / trade tables
-- (signals before trades: FK trades.signal_id -> signals.signal_id)
-- --------------------------------------------------

CREATE TABLE IF NOT EXISTS signals (
    signal_id   VARCHAR(64)    PRIMARY KEY,
    received_at DATETIME       DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME       DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    symbol      VARCHAR(20)    NOT NULL,
    direction   VARCHAR(4)     NOT NULL,
    entry_low   DECIMAL(12,5)  NOT NULL,
    entry_high  DECIMAL(12,5)  NOT NULL,
    sl          DECIMAL(12,5)  NOT NULL,
    tps         JSON,
    raw_text    TEXT,
    source_id   VARCHAR(32)    DEFAULT NULL,
    source_name VARCHAR(64)    DEFAULT NULL,
    parser_profile VARCHAR(32) DEFAULT NULL,
    telegram_chat_id VARCHAR(64) DEFAULT NULL,
    source_risk_percent DECIMAL(6,4) DEFAULT NULL,
    status      VARCHAR(16)    DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS trades (
    id          INT            AUTO_INCREMENT PRIMARY KEY,
    signal_id   VARCHAR(64),
    ticket      BIGINT         UNIQUE,
    lot         DECIMAL(8,2),
    entry_price DECIMAL(12,5),
    close_price DECIMAL(12,5),
    outcome     VARCHAR(8),
    profit      DECIMAL(10,2),
    closed_at   DATETIME,
    created_at  DATETIME       DEFAULT CURRENT_TIMESTAMP,
    entry_mode  VARCHAR(12)    DEFAULT NULL,  -- layered_dca / direct / NULL for legacy rows
    layer_num   TINYINT        DEFAULT NULL,  -- 1,2,3... for DCA layers (NULL for direct)
    FOREIGN KEY (signal_id) REFERENCES signals(signal_id)
        ON DELETE SET NULL
);

-- --------------------------------------------------
-- Mapping / SNR support tables
-- --------------------------------------------------

CREATE TABLE IF NOT EXISTS snr_levels (
    id          INT            AUTO_INCREMENT PRIMARY KEY,
    symbol      VARCHAR(20)    NOT NULL,
    price       DECIMAL(12,5)  NOT NULL,
    valid_date  DATE           NOT NULL,
    created_at  DATETIME       DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_symbol_date (symbol, valid_date)
);

CREATE TABLE IF NOT EXISTS mapping_zones (
    id          INT            AUTO_INCREMENT PRIMARY KEY,
    symbol      VARCHAR(20)    NOT NULL,
    direction   VARCHAR(4)     NOT NULL,
    zone_low    DECIMAL(12,5)  NOT NULL,
    zone_high   DECIMAL(12,5)  NOT NULL,
    sl          DECIMAL(12,5)  NOT NULL,
    tp          DECIMAL(12,5)  NOT NULL,
    valid_date  DATE           NOT NULL,
    fired       BOOLEAN        DEFAULT FALSE,
    signal_id   VARCHAR(64)    NULL,
    created_at  DATETIME       DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_valid_date (valid_date),
    INDEX idx_active (valid_date, fired)
);

-- --------------------------------------------------
-- Candle / market data tables
-- (also available via migrate_add_candles.sql for older DBs)
-- --------------------------------------------------

CREATE TABLE IF NOT EXISTS candles (
    id          INT            AUTO_INCREMENT PRIMARY KEY,
    symbol      VARCHAR(20)    NOT NULL,
    timeframe   VARCHAR(8)     NOT NULL,          -- 'H1', 'H4', 'D1', etc.
    candle_time DATETIME       NOT NULL,          -- candle open time (UTC)
    open        DECIMAL(12,5)  NOT NULL,
    high        DECIMAL(12,5)  NOT NULL,
    low         DECIMAL(12,5)  NOT NULL,
    close       DECIMAL(12,5)  NOT NULL,
    volume      BIGINT         DEFAULT 0,
    saved_at    DATETIME       DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_candle (symbol, timeframe, candle_time),
    INDEX idx_symbol_tf_time (symbol, timeframe, candle_time)
);

-- --------------------------------------------------
-- Guard / audit event tables
-- (source_id also added historically by migrate_add_signal_sources.sql)
-- --------------------------------------------------

CREATE TABLE IF NOT EXISTS guard_events (
    id             INT           AUTO_INCREMENT PRIMARY KEY,
    fired_at       DATETIME      DEFAULT CURRENT_TIMESTAMP,
    guard_name     VARCHAR(32)   NOT NULL,
    signal_id      VARCHAR(64),
    symbol         VARCHAR(20),
    direction      VARCHAR(4),
    source_id      VARCHAR(32),
    reason         TEXT,
    value_actual   VARCHAR(64),
    value_required VARCHAR(64),
    INDEX idx_fired_at   (fired_at),
    INDEX idx_guard_name (guard_name)
);

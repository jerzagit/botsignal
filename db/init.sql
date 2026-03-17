-- SignalBot — MySQL schema
-- Auto-executed by Docker on first run (docker-entrypoint-initdb.d)

CREATE DATABASE IF NOT EXISTS botsignal
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE botsignal;

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
    entry_mode  VARCHAR(12)    DEFAULT NULL,  -- 'layered_dca' | 'direct' | NULL=old data
    layer_num   TINYINT        DEFAULT NULL,  -- 1,2,3... for DCA layers; NULL for direct
    FOREIGN KEY (signal_id) REFERENCES signals(signal_id)
        ON DELETE SET NULL
);

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

CREATE TABLE IF NOT EXISTS guard_events (
    id             INT           AUTO_INCREMENT PRIMARY KEY,
    fired_at       DATETIME      DEFAULT CURRENT_TIMESTAMP,
    guard_name     VARCHAR(32)   NOT NULL,
    signal_id      VARCHAR(64),
    symbol         VARCHAR(20),
    direction      VARCHAR(4),
    reason         TEXT,
    value_actual   VARCHAR(64),
    value_required VARCHAR(64),
    INDEX idx_fired_at   (fired_at),
    INDEX idx_guard_name (guard_name)
);

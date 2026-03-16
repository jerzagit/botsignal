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
    FOREIGN KEY (signal_id) REFERENCES signals(signal_id)
        ON DELETE SET NULL
);

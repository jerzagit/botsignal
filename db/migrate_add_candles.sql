-- Run this once to add the candles table to an existing botsignal database.
-- docker exec -i mysql-docker mysql -uroot -prootpass botsignal < db/migrate_add_candles.sql

USE botsignal;

CREATE TABLE IF NOT EXISTS candles (
    id          INT            AUTO_INCREMENT PRIMARY KEY,
    symbol      VARCHAR(20)    NOT NULL,
    timeframe   VARCHAR(8)     NOT NULL,
    candle_time DATETIME       NOT NULL,
    open        DECIMAL(12,5)  NOT NULL,
    high        DECIMAL(12,5)  NOT NULL,
    low         DECIMAL(12,5)  NOT NULL,
    close       DECIMAL(12,5)  NOT NULL,
    volume      BIGINT         DEFAULT 0,
    saved_at    DATETIME       DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_candle (symbol, timeframe, candle_time),
    INDEX idx_symbol_tf_time (symbol, timeframe, candle_time)
);

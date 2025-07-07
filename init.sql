-- 用户表（确保有token字段，默认5）
CREATE TABLE IF NOT EXISTS users (
    user_id BIGINT PRIMARY KEY,
    username TEXT,
    phone TEXT,
    points INTEGER DEFAULT 0,
    plays INTEGER DEFAULT 0,
    inviter TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_game_time TIMESTAMP,
    blocked BOOLEAN DEFAULT FALSE,
    token INTEGER DEFAULT 5   -- 新用户初始5个token
);

-- 如果已有users表无token字段，兼容升级
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'users' AND column_name = 'token'
    ) THEN
        ALTER TABLE users ADD COLUMN token INTEGER DEFAULT 5;
    END IF;
END$$;

-- 已有老用户的token字段为空时初始化为5
UPDATE users SET token = 5 WHERE token IS NULL;

-- 游戏记录表，含game_name字段
CREATE TABLE IF NOT EXISTS game_logs (
    id SERIAL PRIMARY KEY,
    user_id BIGINT,
    user_roll INTEGER,
    bot_roll INTEGER,
    result TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    game_name TEXT
);

-- 兼容老game_logs表无game_name字段
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'game_logs' AND column_name = 'game_name'
    ) THEN
        ALTER TABLE game_logs ADD COLUMN game_name TEXT;
    END IF;
END$$;

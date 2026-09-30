-- Telegram: the chat a person linked, and the one-time code that links it.
ALTER TABLE users ADD COLUMN tg_chat_id TEXT;
ALTER TABLE users ADD COLUMN tg_code TEXT;
ALTER TABLE users ADD COLUMN tg_code_exp INTEGER;
CREATE UNIQUE INDEX users_tg_code ON users (tg_code) WHERE tg_code IS NOT NULL;

-- When each person last used the site (any signed-in request), refreshed at most every 5 minutes.
ALTER TABLE users ADD COLUMN last_seen_at INTEGER;
UPDATE users SET last_seen_at = last_login_at;

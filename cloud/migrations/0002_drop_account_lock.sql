-- Account-wide lockouts are replaced by per-network failure counters in `rate`
-- (see src/auth.js login), so a stranger can no longer lock an owner out.
ALTER TABLE users DROP COLUMN failed_logins;
ALTER TABLE users DROP COLUMN locked_until;

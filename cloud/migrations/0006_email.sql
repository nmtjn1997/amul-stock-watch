-- Email alerts: the verified address, and a pending one waiting for its code.
ALTER TABLE users ADD COLUMN alert_email TEXT;
ALTER TABLE users ADD COLUMN email_pending TEXT;
ALTER TABLE users ADD COLUMN email_code_hash TEXT;
ALTER TABLE users ADD COLUMN email_code_exp INTEGER;

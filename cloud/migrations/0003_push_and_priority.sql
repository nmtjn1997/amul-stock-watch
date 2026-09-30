-- Browser push is the default alert channel: ntfy.sh without an account shares a daily
-- quota per sending IP, and all Workers share Cloudflare's IPs, so it is opt-in now.

CREATE TABLE push_subs (
  id         INTEGER PRIMARY KEY,
  user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  endpoint   TEXT NOT NULL UNIQUE,
  p256dh     TEXT NOT NULL,
  auth       TEXT NOT NULL,
  label      TEXT NOT NULL,              -- "iPhone Safari", "Android Chrome", for the device list
  created_at INTEGER NOT NULL
);
CREATE INDEX push_subs_user ON push_subs(user_id);

ALTER TABLE users ADD COLUMN ntfy_on INTEGER NOT NULL DEFAULT 0;

-- Order products are offered in; the admin can move them. Lower comes first.
ALTER TABLE products ADD COLUMN sort INTEGER NOT NULL DEFAULT 100;
UPDATE products SET sort = 1 WHERE alias = 'amul-high-protein-rose-lassi-200-ml-or-pack-of-30';
UPDATE products SET sort = 2 WHERE alias = 'amul-high-protein-plain-lassi-200-ml-or-pack-of-30';
UPDATE products SET sort = 3 WHERE alias = 'amul-high-protein-buttermilk-200-ml-or-pack-of-30';
UPDATE products SET sort = 10 WHERE alias = 'amul-high-protein-blueberry-shake-200-ml-or-pack-of-30';
UPDATE products SET sort = 11 WHERE alias LIKE 'amul-kool-protein-milkshake%';
UPDATE products SET sort = 12 WHERE alias LIKE 'amul-high-protein-milk%';
UPDATE products SET sort = 13 WHERE alias LIKE 'amul-high-protein-paneer%';
UPDATE products SET sort = 14 WHERE alias LIKE '%whey-protein%';

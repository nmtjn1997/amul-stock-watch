-- Amul Stock Watch, hosted edition (Cloudflare D1). Every user-owned row carries user_id,
-- and every query in src/ filters on it: that is the whole access-control model.

CREATE TABLE users (
  id            INTEGER PRIMARY KEY,
  username      TEXT UNIQUE,                 -- NULL for Google-only accounts
  pass_hash     TEXT,                        -- pbkdf2$<iterations>$<salt b64>$<hash b64>
  google_sub    TEXT UNIQUE,
  email         TEXT,
  display_name  TEXT NOT NULL,
  role          TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'admin')),
  ntfy_topic    TEXT NOT NULL,               -- random per user, so topics are not guessable
  webhook_url   TEXT,                        -- optional Discord or Slack incoming webhook
  disabled      INTEGER NOT NULL DEFAULT 0,
  failed_logins INTEGER NOT NULL DEFAULT 0,
  locked_until  INTEGER NOT NULL DEFAULT 0,
  created_at    INTEGER NOT NULL,
  last_login_at INTEGER
);

CREATE TABLE sessions (
  token_hash TEXT PRIMARY KEY,               -- sha256 of the cookie value; the cookie itself is never stored
  user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL
);
CREATE INDEX sessions_user ON sessions(user_id);

CREATE TABLE products (
  alias      TEXT PRIMARY KEY,
  label      TEXT NOT NULL,
  enabled    INTEGER NOT NULL DEFAULT 1,
  created_at INTEGER NOT NULL
);

CREATE TABLE watches (
  id         INTEGER PRIMARY KEY,
  user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  pincode    TEXT NOT NULL CHECK (length(pincode) = 6),
  alias      TEXT NOT NULL REFERENCES products(alias),
  enabled    INTEGER NOT NULL DEFAULT 1,
  alerted    INTEGER NOT NULL DEFAULT 0,     -- 1 = told about the current restock; reset when it sells out
  created_at INTEGER NOT NULL,
  UNIQUE (user_id, pincode, alias)
);
CREATE INDEX watches_pair ON watches(pincode, alias);

CREATE TABLE zones (                         -- pincode -> Amul delivery region
  pincode    TEXT PRIMARY KEY,
  record_id  TEXT,
  store      TEXT,
  valid      INTEGER NOT NULL,               -- 0 = Amul does not deliver here
  checked_at INTEGER NOT NULL
);

CREATE TABLE stock (
  pincode    TEXT NOT NULL,
  alias      TEXT NOT NULL,
  in_stock   INTEGER NOT NULL,
  qty        INTEGER NOT NULL,
  price      REAL,
  changed_at INTEGER NOT NULL,
  PRIMARY KEY (pincode, alias)
);

CREATE TABLE alert_log (
  id       INTEGER PRIMARY KEY,
  user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  ts       INTEGER NOT NULL,
  kind     TEXT NOT NULL,                    -- stock | test
  product  TEXT NOT NULL,
  pincodes TEXT NOT NULL,
  result   TEXT NOT NULL
);
CREATE INDEX alert_log_user ON alert_log(user_id, ts);

CREATE TABLE rate (                          -- fixed-window counters for abuse limits
  key          TEXT PRIMARY KEY,
  count        INTEGER NOT NULL,
  window_start INTEGER NOT NULL
);

CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

-- Seed catalog (real aliases from the shop). The admin adds more from the UI.
INSERT INTO products (alias, label, created_at) VALUES
  ('amul-high-protein-rose-lassi-200-ml-or-pack-of-30', 'High Protein Rose Lassi, 30 x 200 mL', unixepoch()),
  ('amul-high-protein-plain-lassi-200-ml-or-pack-of-30', 'High Protein Plain Lassi, 30 x 200 mL', unixepoch()),
  ('amul-high-protein-buttermilk-200-ml-or-pack-of-30', 'High Protein Buttermilk, 30 x 200 mL', unixepoch()),
  ('amul-high-protein-blueberry-shake-200-ml-or-pack-of-30', 'High Protein Blueberry Shake, 30 x 200 mL', unixepoch()),
  ('amul-high-protein-milk-250-ml-or-pack-of-32', 'High Protein Milk, 32 x 250 mL', unixepoch()),
  ('amul-high-protein-milk-250-ml-or-pack-of-8', 'High Protein Milk, 8 x 250 mL', unixepoch()),
  ('amul-kool-protein-milkshake-or-chocolate-180-ml-or-pack-of-30', 'Kool Protein Milkshake Chocolate, 30 x 180 mL', unixepoch()),
  ('amul-kool-protein-milkshake-or-kesar-180-ml-or-pack-of-30', 'Kool Protein Milkshake Kesar, 30 x 180 mL', unixepoch()),
  ('amul-kool-protein-milkshake-or-arabica-coffee-180-ml-or-pack-of-30', 'Kool Protein Milkshake Coffee, 30 x 180 mL', unixepoch()),
  ('amul-high-protein-paneer-400-g-or-pack-of-2', 'High Protein Paneer, 2 x 400 g', unixepoch()),
  ('amul-whey-protein-32-g-or-pack-of-30-sachets', 'Whey Protein, 30 sachets', unixepoch()),
  ('amul-chocolate-whey-protein-34-g-or-pack-of-30-sachets', 'Chocolate Whey Protein, 30 sachets', unixepoch());

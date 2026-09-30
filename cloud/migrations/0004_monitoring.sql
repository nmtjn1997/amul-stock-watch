-- Monitoring: one row per poll run, and an event log of what people and the system did.
-- Both are pruned by the cron (runs after 7 days, events after 30).

CREATE TABLE runs (
  id              INTEGER PRIMARY KEY,
  ts              INTEGER NOT NULL,
  units_total     INTEGER NOT NULL,          -- pincode chunks waiting to be checked
  pincodes        INTEGER NOT NULL,          -- chunks checked this run
  checks          INTEGER NOT NULL,          -- product reads
  amul_requests   INTEGER NOT NULL,          -- every request sent to shop.amul.com
  notify_requests INTEGER NOT NULL,          -- push / ntfy / webhook sends
  alerts          INTEGER NOT NULL,
  errors          TEXT NOT NULL,             -- JSON list, at most 10
  ms              INTEGER NOT NULL
);
CREATE INDEX runs_ts ON runs(ts);

CREATE TABLE events (
  id      INTEGER PRIMARY KEY,
  ts      INTEGER NOT NULL,
  level   TEXT NOT NULL CHECK (level IN ('info', 'warn', 'error')),
  kind    TEXT NOT NULL,                     -- signup, login, login_fail, watch_add, alert_sent, admin, error, ...
  user_id INTEGER,                           -- who did it (no FK: the log outlives deleted accounts)
  actor   TEXT,                              -- username at the time, or "system"
  detail  TEXT NOT NULL,
  net     TEXT                               -- short one-way hash of the network, to spot one source doing a lot
);
CREATE INDEX events_ts ON events(ts);
CREATE INDEX events_kind ON events(kind, ts);

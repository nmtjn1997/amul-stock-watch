-- Questions, suggestions and bug reports sent from the Help page. The admin reads them in
-- Admin, Inbox and replies by email.
CREATE TABLE feedback (
  id         INTEGER PRIMARY KEY,
  user_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
  username   TEXT NOT NULL,
  email      TEXT NOT NULL,
  kind       TEXT NOT NULL,
  message    TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  status     TEXT NOT NULL DEFAULT 'new'
);
CREATE INDEX feedback_created ON feedback (created_at DESC);

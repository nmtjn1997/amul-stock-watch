-- Every time a product flips between in and out of stock at a pincode, with the quantity
-- it came back with. Filled by triggers on `stock`, so the poller does no extra work.
CREATE TABLE stock_events (
  id       INTEGER PRIMARY KEY,
  pincode  TEXT NOT NULL,
  alias    TEXT NOT NULL,
  ts       INTEGER NOT NULL,
  in_stock INTEGER NOT NULL,
  qty      INTEGER
);
CREATE INDEX stock_events_pair ON stock_events (pincode, alias, ts DESC);

CREATE TRIGGER stock_events_first AFTER INSERT ON stock
BEGIN
  INSERT INTO stock_events (pincode, alias, ts, in_stock, qty) VALUES (new.pincode, new.alias, new.changed_at, new.in_stock, new.qty);
END;

CREATE TRIGGER stock_events_flip AFTER UPDATE OF in_stock ON stock WHEN old.in_stock != new.in_stock
BEGIN
  INSERT INTO stock_events (pincode, alias, ts, in_stock, qty) VALUES (new.pincode, new.alias, new.changed_at, new.in_stock, new.qty);
END;

-- Backfill what is already known: sell-out times (an out-of-stock row is only written when
-- it sells out) and restock times from the alerts that went out (quantity unknown).
INSERT INTO stock_events (pincode, alias, ts, in_stock, qty)
  SELECT pincode, alias, changed_at, 0, 0 FROM stock WHERE in_stock = 0;
INSERT INTO stock_events (pincode, alias, ts, in_stock, qty)
  WITH RECURSIVE split(ts, label, rest, pin) AS (
    SELECT ts, product, pincodes || ',', '' FROM alert_log WHERE kind = 'stock'
    UNION ALL
    SELECT ts, label, substr(rest, instr(rest, ',') + 1), substr(rest, 1, instr(rest, ',') - 1) FROM split WHERE rest != ''
  )
  SELECT s.pin, p.alias, MIN(s.ts), 1, NULL FROM split s JOIN products p ON p.label = s.label
  WHERE s.pin != '' GROUP BY s.pin, p.alias, s.ts / 600;

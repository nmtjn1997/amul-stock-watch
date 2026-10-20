-- Rebuild the alert-based restocks (0010 dropped real ones where a sell-out between two
-- restocks was never recorded), then merge only restocks of the same pair that are less
-- than 6 hours apart with no sell-out between: those are one restock alerted to several
-- people at different minutes.
DELETE FROM stock_events WHERE in_stock = 1 AND qty IS NULL;
INSERT INTO stock_events (pincode, alias, ts, in_stock, qty)
  WITH RECURSIVE split(ts, label, rest, pin) AS (
    SELECT ts, product, pincodes || ',', '' FROM alert_log WHERE kind = 'stock'
    UNION ALL
    SELECT ts, label, substr(rest, instr(rest, ',') + 1), substr(rest, 1, instr(rest, ',') - 1) FROM split WHERE rest != ''
  )
  SELECT s.pin, p.alias, MIN(s.ts), 1, NULL FROM split s JOIN products p ON p.label = s.label
  WHERE s.pin != '' GROUP BY s.pin, p.alias, s.ts / 600;
DELETE FROM stock_events
WHERE id IN (
  SELECT id FROM (
    SELECT id, in_stock, qty, ts,
           LAG(in_stock) OVER (PARTITION BY pincode, alias ORDER BY ts, id) AS prev,
           LAG(ts) OVER (PARTITION BY pincode, alias ORDER BY ts, id) AS prev_ts
    FROM stock_events
  ) WHERE in_stock = 1 AND qty IS NULL AND prev = 1 AND ts - prev_ts < 6 * 3600
);

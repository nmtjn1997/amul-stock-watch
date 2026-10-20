-- The backfill took restock times from alerts, and one restock alerts several people at
-- different minutes. Keep a restock only when the event before it (same pincode and
-- product) was a sell-out, or when it is the first event of that pair.
DELETE FROM stock_events
WHERE in_stock = 1 AND qty IS NULL AND id IN (
  SELECT id FROM (
    SELECT id, in_stock,
           LAG(in_stock) OVER (PARTITION BY pincode, alias ORDER BY ts, id) AS prev
    FROM stock_events
  ) WHERE in_stock = 1 AND prev = 1
);

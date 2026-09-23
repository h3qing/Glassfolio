-- Look-through exposure at date $as_of (spec §4.1).
--   b(e,s,t) = S(e,s,t) / N(e,t)                share basket per ETF share
--   x(s,t)   = direct(s,t) + Σ_e q(e,t) b(e,s,t)
-- Weight fallback when S or N is missing: b = weight × p(e) / p(s) (approximate).
-- Cash and `other` securities (futures, foreign cash, ...) are priced at 1, i.e.
-- carried as dollars: USD cash from its basket amount, `other` from weight × p(e).
-- Self-holdings are dropped (they stay in the fund residual).
-- Funds without holdings but with proxy_security_id map by value to the proxy
-- (approximate). Every expanded fund emits a `fund_residual` line
-- (q × p(fund) − Σ children values) so lines always sum to portfolio value.
-- Top-level holdings are also emitted with kind = 'position' (not part of
-- the look-through; used to check conservation).
WITH RECURSIVE
sec AS (
    SELECT * EXCLUDE (rn) FROM (
        SELECT *, row_number() OVER (PARTITION BY security_id ORDER BY created_at DESC) AS rn
        FROM securities) WHERE rn = 1
),
splits AS (
    SELECT DISTINCT security_id, date, ratio_or_amount::DOUBLE AS ratio
    FROM corporate_actions WHERE type = 'split' AND date <= $as_of
),
latest_import AS (
    SELECT account_id, file_hash FROM (
        SELECT account_id, file_hash, row_number() OVER (
            PARTITION BY account_id ORDER BY as_of_date DESC, imported_at DESC) AS rn
        FROM import_files
        WHERE kind = 'statement' AND status = 'imported' AND as_of_date <= $as_of)
    WHERE rn = 1
),
pos AS (
    SELECT p.account_id, p.security_id,
        sum(p.shares::DOUBLE * coalesce((SELECT product(s.ratio) FROM splits s
            WHERE s.security_id = p.security_id AND s.date > p.as_of_date), 1)) AS shares
    FROM positions p
    JOIN latest_import li ON li.account_id = p.account_id AND li.file_hash = p.import_file_hash
    GROUP BY ALL
),
px_ranked AS (
    SELECT security_id, date, close::DOUBLE AS close, row_number() OVER (
        PARTITION BY security_id ORDER BY date DESC,
            CASE source WHEN 'broker_export' THEN 3 WHEN 'etf_file' THEN 2 ELSE 1 END) AS rn
    FROM prices WHERE date <= $as_of AND close IS NOT NULL
),
px AS (
    SELECT r.security_id, r.close / coalesce((SELECT product(s.ratio) FROM splits s
            WHERE s.security_id = r.security_id AND s.date > r.date), 1) AS price
    FROM px_ranked r WHERE r.rn = 1 AND r.security_id NOT IN (
        SELECT security_id FROM sec WHERE type IN ('cash', 'other'))
    UNION ALL
    SELECT security_id, 1.0 FROM sec WHERE type IN ('cash', 'other')
),
holdings_version AS (
    SELECT etf_id, as_of_date, fetched_at FROM (
        SELECT DISTINCT etf_id, as_of_date, fetched_at FROM etf_holdings
        WHERE as_of_date <= $as_of)
    QUALIFY row_number() OVER (PARTITION BY etf_id ORDER BY as_of_date DESC, fetched_at DESC) = 1
),
basket AS (
    SELECT h.etf_id, h.holding_id, sum(h.weight) AS weight,
        sum(CASE WHEN h.shares IS NOT NULL AND h.etf_shares_outstanding IS NOT NULL THEN
            h.shares::DOUBLE
            * coalesce((SELECT product(s.ratio) FROM splits s
                WHERE s.security_id = h.holding_id AND s.date > v.as_of_date), 1)
            / (h.etf_shares_outstanding::DOUBLE
               * coalesce((SELECT product(s.ratio) FROM splits s
                   WHERE s.security_id = h.etf_id AND s.date > v.as_of_date), 1))
        END) AS b_shares
    FROM etf_holdings h
    JOIN holdings_version v ON v.etf_id = h.etf_id AND v.as_of_date = h.as_of_date
        AND v.fetched_at = h.fetched_at
    WHERE h.holding_id <> h.etf_id
    GROUP BY h.etf_id, h.holding_id
),
edges AS (
    SELECT b.etf_id AS parent_id, b.holding_id AS child_id,
        coalesce(CASE WHEN c.type = 'other' THEN b.weight * pp.price END,
                 b.b_shares, b.weight * pp.price / pc.price) AS b,
        b.b_shares IS NULL AND c.type <> 'other' AS approx
    FROM basket b
    JOIN sec c ON c.security_id = b.holding_id
    LEFT JOIN px pp ON pp.security_id = b.etf_id
    LEFT JOIN px pc ON pc.security_id = b.holding_id
    UNION ALL
    SELECT s.security_id, s.proxy_security_id, pp.price / pc.price, TRUE
    FROM sec s
    LEFT JOIN px pp ON pp.security_id = s.security_id
    LEFT JOIN px pc ON pc.security_id = s.proxy_security_id
    WHERE s.proxy_security_id IS NOT NULL
      AND s.security_id NOT IN (SELECT etf_id FROM basket)
),
tree(account_id, security_id, shares, depth, via_id, approx, path, parent_path) AS (
    SELECT account_id, security_id, shares, 0, NULL::VARCHAR, FALSE,
        account_id || '/' || security_id, NULL::VARCHAR
    FROM pos
    UNION ALL
    SELECT t.account_id, e.child_id, t.shares * e.b, t.depth + 1,
        coalesce(t.via_id, t.security_id), t.approx OR e.approx,
        t.path || '/' || e.child_id, t.path
    FROM tree t JOIN edges e ON e.parent_id = t.security_id
    WHERE t.depth < 5
),
nodes AS (
    SELECT t.*, p.price, t.shares * p.price AS value,
        EXISTS (SELECT 1 FROM edges e WHERE e.parent_id = t.security_id) AS has_children
    FROM tree t LEFT JOIN px p ON p.security_id = t.security_id
),
child_values AS (
    SELECT parent_path, sum(value) AS value, bool_or(value IS NULL) AS any_missing
    FROM nodes WHERE parent_path IS NOT NULL GROUP BY parent_path
)
SELECT n.account_id, n.security_id, n.via_id AS via_security_id, n.depth,
    CASE WHEN s.type = 'cash' THEN 'cash'
         WHEN s.type = 'other' THEN 'other'
         WHEN n.has_children THEN 'truncated'
         ELSE 'security' END AS kind,
    n.shares, n.price, n.value, n.approx
FROM nodes n JOIN sec s ON s.security_id = n.security_id
WHERE NOT (n.has_children AND n.depth < 5)
UNION ALL
SELECT n.account_id, n.security_id, coalesce(n.via_id, n.security_id), n.depth,
    'fund_residual', NULL, NULL,
    CASE WHEN c.any_missing THEN NULL ELSE n.value - c.value END, n.approx
FROM nodes n JOIN child_values c ON c.parent_path = n.path
WHERE n.has_children AND n.depth < 5
UNION ALL
SELECT account_id, security_id, NULL, 0, 'position', shares, price, value, FALSE
FROM nodes WHERE depth = 0

-- Unexplained change between two statements of one account (spec §4.3):
--   end value − start holdings at end prices − dividends paid on start holdings
-- Start shares are split-adjusted to the end date. End prices come from the new
-- statement; a security sold in between needs a close dated after the start
-- statement (otherwise it counts as unpriced). Cash is 1.0. Dividends count once
-- per security and date, whichever source reported them.
WITH splits AS (
    SELECT DISTINCT security_id, date, ratio_or_amount::DOUBLE AS ratio
    FROM corporate_actions WHERE type = 'split'
),
sec AS (SELECT * FROM securities QUALIFY row_number() OVER (
    PARTITION BY security_id ORDER BY created_at DESC) = 1),
start_pos AS (
    SELECT security_id, sum(shares::DOUBLE) AS shares FROM positions
    WHERE account_id = $account_id AND import_file_hash = $start_hash GROUP BY ALL
),
end_pos AS (
    SELECT security_id, sum(shares::DOUBLE) AS shares, any_value(export_price::DOUBLE) AS price
    FROM positions WHERE account_id = $account_id AND import_file_hash = $end_hash GROUP BY ALL
),
start_valued AS (
    SELECT s.security_id,
        s.shares * coalesce((SELECT product(x.ratio) FROM splits x WHERE x.security_id = s.security_id
            AND x.date > $start_date AND x.date <= $end_date), 1) AS shares,
        CASE WHEN c.type = 'cash' THEN 1.0 ELSE coalesce(e.price, (
            SELECT p.close::DOUBLE FROM prices p WHERE p.security_id = s.security_id
              AND p.date > $start_date AND p.date <= $end_date AND p.close IS NOT NULL
            ORDER BY p.date DESC LIMIT 1)) END AS end_price,
        coalesce((SELECT sum(d) FROM (SELECT max(p.dividend_per_share::DOUBLE) AS d FROM prices p
            WHERE p.security_id = s.security_id AND p.date > $start_date
              AND p.date <= $end_date GROUP BY p.date)), 0) * s.shares AS dividends
    FROM start_pos s JOIN sec c USING (security_id)
    LEFT JOIN end_pos e USING (security_id)
)
SELECT
    (SELECT sum(shares * price) FROM end_pos) AS end_value,
    (SELECT sum(shares * end_price) FROM start_valued) AS start_at_end_prices,
    (SELECT sum(dividends) FROM start_valued) AS dividends,
    (SELECT count(*) FROM start_valued WHERE end_price IS NULL) AS unpriced

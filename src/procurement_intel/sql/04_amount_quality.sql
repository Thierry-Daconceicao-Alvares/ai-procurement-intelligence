-- Amount quality. Reported amounts contain obvious errors: amounts entered in
-- thousands of euros, typing errors, placeholders. Rule-based checks, in order:
--
-- 1. placeholder: 10 million euros or more, and at most 10 euros below a round
--    amount (one significant digit): 999,999,999, 99,999,999.99, or
--    399,999,996 (four lots of 99,999,999 added up);
-- 2. out_of_bounds: below 100 euros, or 10 billion euros and more;
-- 3. default_ceiling: one buyer reports the same amount of 10 million euros or
--    more for at least 5 contracts, in at least 3 CPV divisions and on at least
--    3 dates, and this amount is at least 100 times the 90th percentile of the
--    buyer's other contracts. An amount reused for unrelated purchases is a
--    default value, not an estimate of what is bought. Large buyers and central
--    purchasing bodies also reuse standard ceilings (20 or 100 million euros
--    for instance), but these stay within the range of their large contracts.
--
-- The model of expected amounts (amount_model.py) then scores the amounts
-- that pass these checks; 05_marts.sql gives the final status. The segment
-- z-score below is a simpler benchmark, kept to evaluate the model: each
-- amount is compared with comparable contracts (same CPV division, type,
-- procedure group, framework agreement or not) on a log scale, with the
-- median and the median absolute deviation (MAD), which a few extreme values
-- cannot move:  robust z = (log10(amount) - median) / (1.4826 * MAD).
-- Duplicate declarations are left out of all statistics.

CREATE OR REPLACE TABLE int_amount_quality AS
WITH canonical AS (
    SELECT c.*
    FROM int_contracts AS c
    LEFT JOIN int_duplicates AS d USING (uid)
    WHERE coalesce(d.is_canonical, true)
),
amounts AS (
    SELECT
        uid,
        buyer_siret,
        initial_amount,
        notification_date,
        log10(initial_amount) AS log_amount,
        left(cpv_code, 2) AS declared_division,
        coalesce(left(cpv_code, 2), '??') AS cpv_division,
        contract_type,
        coalesce(is_framework, false) AS is_framework,
        procedure_group,
        initial_amount < 100 OR initial_amount >= 1e10 AS out_of_bounds,
        -- Distance to the next round amount with one significant digit.
        ceil(initial_amount / power(10, floor(log10(initial_amount + 10))))
            * power(10, floor(log10(initial_amount + 10))) - initial_amount AS below_round
    FROM canonical
    WHERE initial_amount IS NOT NULL
),
checked AS (
    SELECT
        *,
        initial_amount >= 1e7 AND below_round > 0.001 AND below_round <= 10 AS placeholder
    FROM amounts
),
-- Amounts that one buyer reuses for unrelated purchases.
repeated AS (
    SELECT
        buyer_siret,
        initial_amount,
        count(*) AS same_amount_contracts,
        count(DISTINCT declared_division) AS same_amount_divisions
    FROM checked
    WHERE initial_amount >= 1e7 AND NOT placeholder AND NOT out_of_bounds
    GROUP BY buyer_siret, initial_amount
    HAVING count(*) >= 5
        AND count(DISTINCT declared_division) >= 3
        AND count(DISTINCT notification_date) >= 3
),
-- The buyer's large contracts (90th percentile), without the reused amounts.
buyer_typical AS (
    SELECT
        k.buyer_siret,
        quantile_cont(k.initial_amount, 0.9) AS large_amount,
        count(*) AS n
    FROM checked AS k
    LEFT JOIN repeated AS r USING (buyer_siret, initial_amount)
    WHERE r.buyer_siret IS NULL AND NOT k.placeholder AND NOT k.out_of_bounds
    GROUP BY k.buyer_siret
),
flagged AS (
    SELECT
        k.*,
        r.same_amount_contracts,
        r.same_amount_divisions,
        t.large_amount AS buyer_p90_amount,
        CASE
            WHEN k.placeholder THEN 'placeholder'
            WHEN k.out_of_bounds THEN 'out_of_bounds'
            WHEN r.buyer_siret IS NOT NULL AND t.n >= 5
                AND k.initial_amount >= 100 * t.large_amount THEN 'default_ceiling'
        END AS amount_issue
    FROM checked AS k
    LEFT JOIN repeated AS r USING (buyer_siret, initial_amount)
    LEFT JOIN buyer_typical AS t USING (buyer_siret)
),
detailed AS (
    SELECT
        cpv_division, contract_type, is_framework, procedure_group,
        count(*) AS n, median(log_amount) AS median_log, mad(log_amount) AS mad_log
    FROM flagged
    WHERE amount_issue IS NULL
    GROUP BY ALL
),
broad AS (
    SELECT
        contract_type, is_framework,
        count(*) AS n, median(log_amount) AS median_log, mad(log_amount) AS mad_log
    FROM flagged
    WHERE amount_issue IS NULL
    GROUP BY ALL
),
scored AS (
    SELECT
        f.uid,
        f.amount_issue,
        f.same_amount_contracts,
        f.same_amount_divisions,
        f.buyer_p90_amount,
        -- Segments with fewer than 50 contracts use the broader segment.
        CASE WHEN d.n >= 50 THEN d.median_log ELSE b.median_log END AS median_log,
        CASE WHEN d.n >= 50 THEN d.mad_log ELSE b.mad_log END AS mad_log,
        CASE WHEN d.n >= 50 THEN 'detailed' ELSE 'broad' END AS segment_level,
        f.log_amount
    FROM flagged AS f
    LEFT JOIN detailed AS d USING (cpv_division, contract_type, is_framework, procedure_group)
    LEFT JOIN broad AS b USING (contract_type, is_framework)
)
SELECT
    c.uid,
    c.initial_amount,
    s.amount_issue,
    s.same_amount_contracts,
    s.same_amount_divisions,
    s.buyer_p90_amount,
    power(10, s.median_log) AS segment_median_amount,
    s.segment_level,
    round((s.log_amount - s.median_log) / nullif(1.4826 * s.mad_log, 0), 2) AS amount_z
FROM canonical AS c
LEFT JOIN scored AS s USING (uid);

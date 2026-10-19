-- Amount quality. Reported amounts contain obvious errors: amounts entered in
-- thousands of euros, typing errors, placeholders such as 1 euro. Each amount
-- is compared with comparable contracts (same CPV division, type, procedure
-- group, framework agreement or not) on a log scale, with robust statistics:
-- the median and the median absolute deviation (MAD), which a few extreme
-- values cannot move.
--
-- robust z = (log10(amount) - median) / (1.4826 * MAD)
-- A z of 5 means about 3 orders of magnitude away from the usual amount for
-- typical segments: the amount is treated as implausible and excluded from
-- totals. Duplicate declarations are left out of the statistics.

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
        initial_amount,
        log10(initial_amount) AS log_amount,
        coalesce(left(cpv_code, 2), '??') AS cpv_division,
        contract_type,
        coalesce(is_framework, false) AS is_framework,
        procedure_group
    FROM canonical
    WHERE initial_amount IS NOT NULL
),
detailed AS (
    SELECT
        cpv_division, contract_type, is_framework, procedure_group,
        count(*) AS n, median(log_amount) AS median_log, mad(log_amount) AS mad_log
    FROM amounts
    GROUP BY ALL
),
broad AS (
    SELECT
        contract_type, is_framework,
        count(*) AS n, median(log_amount) AS median_log, mad(log_amount) AS mad_log
    FROM amounts
    GROUP BY ALL
),
scored AS (
    SELECT
        a.uid,
        a.initial_amount,
        -- Segments with fewer than 50 contracts use the broader segment.
        CASE WHEN d.n >= 50 THEN d.median_log ELSE b.median_log END AS median_log,
        CASE WHEN d.n >= 50 THEN d.mad_log ELSE b.mad_log END AS mad_log,
        CASE WHEN d.n >= 50 THEN 'detailed' ELSE 'broad' END AS segment_level,
        a.log_amount
    FROM amounts AS a
    LEFT JOIN detailed AS d USING (cpv_division, contract_type, is_framework, procedure_group)
    LEFT JOIN broad AS b USING (contract_type, is_framework)
)
SELECT
    c.uid,
    c.initial_amount,
    power(10, s.median_log) AS segment_median_amount,
    s.segment_level,
    round((s.log_amount - s.median_log) / nullif(1.4826 * s.mad_log, 0), 2) AS amount_z,
    CASE
        WHEN c.initial_amount IS NULL THEN 'missing'
        WHEN c.initial_amount < 100 OR c.initial_amount > 1e10 THEN 'implausible'
        WHEN abs((s.log_amount - s.median_log) / nullif(1.4826 * s.mad_log, 0)) >= 5
            THEN 'implausible'
        ELSE 'valid'
    END AS amount_status
FROM canonical AS c
LEFT JOIN scored AS s USING (uid);

-- Records that describe the same award. Three kinds of links:
-- - duplicate: the same contract published several times (by several
--   platforms, by the same one, or under two establishments of one buyer);
-- - multi-supplier framework: one framework agreement awarded to several
--   suppliers was published once per supplier, each record with the full
--   maximum amount;
-- - joint purchase: the members of a purchasing group (groupement de
--   commandes) each published the contract with the full amount.
--
-- Records of the same buyer are linked when they share the amount and the
-- notification date, carry the same lot number (if any) and have nearly the
-- same description:
-- - containment: share of the words of the shorter description found in the
--   other one (descriptions are often truncated by one of the platforms);
-- - Jaccard index: shared words / all words of the two descriptions.
-- They are duplicates when they name the same lead holder, and one
-- multi-supplier framework when both are framework agreements. Lots of one
-- procedure awarded on the same day are not linked: their descriptions differ
-- by the lot number or the lot title.
--
-- Records of different buyers are linked when they share the amount (at
-- least 100,000 euros), the notification date, the CPV code, the lead holder
-- and the lot number (if any): independent contracts almost never coincide on
-- all five. Descriptions are not compared, because each member words its
-- record its own way. Two establishments of the same legal unit (same SIREN)
-- make a duplicate, not a joint purchase.
--
-- Members of a purchasing group also notify their own contract under the
-- group's framework agreement, on their own date and with their own CPV code,
-- but with the maximum amount of the whole group. Records of at least 10
-- million euros with the same amount and lead holder are grouped in time (a
-- new cluster starts after 60 days without such a record); a cluster from at
-- least three legal units is one joint purchase.

CREATE OR REPLACE TABLE int_duplicate_pairs AS
WITH contracts AS (
    SELECT
        uid,
        buyer_siret,
        lead_holder_key,
        cpv_code,
        coalesce(is_framework, false) AS is_framework,
        initial_amount,
        notification_date,
        list_distinct(
            list_filter(
                string_split(
                    regexp_replace(
                        strip_accents(lower(regexp_replace(coalesce(description, ''), '&#?\w+;', ' ', 'g'))),
                        '[^a-z0-9]+', ' ', 'g'
                    ),
                    ' '
                ),
                word -> length(word) > 1
            )
        ) AS words,
        nullif(
            regexp_extract(strip_accents(lower(coalesce(description, ''))), 'lot\s*(n\s*)?[°o]?\s*(\d+)', 2),
            ''
        ) AS lot_number
    FROM int_contracts
    WHERE initial_amount IS NOT NULL
        AND lead_holder_key IS NOT NULL
        AND buyer_siret IS NOT NULL
),
same_buyer AS (
    SELECT
        a.uid AS uid_a,
        b.uid AS uid_b,
        a.lead_holder_key = b.lead_holder_key AS same_holder,
        a.is_framework AND b.is_framework AS both_frameworks,
        len(list_intersect(a.words, b.words)) AS shared_words,
        least(len(a.words), len(b.words)) AS shorter_words,
        len(list_distinct(list_concat(a.words, b.words))) AS all_words
    FROM contracts AS a
    JOIN contracts AS b
        ON a.buyer_siret = b.buyer_siret
        AND a.initial_amount = b.initial_amount
        AND a.notification_date = b.notification_date
        AND a.uid < b.uid
    WHERE coalesce(a.lot_number, '') = coalesce(b.lot_number, '')
),
other_buyer AS (
    SELECT
        a.uid AS uid_a,
        b.uid AS uid_b,
        left(a.buyer_siret, 9) = left(b.buyer_siret, 9) AS same_legal_unit,
        len(list_intersect(a.words, b.words)) AS shared_words,
        least(len(a.words), len(b.words)) AS shorter_words,
        len(list_distinct(list_concat(a.words, b.words))) AS all_words
    FROM contracts AS a
    JOIN contracts AS b
        ON a.initial_amount = b.initial_amount
        AND a.notification_date = b.notification_date
        AND a.cpv_code = b.cpv_code
        AND a.lead_holder_key = b.lead_holder_key
        AND a.buyer_siret <> b.buyer_siret
        AND a.uid < b.uid
    WHERE a.initial_amount >= 1e5
        AND coalesce(a.lot_number, '') = coalesce(b.lot_number, '')
),
large AS (
    SELECT
        uid,
        initial_amount,
        lead_holder_key,
        left(buyer_siret, 9) AS legal_unit,
        notification_date,
        coalesce(
            date_diff(
                'day',
                lag(notification_date) OVER (
                    PARTITION BY initial_amount, lead_holder_key ORDER BY notification_date, uid
                ),
                notification_date
            ) > 60,
            true
        ) AS starts_cluster
    FROM contracts
    WHERE initial_amount >= 1e7
),
clustered AS (
    SELECT
        *,
        sum(starts_cluster::INT) OVER (
            PARTITION BY initial_amount, lead_holder_key
            ORDER BY notification_date, uid
            ROWS UNBOUNDED PRECEDING
        ) AS cluster
    FROM large
),
member_groups AS (
    SELECT initial_amount, lead_holder_key, cluster, min(uid) AS first_uid
    FROM clustered
    GROUP BY ALL
    HAVING count(DISTINCT legal_unit) >= 3
)
SELECT
    uid_a,
    uid_b,
    CASE WHEN same_holder THEN 'duplicate' ELSE 'multi_supplier_framework' END AS link,
    round(shared_words / greatest(shorter_words, 1), 3) AS containment,
    round(shared_words / greatest(all_words, 1), 3) AS jaccard
FROM same_buyer
WHERE (same_holder OR both_frameworks)
    AND shorter_words > 0
    AND shared_words / shorter_words >= 0.9
    AND shared_words / all_words >= 0.6

UNION ALL

SELECT
    uid_a,
    uid_b,
    CASE WHEN same_legal_unit THEN 'duplicate' ELSE 'joint_purchase' END AS link,
    round(shared_words / greatest(shorter_words, 1), 3) AS containment,
    round(shared_words / greatest(all_words, 1), 3) AS jaccard
FROM other_buyer

UNION ALL

SELECT
    g.first_uid AS uid_a,
    c.uid AS uid_b,
    'joint_purchase' AS link,
    NULL AS containment,
    NULL AS jaccard
FROM clustered AS c
JOIN member_groups AS g USING (initial_amount, lead_holder_key, cluster)
WHERE c.uid <> g.first_uid;

-- Records that describe the same award. Two records are linked when they
-- share the buyer, the amount and the notification date, carry the same lot
-- number (if any) and have nearly the same description:
-- - containment: share of the words of the shorter description found in the
--   other one (descriptions are often truncated by one of the platforms);
-- - Jaccard index: shared words / all words of the two descriptions.
-- Two kinds of links:
-- - duplicate: same lead holder. The same contract was published several
--   times, by several platforms or by the same one;
-- - multi-supplier framework: different holders, both records are framework
--   agreements. One framework agreement awarded to several suppliers was
--   published once per supplier, each record with the full maximum amount.
-- Lots of one procedure awarded on the same day are not linked: their
-- descriptions differ by the lot number or the lot title.

CREATE OR REPLACE TABLE int_duplicate_pairs AS
WITH contracts AS (
    SELECT
        uid,
        buyer_siret,
        lead_holder_key,
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
pairs AS (
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
)
SELECT
    uid_a,
    uid_b,
    CASE WHEN same_holder THEN 'duplicate' ELSE 'multi_supplier_framework' END AS link,
    round(shared_words / greatest(shorter_words, 1), 3) AS containment,
    round(shared_words / greatest(all_words, 1), 3) AS jaccard
FROM pairs
WHERE (same_holder OR both_frameworks)
    AND shorter_words > 0
    AND shared_words / shorter_words >= 0.9
    AND shared_words / all_words >= 0.6;

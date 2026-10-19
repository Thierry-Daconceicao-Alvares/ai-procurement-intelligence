-- Alerts: rule-based detectors over the marts. Each row is a risk indicator to
-- review, never a finding of wrongdoing: most have a legitimate explanation
-- (urgency, exclusive rights, a reporting error...). The evidence column holds
-- every figure needed to explain the alert; titles, actions and priorities are
-- added in Python (alerts.py).

-- Thresholds in force at the notification date of each contract. The law
-- applies them from the launch of the consultation, which the data does not
-- give: the notification date is an approximation.
CREATE OR REPLACE TABLE int_contract_thresholds AS
WITH thresholds AS (
    SELECT * FROM read_csv('{reference_dir}/procurement_thresholds.csv', header = true)
),
contracts AS (
    SELECT
        uid,
        notification_date,
        CASE WHEN contract_type = 'Works' THEN 'works' ELSE 'supplies_services' END
            AS contract_scope,
        CASE WHEN buyer_category = 'Central government' THEN 'central_government'
            ELSE 'other_authorities' END AS buyer_scope
    FROM fct_contracts
)
SELECT
    c.uid,
    max(t.amount_eur) FILTER (WHERE t.threshold = 'eu_formal') AS eu_threshold,
    max(t.amount_eur) FILTER (WHERE t.threshold = 'no_competition') AS no_competition_threshold
FROM contracts AS c
LEFT JOIN thresholds AS t
    ON t.contract_scope = c.contract_scope
    AND t.buyer_scope IN ('all', c.buyer_scope)
    AND c.notification_date BETWEEN t.valid_from AND t.valid_to
GROUP BY c.uid;

CREATE OR REPLACE TABLE int_alerts AS

-- 1. Amount increased by modifications beyond the de minimis share that the
-- Code allows without justification (10% for supplies and services, 15% for
-- works, art. R. 2194-8); 50% is the cap of most other grounds (R. 2194-3, -5).
SELECT
    'amount_increase' AS alert_type,
    CASE WHEN amount_change_ratio >= 0.5 THEN 'high'
        WHEN amount_change_ratio >= 0.25 THEN 'medium' ELSE 'low' END AS severity,
    uid,
    buyer_siret,
    lead_holder_key AS holder_key,
    cpv_division,
    coalesce(last_modification_date, notification_date) AS reference_date,
    reported_current_amount - awarded_amount AS amount_at_stake,
    to_json({
        'initial_amount': awarded_amount,
        'current_amount': reported_current_amount,
        'increase_ratio': amount_change_ratio,
        'modification_count': modification_count,
        'notification_date': notification_date,
        'last_modification_date': last_modification_date,
        'contract_type': contract_type,
        'de_minimis_ratio': CASE WHEN contract_type = 'Works' THEN 0.15 ELSE 0.10 END
    }) AS evidence
FROM fct_contracts
WHERE amount_change_status = 'increased'
    AND amount_change_ratio >= CASE WHEN contract_type = 'Works' THEN 0.15 ELSE 0.10 END

UNION ALL BY NAME

-- 2. Amounts far from what comparable contracts cost (model residual).
SELECT
    CASE WHEN amount_status = 'implausible' THEN 'implausible_amount'
        ELSE 'atypical_amount' END AS alert_type,
    CASE WHEN amount_status = 'implausible' THEN 'high' ELSE 'medium' END AS severity,
    uid,
    buyer_siret,
    lead_holder_key AS holder_key,
    cpv_division,
    notification_date AS reference_date,
    abs(reported_amount - expected_amount) AS amount_at_stake,
    to_json({
        'reported_amount': reported_amount,
        'expected_amount': round(expected_amount),
        'ratio_to_expected': round(reported_amount / expected_amount, 3),
        'robust_z': amount_z_model,
        'excluded_from_totals': amount_status = 'implausible',
        'contract_type': contract_type,
        'is_framework': is_framework,
        'procedure_type': procedure_type,
        'notification_date': notification_date
    }) AS evidence
FROM fct_contracts
WHERE amount_status = 'implausible'
    OR (amount_status = 'valid' AND abs(amount_z_model) >= 3.5)

UNION ALL BY NAME

-- 3. Formalised competitive procedure that received a single offer.
SELECT
    'single_bid' AS alert_type,
    CASE WHEN f.awarded_amount >= 1e6 THEN 'high' ELSE 'medium' END AS severity,
    f.uid,
    f.buyer_siret,
    f.lead_holder_key AS holder_key,
    f.cpv_division,
    f.notification_date AS reference_date,
    f.awarded_amount AS amount_at_stake,
    to_json({
        'awarded_amount': f.awarded_amount,
        'offers_received': f.offers_received,
        'procedure_type': f.procedure_type,
        'eu_threshold': t.eu_threshold,
        'notification_date': f.notification_date
    }) AS evidence
FROM fct_contracts AS f
JOIN int_contract_thresholds AS t USING (uid)
WHERE f.offers_received = 1
    AND f.procedure_type IN (
        'Open tender', 'Restricted tender', 'Competitive procedure with negotiation',
        'Competitive dialogue'
    )
    AND f.awarded_amount >= t.eu_threshold

UNION ALL BY NAME

-- 4. Buyers whose single-offer rate is well above the rate expected from the
-- categories they buy (binomial z-score of at least 3, and 15 points above).
SELECT
    'buyer_single_bid_rate' AS alert_type,
    CASE WHEN z >= 6 THEN 'high' ELSE 'medium' END AS severity,
    NULL AS uid,
    buyer_siret,
    NULL AS holder_key,
    NULL AS cpv_division,
    last_date AS reference_date,
    single_bid_amount AS amount_at_stake,
    to_json({
        'procedures': n,
        'single_bids': singles,
        'single_bid_rate': round(rate, 3),
        'expected_rate': round(expected, 3),
        'z_score': round(z, 1),
        'first_date': first_date,
        'last_date': last_date
    }) AS evidence
FROM (
    WITH base AS (
        SELECT
            buyer_siret,
            cpv_division,
            notification_date,
            (offers_received = 1)::INT AS single,
            CASE WHEN offers_received = 1 THEN awarded_amount END AS single_amount
        FROM fct_contracts
        WHERE procedure_group = 'Competitive'
            AND offers_received IS NOT NULL
            AND cpv_division IS NOT NULL
    ),
    division_rate AS (
        SELECT cpv_division, avg(single) AS rate FROM base GROUP BY cpv_division
    )
    SELECT
        b.buyer_siret,
        count(*) AS n,
        sum(b.single) AS singles,
        avg(b.single) AS rate,
        avg(d.rate) AS expected,
        (avg(b.single) - avg(d.rate)) / sqrt(avg(d.rate) * (1 - avg(d.rate)) / count(*)) AS z,
        coalesce(sum(b.single_amount), 0) AS single_bid_amount,
        min(b.notification_date) AS first_date,
        max(b.notification_date) AS last_date
    FROM base AS b
    JOIN division_rate AS d USING (cpv_division)
    GROUP BY b.buyer_siret
)
WHERE n >= 20 AND z >= 3 AND rate >= expected + 0.15

UNION ALL BY NAME

-- 5. Contract awarded without publication or competition above the threshold
-- that allows it (art. R. 2122-8). Exceptions exist (urgency, unsuccessful
-- tender, exclusive rights: R. 2122-1 to R. 2122-11) and must be documented.
SELECT
    'non_competitive_award' AS alert_type,
    CASE WHEN f.awarded_amount >= t.eu_threshold THEN 'high' ELSE 'medium' END AS severity,
    f.uid,
    f.buyer_siret,
    f.lead_holder_key AS holder_key,
    f.cpv_division,
    f.notification_date AS reference_date,
    f.awarded_amount AS amount_at_stake,
    to_json({
        'awarded_amount': f.awarded_amount,
        'no_competition_threshold': t.no_competition_threshold,
        'eu_threshold': t.eu_threshold,
        'procedure_reported': f.procedure_type,
        'contract_type': f.contract_type,
        'notification_date': f.notification_date
    }) AS evidence
FROM fct_contracts AS f
JOIN int_contract_thresholds AS t USING (uid)
WHERE f.procedure_type = 'Without prior publication or competition'
    AND f.awarded_amount >= t.no_competition_threshold

UNION ALL BY NAME

-- 6. Possible splitting: in one calendar year, a buyer awarded several
-- contracts of the same CPV group to the same supplier, on different dates,
-- each below the EU threshold but together above it. Needs of the same
-- category must be added up over the year to choose the procedure
-- (art. R. 2121-1 and following). Works are estimated per operation and are
-- left out; call-off contracts of a framework agreement are expected to repeat.
SELECT
    'possible_splitting' AS alert_type,
    CASE WHEN total >= 2 * threshold THEN 'high' ELSE 'medium' END AS severity,
    contracts[1].uid AS uid,
    buyer_siret,
    lead_holder_key AS holder_key,
    left(cpv_group, 2) AS cpv_division,
    last_date AS reference_date,
    total AS amount_at_stake,
    to_json({
        'year': notification_year,
        'cpv_group': cpv_group,
        'contract_count': n,
        'total_amount': total,
        'eu_threshold': threshold,
        'largest_amount': largest,
        'contracts': contracts
    }) AS evidence
FROM (
    SELECT
        f.buyer_siret,
        f.lead_holder_key,
        f.cpv_group,
        f.notification_year,
        count(*) AS n,
        count(DISTINCT f.notification_date) AS dates,
        sum(f.awarded_amount) AS total,
        max(f.awarded_amount) AS largest,
        max(t.eu_threshold) AS threshold,
        max(f.notification_date) AS last_date,
        list(
            {'uid': f.uid, 'date': f.notification_date, 'amount': f.awarded_amount,
             'reference': f.contract_ref}
            ORDER BY f.notification_date, f.uid
        ) AS contracts
    FROM fct_contracts AS f
    JOIN int_contract_thresholds AS t USING (uid)
    WHERE f.contract_type IN ('Supplies', 'Services')
        AND f.contract_nature <> 'Call-off contract'
        AND f.awarded_amount < t.eu_threshold
        AND f.lead_holder_key IS NOT NULL
        AND f.cpv_group IS NOT NULL
    GROUP BY f.buyer_siret, f.lead_holder_key, f.cpv_group, f.notification_year
)
WHERE n >= 2 AND dates >= 2 AND total >= threshold

UNION ALL BY NAME

-- 7. Supplier whose main activity (NAF) rarely goes with the category bought:
-- fewer than 0.2% of the contracts won by companies of that NAF division.
SELECT
    'unusual_supplier_activity' AS alert_type,
    CASE WHEN awarded_amount >= 1e6 THEN 'medium' ELSE 'low' END AS severity,
    uid,
    buyer_siret,
    holder_key,
    cpv_division,
    notification_date AS reference_date,
    awarded_amount AS amount_at_stake,
    to_json({
        'awarded_amount': awarded_amount,
        'naf_code': naf_code,
        'naf_label': naf_label,
        'cpv_division': cpv_division,
        'share_of_naf_division_contracts': round(pair_share, 5),
        'naf_division_contracts': naf_total,
        'notification_date': notification_date
    }) AS evidence
FROM (
    WITH pairs AS (
        SELECT
            f.uid,
            f.buyer_siret,
            f.lead_holder_key AS holder_key,
            f.cpv_division,
            f.notification_date,
            f.awarded_amount,
            s.naf_code,
            s.naf_label,
            left(s.naf_code, 2) AS naf_division
        FROM fct_contracts AS f
        JOIN dim_suppliers AS s ON s.holder_key = f.lead_holder_key
        WHERE s.naf_code IS NOT NULL
            AND f.cpv_division IS NOT NULL
            AND NOT s.is_individual_entrepreneur
    ),
    frequency AS (
        SELECT
            naf_division,
            cpv_division,
            count(*) AS n,
            sum(count(*)) OVER (PARTITION BY naf_division) AS naf_total
        FROM pairs
        GROUP BY naf_division, cpv_division
    )
    SELECT p.*, f.n / f.naf_total AS pair_share, f.naf_total
    FROM pairs AS p
    JOIN frequency AS f USING (naf_division, cpv_division)
)
WHERE naf_total >= 500 AND pair_share < 0.002 AND awarded_amount >= 100000

UNION ALL BY NAME

-- 8. Supplier recorded as ceased in SIRENE more than 90 days before the award:
-- usually a wrong identifier, sometimes a contract with a dissolved company.
SELECT
    'supplier_ceased_before_award' AS alert_type,
    'medium' AS severity,
    f.uid,
    f.buyer_siret,
    s.holder_key,
    f.cpv_division,
    f.notification_date AS reference_date,
    coalesce(f.awarded_amount, 0) AS amount_at_stake,
    to_json({
        'awarded_amount': f.awarded_amount,
        'cessation_date': s.cessation_date,
        'notification_date': f.notification_date,
        'days_between': date_diff('day', s.cessation_date, f.notification_date)
    }) AS evidence
FROM fct_contracts AS f
JOIN dim_suppliers AS s ON s.holder_key = f.lead_holder_key
WHERE s.is_ceased
    AND NOT s.is_individual_entrepreneur
    AND s.cessation_date < f.notification_date - INTERVAL 90 DAY

UNION ALL BY NAME

-- 9. Large contract won by a company created less than six months earlier.
SELECT
    'newly_created_supplier' AS alert_type,
    CASE WHEN f.awarded_amount >= 5e6 THEN 'high' ELSE 'medium' END AS severity,
    f.uid,
    f.buyer_siret,
    s.holder_key,
    f.cpv_division,
    f.notification_date AS reference_date,
    f.awarded_amount AS amount_at_stake,
    to_json({
        'awarded_amount': f.awarded_amount,
        'creation_date': s.creation_date,
        'notification_date': f.notification_date,
        'company_age_days': date_diff('day', s.creation_date, f.notification_date)
    }) AS evidence
FROM fct_contracts AS f
JOIN dim_suppliers AS s ON s.holder_key = f.lead_holder_key
WHERE NOT s.is_individual_entrepreneur
    AND s.creation_date BETWEEN f.notification_date - INTERVAL 180 DAY AND f.notification_date
    AND f.awarded_amount >= 500000

UNION ALL BY NAME

-- 10. Dependency: one supplier holds most of a buyer's spend in a category.
SELECT
    'supplier_dependency' AS alert_type,
    CASE WHEN top_share >= 0.8 AND total >= 5e6 THEN 'high' ELSE 'medium' END AS severity,
    NULL AS uid,
    buyer_siret,
    top_holder AS holder_key,
    cpv_division,
    last_date AS reference_date,
    top_amount AS amount_at_stake,
    to_json({
        'category_amount': total,
        'top_supplier_amount': top_amount,
        'top_supplier_share': round(top_share, 3),
        'suppliers': suppliers,
        'contracts': contracts,
        'hhi': round(hhi, 3),
        'first_date': first_date,
        'last_date': last_date
    }) AS evidence
FROM (
    WITH by_supplier AS (
        SELECT
            f.buyer_siret,
            f.cpv_division,
            h.holder_key,
            sum(h.allocated_amount) AS amount,
            count(*) AS contracts,
            min(f.notification_date) AS first_date,
            max(f.notification_date) AS last_date
        FROM fct_contract_holders AS h
        JOIN fct_contracts AS f USING (uid)
        WHERE h.allocated_amount > 0 AND f.cpv_division IS NOT NULL
        GROUP BY f.buyer_siret, f.cpv_division, h.holder_key
    ),
    shares AS (
        SELECT
            *,
            amount / sum(amount) OVER (PARTITION BY buyer_siret, cpv_division) AS share
        FROM by_supplier
    )
    SELECT
        buyer_siret,
        cpv_division,
        sum(amount) AS total,
        count(*) AS suppliers,
        sum(contracts) AS contracts,
        arg_max(holder_key, amount) AS top_holder,
        max(amount) AS top_amount,
        max(share) AS top_share,
        sum(share * share) AS hhi,
        min(first_date) AS first_date,
        max(last_date) AS last_date
    FROM shares
    GROUP BY buyer_siret, cpv_division
)
WHERE total >= 1e6 AND contracts >= 3 AND top_share >= 0.6

UNION ALL BY NAME

-- 11. Several records for one award: the same contract published several
-- times (duplicate), or one framework agreement published once per supplier.
-- The extra records are excluded from totals; the alert shows what was removed.
SELECT
    CASE WHEN d.group_kind = 'duplicate' THEN 'duplicate_declarations'
        ELSE 'framework_split_records' END AS alert_type,
    CASE WHEN d.group_size >= 5 OR (d.group_size - 1) * coalesce(f.awarded_amount, 0) >= 1e7
        THEN 'high' ELSE 'medium' END AS severity,
    f.uid,
    f.buyer_siret,
    f.lead_holder_key AS holder_key,
    f.cpv_division,
    f.notification_date AS reference_date,
    (d.group_size - 1) * coalesce(f.awarded_amount, 0) AS amount_at_stake,
    to_json({
        'records': d.group_size,
        'amount': f.reported_amount,
        'notification_date': f.notification_date,
        'kept_record': f.uid,
        'holders': f.holder_count,
        'removed_records': r.removed
    }) AS evidence
FROM int_duplicates AS d
JOIN fct_contracts AS f ON f.uid = d.uid
JOIN (
    SELECT
        o.duplicate_group,
        list({'uid': c.uid, 'reference': c.contract_ref, 'source': c.source_dataset} ORDER BY c.uid)
            AS removed
    FROM int_duplicates AS o
    JOIN int_contracts AS c USING (uid)
    WHERE NOT o.is_canonical
    GROUP BY o.duplicate_group
) AS r USING (duplicate_group)
WHERE d.is_canonical;

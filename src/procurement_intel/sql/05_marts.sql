-- Marts: the star schema used by the application, the detectors and Power BI.
-- Amounts are in euros excluding VAT, as awarded (for framework agreements,
-- the maximum or estimated amount), not amounts paid.

-- CPV hierarchy: every code with its division, group and class labels.
CREATE OR REPLACE TABLE dim_cpv AS
WITH cpv AS (
    SELECT * FROM read_csv('{reference_dir}/cpv_2008.csv', all_varchar = true)
)
SELECT
    c.code AS cpv_code,
    c.label_en AS cpv_label,
    c.label_fr AS cpv_label_fr,
    c.level AS cpv_level,
    c.division AS cpv_division,
    d.label_en AS cpv_division_label,
    left(c.code, 3) || '00000' AS cpv_group,
    g.label_en AS cpv_group_label,
    left(c.code, 4) || '0000' AS cpv_class,
    k.label_en AS cpv_class_label
FROM cpv AS c
LEFT JOIN cpv AS d ON d.code = c.division || '000000'
LEFT JOIN cpv AS g ON g.code = left(c.code, 3) || '00000'
LEFT JOIN cpv AS k ON k.code = left(c.code, 4) || '0000';

-- Amount status: missing, implausible (outside hard bounds, or at least 5
-- robust standard deviations from the amount the model expects for
-- comparable contracts) or valid. Only valid amounts enter totals.
CREATE OR REPLACE TABLE int_amount_status AS
SELECT
    q.uid,
    q.initial_amount,
    q.amount_z AS amount_z_segment,
    q.segment_median_amount,
    m.amount_z_model,
    -- Contracts outside the hard bounds are not scored by the model: their
    -- segment median is the expected amount.
    coalesce(m.expected_amount, q.segment_median_amount) AS expected_amount,
    CASE
        WHEN q.initial_amount IS NULL THEN 'missing'
        WHEN q.initial_amount < 100 OR q.initial_amount >= 1e10 THEN 'implausible'
        WHEN abs(m.amount_z_model) >= 5 THEN 'implausible'
        ELSE 'valid'
    END AS amount_status
FROM int_amount_quality AS q
LEFT JOIN int_amount_model AS m USING (uid);

-- Holders of each kept record: its own holders, plus those of the records it
-- replaces when one framework agreement was published once per supplier.
CREATE OR REPLACE TABLE int_kept_holders AS
SELECT DISTINCT ON (uid, holder_key)
    coalesce(d.canonical_uid, h.uid) AS uid,
    h.holder_key,
    h.holder_size,
    h.holder_naf_code,
    h.holder_department_code,
    h.holder_region_code,
    h.holder_distance_km
FROM int_contract_holders AS h
LEFT JOIN int_duplicates AS d USING (uid)
ORDER BY uid, holder_key, h.uid;

CREATE OR REPLACE TABLE fct_contracts AS
SELECT
    c.uid,
    c.contract_ref,
    c.buyer_siret,
    c.notification_date,
    year(c.notification_date) AS notification_year,
    date_trunc('month', c.notification_date)::DATE AS notification_month,
    c.publication_date,
    c.description,
    c.contract_type,
    c.contract_nature,
    coalesce(c.is_framework, false) AS is_framework,
    c.procedure_type,
    c.procedure_group,
    c.offers_received,
    c.cpv_code,
    -- Codes outside the CPV 2008 list (such as old CPV 2003 codes) are kept
    -- in cpv_code but get no division.
    p.cpv_division,
    p.cpv_group,
    c.initial_amount AS reported_amount,
    q.amount_status,
    q.amount_z_model,
    q.expected_amount,
    q.amount_z_segment,
    CASE WHEN q.amount_status = 'valid' THEN c.initial_amount END AS awarded_amount,
    c.initial_duration_months AS duration_months,
    c.price_form,
    c.price_variation,
    c.modification_count,
    c.last_modification_date,
    c.current_amount AS reported_current_amount,
    -- Change of amount through modifications, for valid amounts. A ratio
    -- beyond 1:20 is almost always a reporting error (the amount of the
    -- modification alone, or a unit error), not a real change.
    CASE
        WHEN q.amount_status = 'valid' AND c.modification_count > 0 AND c.current_amount > 0
            AND c.current_amount / c.initial_amount BETWEEN 0.05 AND 20
            THEN round(c.current_amount / c.initial_amount - 1, 4)
    END AS amount_change_ratio,
    CASE
        WHEN c.modification_count = 0 OR c.current_amount IS NULL THEN 'no modification'
        WHEN c.current_amount = c.initial_amount THEN 'unchanged'
        WHEN q.amount_status <> 'valid' THEN 'not assessed'
        WHEN c.current_amount / c.initial_amount NOT BETWEEN 0.05 AND 20 THEN 'implausible'
        WHEN c.current_amount > c.initial_amount THEN 'increased'
        ELSE 'decreased'
    END AS amount_change_status,
    coalesce(k.holders, c.holder_count) AS holder_count,
    c.lead_holder_key,
    c.buyer_category,
    c.buyer_department_code,
    c.buyer_region_code,
    c.social_considerations,
    c.environmental_considerations,
    c.subcontracting_declared,
    c.consortium_type,
    c.is_innovative,
    c.producer_amount_flag,
    c.source_dataset,
    coalesce(d.group_size, 1) AS records,
    coalesce(d.group_kind, 'single record') AS record_group_kind
FROM int_contracts AS c
JOIN int_amount_status AS q USING (uid)
LEFT JOIN int_duplicates AS d USING (uid)
LEFT JOIN dim_cpv AS p ON p.cpv_code = c.cpv_code
LEFT JOIN (SELECT uid, count(*) AS holders FROM int_kept_holders GROUP BY uid) AS k USING (uid)
WHERE coalesce(d.is_canonical, true);

-- Contract holders, with the awarded amount shared equally between the
-- holders of a contract (co-contractors or several suppliers of one
-- framework agreement), so that supplier totals add up to the contract total.
CREATE OR REPLACE TABLE fct_contract_holders AS
SELECT
    h.uid,
    h.holder_key,
    f.awarded_amount / f.holder_count AS allocated_amount,
    h.holder_size,
    h.holder_naf_code,
    h.holder_department_code,
    h.holder_region_code,
    h.holder_distance_km
FROM int_kept_holders AS h
JOIN fct_contracts AS f USING (uid);

CREATE OR REPLACE TABLE dim_buyers AS
SELECT
    buyer_siret,
    arg_max(buyer_name, notification_date) AS buyer_name,
    arg_max(buyer_category, notification_date) AS buyer_category,
    arg_max(buyer_department_code, notification_date) AS buyer_department_code,
    arg_max(buyer_department, notification_date) AS buyer_department,
    arg_max(buyer_region_code, notification_date) AS buyer_region_code,
    arg_max(buyer_region, notification_date) AS buyer_region
FROM int_contracts
WHERE uid IN (SELECT uid FROM fct_contracts)
GROUP BY buyer_siret;

-- Suppliers (one row per company). The name of an individual entrepreneur is
-- withheld: they are natural persons, and their SIRENE record may not be
-- public. SIRENE gives the legal form, creation date and status.
CREATE OR REPLACE TABLE dim_suppliers AS
WITH holders AS (
    SELECT
        h.holder_key,
        arg_max(h.holder_siren, c.notification_date) AS holder_siren,
        arg_max(h.holder_name, c.notification_date) AS reported_name,
        arg_max(h.holder_size, c.notification_date) AS holder_size,
        arg_max(h.holder_naf_code, c.notification_date) AS naf_code,
        arg_max(h.holder_naf_label, c.notification_date) AS naf_label,
        arg_max(h.holder_department_code, c.notification_date) AS department_code,
        arg_max(h.holder_region_code, c.notification_date) AS region_code
    FROM int_contract_holders AS h
    JOIN int_contracts AS c USING (uid)
    WHERE h.holder_key IN (SELECT holder_key FROM fct_contract_holders)
    GROUP BY h.holder_key
),
sirene AS (
    SELECT * FROM read_parquet('{snapshot_dir}/sirene_titulaires.parquet')
)
SELECT
    h.holder_key,
    h.holder_siren,
    coalesce(s.categorieJuridiqueUniteLegale = '1000', false) AS is_individual_entrepreneur,
    CASE
        WHEN s.categorieJuridiqueUniteLegale = '1000' OR s.statutDiffusionUniteLegale = 'P'
            THEN 'Individual entrepreneur (name withheld)'
        ELSE coalesce(h.reported_name, 'Unnamed holder ' || h.holder_key)
    END AS supplier_name,
    h.holder_size,
    h.naf_code,
    h.naf_label,
    h.department_code,
    h.region_code,
    s.categorieJuridiqueUniteLegale AS legal_category_code,
    s.dateCreationUniteLegale AS creation_date,
    s.etatAdministratifUniteLegale = 'C' AS is_ceased,
    CASE WHEN s.etatAdministratifUniteLegale = 'C' THEN s.dateDebut END AS cessation_date,
    s.trancheEffectifsUniteLegale AS headcount_band,
    s.economieSocialeSolidaireUniteLegale = 'O' AS is_social_economy,
    s.siren IS NOT NULL AS found_in_sirene
FROM holders AS h
LEFT JOIN sirene AS s ON s.siren = h.holder_siren;

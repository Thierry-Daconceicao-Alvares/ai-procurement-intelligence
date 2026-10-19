-- Contracts: one row per contract (uid) notified in the analysis window, with
-- the attributes of its award and its current state after modifications.
-- Fields are taken from the first row of the first version, in a fixed order,
-- so that the result does not depend on how the source files were read.

CREATE OR REPLACE TABLE int_contract_rows AS
SELECT
    *,
    row_number() OVER (
        PARTITION BY uid, modification_id
        ORDER BY source_dataset, holder_key NULLS LAST, description, contract_ref
    ) AS row_order,
    -- Rows of the award: version 0, or the first version reported.
    modification_id = min(modification_id) OVER (PARTITION BY uid) AS is_award_row
FROM stg_decp;

-- One row per version of a contract: the award (0) and each modification.
CREATE OR REPLACE TABLE int_contract_versions AS
SELECT
    uid,
    modification_id,
    max(amount) AS amount,
    arg_min(duration_months, row_order) FILTER (WHERE duration_months IS NOT NULL)
        AS duration_months,
    min(notification_date) AS notification_date,
    bool_or(is_current) AS is_current
FROM int_contract_rows
GROUP BY uid, modification_id;

CREATE OR REPLACE TABLE int_contracts AS
WITH award_rows AS (
    SELECT * FROM int_contract_rows WHERE is_award_row
),
award AS (
    SELECT
        uid,
        arg_min(contract_ref, row_order) AS contract_ref,
        arg_min(source_dataset, row_order) AS source_dataset,
        arg_min(buyer_siret, row_order) FILTER (WHERE buyer_siret IS NOT NULL) AS buyer_siret,
        arg_min(buyer_name, row_order) FILTER (WHERE buyer_name IS NOT NULL) AS buyer_name,
        arg_min(buyer_category, row_order) AS buyer_category,
        arg_min(buyer_department_code, row_order) FILTER (WHERE buyer_department_code IS NOT NULL)
            AS buyer_department_code,
        arg_min(buyer_department, row_order) FILTER (WHERE buyer_department IS NOT NULL)
            AS buyer_department,
        arg_min(buyer_region_code, row_order) FILTER (WHERE buyer_region_code IS NOT NULL)
            AS buyer_region_code,
        arg_min(buyer_region, row_order) FILTER (WHERE buyer_region IS NOT NULL) AS buyer_region,
        arg_min(description, row_order) FILTER (WHERE description IS NOT NULL) AS description,
        arg_min(cpv_code, row_order) FILTER (WHERE cpv_code IS NOT NULL) AS cpv_code,
        arg_min(contract_type, row_order) AS contract_type,
        arg_min(contract_nature, row_order) AS contract_nature,
        bool_or(is_framework) AS is_framework,
        arg_min(framework_ref, row_order) FILTER (WHERE framework_ref IS NOT NULL)
            AS framework_ref,
        arg_min(procedure_fr, row_order) FILTER (WHERE procedure_fr IS NOT NULL) AS procedure_fr,
        arg_min(procedure_type, row_order) AS procedure_type,
        arg_min(procedure_group, row_order) AS procedure_group,
        bool_or(is_dynamic_purchasing_system) AS is_dynamic_purchasing_system,
        arg_min(offers_received, row_order) FILTER (WHERE offers_received IS NOT NULL)
            AS offers_received,
        max(amount) AS initial_amount,
        arg_min(producer_amount_flag, row_order) FILTER (WHERE producer_amount_flag IS NOT NULL)
            AS producer_amount_flag,
        arg_min(duration_months, row_order) FILTER (WHERE duration_months IS NOT NULL)
            AS initial_duration_months,
        min(notification_date) AS notification_date,
        min(publication_date) AS publication_date,
        arg_min(price_form, row_order) FILTER (WHERE price_form IS NOT NULL) AS price_form,
        arg_min(price_variation, row_order) FILTER (WHERE price_variation IS NOT NULL)
            AS price_variation,
        arg_min(social_considerations, row_order) FILTER (WHERE social_considerations IS NOT NULL)
            AS social_considerations,
        arg_min(environmental_considerations, row_order)
            FILTER (WHERE environmental_considerations IS NOT NULL) AS environmental_considerations,
        bool_or(subcontracting_declared) AS subcontracting_declared,
        arg_min(consortium_type, row_order) FILTER (WHERE consortium_type IS NOT NULL)
            AS consortium_type,
        bool_or(is_innovative) AS is_innovative,
        list_sort(list_distinct(list(holder_key) FILTER (WHERE holder_key IS NOT NULL)))
            AS holder_keys
    FROM award_rows
    GROUP BY uid
),
changes AS (
    SELECT
        uid,
        count(*) FILTER (WHERE modification_id > 0) AS modification_count,
        max(notification_date) FILTER (WHERE modification_id > 0) AS last_modification_date,
        arg_max(amount, modification_id) FILTER (WHERE amount IS NOT NULL) AS current_amount,
        arg_max(duration_months, modification_id) FILTER (WHERE duration_months IS NOT NULL)
            AS current_duration_months
    FROM int_contract_versions
    GROUP BY uid
)
SELECT
    award.*,
    len(holder_keys) AS holder_count,
    holder_keys[1] AS lead_holder_key,
    changes.modification_count,
    changes.last_modification_date,
    changes.current_amount,
    changes.current_duration_months
FROM award
JOIN changes USING (uid)
WHERE award.notification_date BETWEEN DATE '{start_date}' AND DATE '{end_date}';

-- Holders of each contract at award, with their attributes as reported.
CREATE OR REPLACE TABLE int_contract_holders AS
SELECT
    r.uid,
    r.holder_key,
    arg_min(r.holder_siren, r.row_order) FILTER (WHERE r.holder_siren IS NOT NULL) AS holder_siren,
    arg_min(r.holder_siret, r.row_order) FILTER (WHERE r.holder_siret IS NOT NULL) AS holder_siret,
    arg_min(r.holder_name, r.row_order) FILTER (WHERE r.holder_name IS NOT NULL) AS holder_name,
    arg_min(r.holder_size, r.row_order) FILTER (WHERE r.holder_size IS NOT NULL) AS holder_size,
    arg_min(r.holder_naf_code, r.row_order) FILTER (WHERE r.holder_naf_code IS NOT NULL)
        AS holder_naf_code,
    arg_min(r.holder_naf_label, r.row_order) FILTER (WHERE r.holder_naf_label IS NOT NULL)
        AS holder_naf_label,
    arg_min(r.holder_department_code, r.row_order)
        FILTER (WHERE r.holder_department_code IS NOT NULL) AS holder_department_code,
    arg_min(r.holder_region_code, r.row_order) FILTER (WHERE r.holder_region_code IS NOT NULL)
        AS holder_region_code,
    min(r.holder_distance_km) AS holder_distance_km
FROM int_contract_rows AS r
JOIN int_contracts AS c USING (uid)
WHERE r.holder_key IS NOT NULL AND r.is_award_row
GROUP BY r.uid, r.holder_key;

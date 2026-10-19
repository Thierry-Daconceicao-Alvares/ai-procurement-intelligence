-- Staging: one row per row of the consolidated DECP, with typed, validated and
-- normalised fields. A contract appears on several rows: one per holder
-- (co-contractors, multi-supplier framework agreements) and per modification.
-- Labels are translated to English; the original French values are kept where
-- they help a reader check the source.

CREATE OR REPLACE TABLE stg_decp AS
WITH raw AS (
    SELECT
        *,
        strip_accents(lower(trim(procedure))) AS procedure_key,
        strip_accents(lower(trim(nature))) AS nature_key,
        regexp_replace(coalesce(titulaire_id, ''), '\s', '', 'g') AS holder_id_clean
    FROM read_parquet('{snapshot_dir}/decp/*.parquet')
)
SELECT
    uid,
    trim(id) AS contract_ref,
    coalesce(modification_id, 0) AS modification_id,
    coalesce(donneesActuelles, false) AS is_current,
    sourceDataset AS source_dataset,

    -- Buyer
    acheteur_id AS buyer_siret,
    acheteur_nom AS buyer_name,
    CASE acheteur_categorie
        WHEN 'Commune' THEN 'Municipality'
        WHEN 'Groupement de communes' THEN 'Inter-municipal body'
        WHEN 'Département' THEN 'Department'
        WHEN 'Département outre-mer' THEN 'Overseas department'
        WHEN 'Région' THEN 'Region'
        WHEN 'État' THEN 'Central government'
        WHEN 'Établissement hospitalier' THEN 'Hospital'
        WHEN 'EPIC' THEN 'Public industrial or commercial body'
        WHEN 'Syndicat mixte' THEN 'Joint authority'
        ELSE 'Other or unknown'
    END AS buyer_category,
    acheteur_departement_code AS buyer_department_code,
    acheteur_departement_nom AS buyer_department,
    acheteur_region_code AS buyer_region_code,
    acheteur_region_nom AS buyer_region,

    -- Holder (titulaire). SIRET numbers identify French establishments; the
    -- company is identified by the first nine digits (SIREN). Placeholders such
    -- as "00001" are not identifiers.
    CASE
        WHEN regexp_full_match(holder_id_clean, '\d{14}')
            AND holder_id_clean <> '00000000000000' THEN holder_id_clean
    END AS holder_siret,
    CASE
        WHEN regexp_full_match(holder_id_clean, '\d{14}')
            AND holder_id_clean <> '00000000000000' THEN left(holder_id_clean, 9)
        WHEN regexp_full_match(holder_id_clean, '\d{9}')
            AND holder_id_clean NOT IN ('000000000', '999999999') THEN holder_id_clean
    END AS holder_siren,
    CASE
        WHEN regexp_full_match(holder_id_clean, '\d{14}')
            AND holder_id_clean <> '00000000000000' THEN 'FR:' || left(holder_id_clean, 9)
        WHEN regexp_full_match(holder_id_clean, '\d{9}')
            AND holder_id_clean NOT IN ('000000000', '999999999') THEN 'FR:' || holder_id_clean
        WHEN length(holder_id_clean) >= 6
            AND NOT regexp_full_match(holder_id_clean, '[0 ]*\d{0,2}')
            AND holder_id_clean NOT IN ('999999999', '9999999999') THEN 'ID:' || upper(holder_id_clean)
    END AS holder_key,
    titulaire_nom AS holder_name,
    CASE titulaire_categorie
        WHEN 'PME' THEN 'SME'
        WHEN 'ETI' THEN 'Mid-sized company'
        WHEN 'GE' THEN 'Large company'
    END AS holder_size,
    titulaire_activite_code AS holder_naf_code,
    titulaire_activite_libelle AS holder_naf_label,
    titulaire_departement_code AS holder_department_code,
    titulaire_region_code AS holder_region_code,
    CASE WHEN titulaire_distance BETWEEN 0 AND 20000 THEN titulaire_distance END AS holder_distance_km,

    -- What was bought
    objet AS description,
    CASE
        WHEN regexp_full_match(left(codeCPV, 8), '\d{8}') AND left(codeCPV, 8) <> '00000000'
            THEN left(codeCPV, 8)
    END AS cpv_code,
    CASE "type"
        WHEN 'Travaux' THEN 'Works'
        WHEN 'Services' THEN 'Services'
        WHEN 'Fournitures' THEN 'Supplies'
        ELSE 'Uncategorised'
    END AS contract_type,
    CASE
        WHEN nature_key LIKE 'accord%cadre%' THEN 'Framework agreement'
        WHEN nature_key LIKE '%subsequent%' THEN 'Call-off contract'
        WHEN nature_key LIKE '%partenariat%' THEN 'Partnership contract'
        WHEN nature_key LIKE '%defense%' OR nature_key LIKE '%securite%'
            THEN 'Defence or security contract'
        WHEN nature_key LIKE 'marche%' THEN 'Contract'
        WHEN nature_key IS NULL THEN 'Contract'
        ELSE 'Other'
    END AS contract_nature,
    (
        coalesce(nature_key, '') LIKE 'accord%cadre%'
        OR coalesce(strip_accents(lower(techniques)), '') LIKE '%accord%cadre%'
    ) AS is_framework,
    idAccordCadre AS framework_ref,

    -- How it was awarded
    procedure AS procedure_fr,
    CASE
        WHEN procedure_key IS NULL THEN 'Not reported'
        WHEN procedure_key LIKE '%sans publicite%' THEN 'Without prior publication or competition'
        WHEN procedure_key LIKE 'procedure adaptee%' THEN 'Adapted procedure'
        WHEN procedure_key LIKE 'appel d%offres ouvert%' THEN 'Open tender'
        WHEN procedure_key LIKE 'appel d%offres restreint%' THEN 'Restricted tender'
        WHEN procedure_key LIKE '%dialogue%' THEN 'Competitive dialogue'
        WHEN procedure_key LIKE '%negoci%' THEN 'Competitive procedure with negotiation'
        ELSE 'Other'
    END AS procedure_type,
    CASE
        WHEN procedure_key IS NULL THEN 'Unknown'
        WHEN procedure_key LIKE '%sans publicite%' THEN 'Non-competitive'
        ELSE 'Competitive'
    END AS procedure_group,
    coalesce(strip_accents(lower(techniques)), '') LIKE '%acquisition dynamique%'
        AS is_dynamic_purchasing_system,
    CASE WHEN offresRecues BETWEEN 0 AND 999 THEN offresRecues END AS offers_received,

    -- Amount and dates
    CASE WHEN montant > 0 THEN montant END AS amount,
    montant_anomalie AS producer_amount_flag,
    CASE WHEN dureeMois BETWEEN 1 AND 600 THEN dureeMois END AS duration_months,
    CASE
        WHEN dateNotification BETWEEN DATE '2015-01-01' AND DATE '{snapshot_date}'
            THEN dateNotification
    END AS notification_date,
    datePublicationDonnees AS publication_date,

    -- Price
    CASE formePrix
        WHEN 'Forfaitaire' THEN 'Lump sum'
        WHEN 'Unitaire' THEN 'Unit prices'
        WHEN 'Mixte' THEN 'Mixed'
    END AS price_form,
    CASE
        WHEN formePrix = 'Ferme' OR typesPrix LIKE 'Définitif ferme%' THEN 'Fixed'
        WHEN formePrix = 'Révisable' OR typesPrix LIKE 'Définitif révisable%' THEN 'Revisable'
        WHEN formePrix = 'Ferme et actualisable' OR typesPrix LIKE 'Définitif actualisable%'
            THEN 'Fixed and adjustable'
        WHEN typesPrix LIKE 'Provisoire%' THEN 'Provisional'
    END AS price_variation,

    -- Policy fields (published since 2024)
    considerationsSociales AS social_considerations,
    considerationsEnvironnementales AS environmental_considerations,
    sousTraitanceDeclaree AS subcontracting_declared,
    typeGroupementOperateurs AS consortium_type,
    marcheInnovant AS is_innovative
FROM raw;

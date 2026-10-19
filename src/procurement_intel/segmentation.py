"""Supplier segments and category supply risk (Kraljic matrix).

Suppliers: k-means on standardised features of each company's public
business (value, value per contract, contracts, buyers, categories, regions,
distance, dependency on its main buyer). Individual entrepreneurs are left out:
they are natural persons. Each segment is named after its most distinctive
features, so that the names follow the data.

Categories (CPV groups): the Kraljic matrix crosses the value at stake (profit
impact) with the supply risk of the market. Supply risk combines three market
measures, each turned into a percentile among categories: concentration of
suppliers (HHI), share of single-offer procedures and scarcity of offers
(inverse of the average number of offers).
"""

from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler

SUPPLIER_FEATURES_SQL = """
WITH lines AS (
    SELECT
        h.holder_key,
        h.allocated_amount,
        h.holder_distance_km,
        f.uid,
        f.buyer_siret,
        f.cpv_division,
        f.buyer_region_code,
        f.offers_received,
        f.is_framework,
        f.notification_date
    FROM fct_contract_holders AS h
    JOIN fct_contracts AS f USING (uid)
    JOIN dim_suppliers AS s USING (holder_key)
    WHERE h.allocated_amount > 0 AND NOT s.is_individual_entrepreneur
),
top_buyer AS (
    SELECT holder_key, max(amount) AS top_amount
    FROM (
        SELECT holder_key, buyer_siret, sum(allocated_amount) AS amount
        FROM lines
        GROUP BY holder_key, buyer_siret
    )
    GROUP BY holder_key
)
SELECT
    l.holder_key,
    sum(l.allocated_amount) AS total_value,
    count(DISTINCT l.uid) AS contracts,
    count(DISTINCT l.buyer_siret) AS buyers,
    count(DISTINCT l.cpv_division) AS divisions,
    count(DISTINCT l.buyer_region_code) AS regions,
    median(l.holder_distance_km) AS median_distance_km,
    any_value(t.top_amount) / sum(l.allocated_amount) AS top_buyer_share,
    avg((l.offers_received = 1)::INT) FILTER (WHERE l.offers_received IS NOT NULL)
        AS single_bid_share,
    avg(l.is_framework::INT) AS framework_share,
    date_diff('day', min(l.notification_date), max(l.notification_date)) / 365.25
        AS active_years
FROM lines AS l
JOIN top_buyer AS t USING (holder_key)
GROUP BY l.holder_key
HAVING count(DISTINCT l.uid) >= 3
ORDER BY l.holder_key
"""

FEATURES = [
    "log_total_value",
    "log_value_per_contract",
    "log_contracts",
    "log_buyers",
    "divisions",
    "regions",
    "log_distance",
    "top_buyer_share",
]

# Names given to a segment from its standardised centroid, checked in order.
SEGMENT_RULES = [
    ("National multi-category suppliers", lambda c: c["log_buyers"] > 1 and c["regions"] > 1),
    ("Large-contract holders", lambda c: c["log_value_per_contract"] > 0.8),
    ("Suppliers dependent on one buyer", lambda c: c["top_buyer_share"] > 0.8),
    ("Distant specialists", lambda c: c["log_distance"] > 0.5),
    ("Local specialists", lambda c: c["log_distance"] < -0.3),
]


def name_segments(centroids: pd.DataFrame) -> dict[int, str]:
    names: dict[int, str] = {}
    used: set[str] = set()
    for index, centroid in centroids.iterrows():
        for name, rule in SEGMENT_RULES:
            if name not in used and rule(centroid):
                names[index] = name
                used.add(name)
                break
        else:
            names[index] = (
                "Regional generalists"
                if "Regional generalists" not in used
                else (f"Segment {index + 1}")
            )
            used.add(names[index])
    return names


def supplier_segments(con: duckdb.DuckDBPyConnection, clusters: int = 6) -> pd.DataFrame:
    frame = con.execute(SUPPLIER_FEATURES_SQL).fetchdf()
    frame["log_total_value"] = np.log10(frame["total_value"])
    frame["log_value_per_contract"] = np.log10(frame["total_value"] / frame["contracts"])
    frame["log_contracts"] = np.log10(frame["contracts"])
    frame["log_buyers"] = np.log10(frame["buyers"])
    frame["log_distance"] = np.log10(
        1 + frame["median_distance_km"].fillna(frame["median_distance_km"].median())
    )
    values = frame[FEATURES].astype(float)
    scaler = StandardScaler().fit(values)
    model = KMeans(n_clusters=clusters, n_init=10, random_state=42).fit(scaler.transform(values))
    centroids = pd.DataFrame(model.cluster_centers_, columns=FEATURES)
    names = name_segments(centroids)
    frame["segment"] = [names[label] for label in model.labels_]
    return frame


CATEGORY_MARKET_SQL = """
WITH lines AS (
    SELECT f.cpv_group, h.holder_key, h.allocated_amount
    FROM fct_contract_holders AS h
    JOIN fct_contracts AS f USING (uid)
    WHERE h.allocated_amount > 0 AND f.cpv_group IS NOT NULL
),
supplier_shares AS (
    SELECT
        cpv_group,
        holder_key,
        sum(allocated_amount) / sum(sum(allocated_amount)) OVER (PARTITION BY cpv_group) AS share
    FROM lines
    GROUP BY cpv_group, holder_key
),
concentration AS (
    SELECT cpv_group, sum(share * share) AS hhi, max(share) AS top_supplier_share,
        count(*) AS suppliers
    FROM supplier_shares
    GROUP BY cpv_group
),
competition AS (
    SELECT
        cpv_group,
        count(*) AS contracts,
        sum(awarded_amount) AS total_value,
        avg(offers_received) FILTER (WHERE procedure_group = 'Competitive') AS avg_offers,
        avg((offers_received = 1)::INT)
            FILTER (WHERE procedure_group = 'Competitive' AND offers_received IS NOT NULL)
            AS single_bid_share,
        count(*) FILTER (WHERE procedure_group = 'Competitive' AND offers_received IS NOT NULL)
            AS procedures_with_offers
    FROM fct_contracts
    WHERE cpv_group IS NOT NULL
    GROUP BY cpv_group
)
SELECT
    c.cpv_group,
    g.cpv_label AS cpv_group_label,
    left(c.cpv_group, 2) AS cpv_division,
    c.contracts,
    c.total_value,
    k.suppliers,
    k.hhi,
    k.top_supplier_share,
    c.avg_offers,
    c.single_bid_share,
    c.procedures_with_offers
FROM competition AS c
JOIN concentration AS k USING (cpv_group)
LEFT JOIN dim_cpv AS g ON g.cpv_code = c.cpv_group
ORDER BY c.cpv_group
"""


def category_markets(con: duckdb.DuckDBPyConnection, min_procedures: int = 30) -> pd.DataFrame:
    """Supply risk and Kraljic quadrant of each CPV group with enough data."""
    frame = con.execute(CATEGORY_MARKET_SQL).fetchdf()
    frame = frame[frame["procedures_with_offers"] >= min_procedures].copy()
    risk_parts = pd.DataFrame(
        {
            "hhi": frame["hhi"].rank(pct=True),
            "single_bid": frame["single_bid_share"].rank(pct=True),
            "scarce_offers": (1 / frame["avg_offers"]).rank(pct=True),
        }
    )
    frame["supply_risk"] = risk_parts.mean(axis=1).round(3)
    frame["value_percentile"] = frame["total_value"].rank(pct=True).round(3)
    high_value = frame["value_percentile"] >= 0.5
    high_risk = frame["supply_risk"] >= 0.5
    frame["kraljic_quadrant"] = np.select(
        [high_value & high_risk, ~high_value & high_risk, high_value & ~high_risk],
        ["Strategic", "Bottleneck", "Leverage"],
        default="Non-critical",
    )
    return frame


def build_segments(con: duckdb.DuckDBPyConnection) -> dict:
    suppliers = supplier_segments(con)
    markets = category_markets(con)
    for name, frame in (("fct_supplier_profiles", suppliers), ("fct_category_markets", markets)):
        con.register("frame", frame)
        con.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM frame")
        con.unregister("frame")
    summary = (
        suppliers.groupby("segment")
        .agg(
            suppliers=("holder_key", "count"),
            median_contracts=("contracts", "median"),
            median_buyers=("buyers", "median"),
            median_value=("total_value", "median"),
            median_top_buyer_share=("top_buyer_share", "median"),
        )
        .round(3)
        .reset_index()
        .to_dict("records")
    )
    quadrants = markets["kraljic_quadrant"].value_counts().to_dict()
    return {"supplier_segments": summary, "kraljic_quadrants": quadrants}

"""Group the records that describe the same award and choose the one to keep.

The SQL model 03_duplicate_candidates finds pairs of records that describe the
same award. Pairs are linked into groups (connected components: if A is linked
to B and B to C, the three describe one award). In each group, the most
complete record is kept as the canonical one; the others are excluded from
every count and amount, but stay visible as data-quality findings.

A group is a "joint_purchase" when its records come from several buyers (legal
units): the members of a purchasing group each published the contract. It is
a "multi_supplier_framework" when one buyer published one framework agreement
once per supplier: its suppliers are then all attached to the canonical
record. Otherwise it is a "duplicate".
"""

from __future__ import annotations

import duckdb
import pandas as pd


class UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, item: str) -> str:
        parent = self.parent.setdefault(item, item)
        while parent != item:
            grand = self.parent[parent]
            self.parent[item] = grand
            item, parent = parent, grand
        return item

    def union(self, a: str, b: str) -> None:
        root_a, root_b = self.find(a), self.find(b)
        if root_a != root_b:
            # The smaller identifier becomes the root, so that groups are stable.
            low, high = sorted((root_a, root_b))
            self.parent[high] = low


def group_pairs(pairs: list[tuple[str, str]]) -> dict[str, str]:
    """Map every record that appears in a pair to its group identifier."""
    groups = UnionFind()
    for a, b in pairs:
        groups.union(a, b)
    return {item: groups.find(item) for item in groups.parent}


# Completeness score: the canonical record is the one that reports the most of
# these fields; ties go to the earliest publication, then the smallest uid. A
# joint purchase is first kept under the buyer with the most contracts.
COMPLETENESS = """
    (offers_received IS NOT NULL)::INT
    + (procedure_type <> 'Not reported')::INT
    + (cpv_code IS NOT NULL)::INT
    + (initial_duration_months IS NOT NULL)::INT
    + (price_variation IS NOT NULL)::INT
"""


def build_duplicates(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Create the table int_duplicates(uid, duplicate_group, canonical_uid,
    is_canonical, group_size, group_buyers, group_kind) and return it."""
    pairs = con.execute("SELECT uid_a, uid_b FROM int_duplicate_pairs").fetchall()
    membership = pd.DataFrame(list(group_pairs(pairs).items()), columns=["uid", "duplicate_group"])
    con.register("membership", membership)
    con.execute(
        f"""
        CREATE OR REPLACE TABLE int_duplicates AS
        WITH buyer_size AS (
            SELECT buyer_siret, count(*) AS buyer_contracts FROM int_contracts GROUP BY 1
        ),
        groups AS (
            SELECT
                m.uid,
                m.duplicate_group,
                {COMPLETENESS} AS completeness,
                c.publication_date,
                b.buyer_contracts,
                count(*) OVER (PARTITION BY m.duplicate_group) AS group_size,
                count(DISTINCT c.lead_holder_key) OVER (PARTITION BY m.duplicate_group)
                    AS group_holders,
                count(DISTINCT c.buyer_siret) OVER (PARTITION BY m.duplicate_group)
                    AS group_buyers,
                count(DISTINCT left(c.buyer_siret, 9)) OVER (PARTITION BY m.duplicate_group)
                    AS group_legal_units
            FROM membership AS m
            JOIN int_contracts AS c USING (uid)
            LEFT JOIN buyer_size AS b USING (buyer_siret)
        ),
        ranked AS (
            SELECT
                *,
                -- A joint purchase is kept under the buyer with the most
                -- contracts, most often the coordinator of the purchasing group.
                row_number() OVER (
                    PARTITION BY duplicate_group
                    ORDER BY
                        CASE WHEN group_legal_units > 1 THEN buyer_contracts END DESC NULLS LAST,
                        completeness DESC,
                        publication_date NULLS LAST,
                        uid
                ) AS rank_in_group
            FROM groups
        )
        SELECT
            uid,
            duplicate_group,
            first_value(uid) OVER (
                PARTITION BY duplicate_group ORDER BY rank_in_group
            ) AS canonical_uid,
            rank_in_group = 1 AS is_canonical,
            group_size,
            group_buyers,
            CASE
                WHEN group_legal_units > 1 THEN 'joint_purchase'
                WHEN group_holders > 1 THEN 'multi_supplier_framework'
                ELSE 'duplicate'
            END AS group_kind
        FROM ranked
        """
    )
    con.unregister("membership")
    return con.execute("SELECT * FROM int_duplicates").fetchdf()

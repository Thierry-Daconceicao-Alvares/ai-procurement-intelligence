"""Records that describe the same award are linked and merged."""

from __future__ import annotations

from datetime import date

from conftest import contract, load_contracts, run_sql

from procurement_intel.dedup import UnionFind, build_duplicates, group_pairs


def links(con) -> dict[tuple[str, str], str]:
    rows = con.execute("SELECT uid_a, uid_b, link FROM int_duplicate_pairs").fetchall()
    return {(a, b): link for a, b, link in rows}


def build(con, rows):
    load_contracts(con, rows)
    run_sql(con, "03_duplicate_candidates")
    return build_duplicates(con).set_index("uid")


def test_union_find_groups_chains():
    groups = group_pairs([("a", "b"), ("b", "c"), ("x", "y")])
    assert groups["a"] == groups["b"] == groups["c"] == "a"
    assert groups["x"] == groups["y"] == "x"
    finder = UnionFind()
    finder.union("z", "m")
    assert finder.find("z") == "m"


def test_duplicate_with_truncated_description(con):
    text = "Travaux de renovation de la toiture de l ecole primaire Jean Moulin et du gymnase"
    duplicates = build(
        con,
        [
            contract("a", description=text, offers_received=None),
            contract("b", description=text[:60]),
        ],
    )
    assert links(con) == {("a", "b"): "duplicate"}
    assert duplicates.loc["a", "group_kind"] == "duplicate"
    # The most complete record (offers reported) is kept.
    assert duplicates.loc["b", "is_canonical"] and not duplicates.loc["a", "is_canonical"]


def test_framework_published_once_per_supplier(con):
    rows = [
        contract(
            uid,
            lead_holder_key=holder,
            is_framework=True,
            contract_type="Supplies",
            description="Accord-cadre de fourniture de mobilier scolaire",
        )
        for uid, holder in (("a", "FR:111111111"), ("b", "FR:222222222"), ("c", "FR:333333333"))
    ]
    duplicates = build(con, rows)
    assert set(links(con).values()) == {"multi_supplier_framework"}
    assert (duplicates["group_kind"] == "multi_supplier_framework").all()
    assert duplicates["is_canonical"].sum() == 1


def test_lots_of_one_procedure_are_not_linked(con):
    build(
        con,
        [
            contract("a", description="Construction du groupe scolaire lot 1 gros oeuvre"),
            contract("b", description="Construction du groupe scolaire lot 2 gros oeuvre"),
        ],
    )
    assert links(con) == {}


def test_different_holders_outside_frameworks_are_not_linked(con):
    build(con, [contract("a"), contract("b", lead_holder_key="FR:999999999")])
    assert links(con) == {}


def test_joint_purchase_published_by_each_member(con):
    rows = [
        contract(
            "a",
            buyer_siret="21750001600019",
            initial_amount=600_000.0,
            description="Groupement de commandes telecommunications",
        ),
        contract(
            "b",
            buyer_siret="21930066700011",
            initial_amount=600_000.0,
            description="Services de communications electroniques lot unique",
        ),
        contract(
            "c",
            buyer_siret="25930001900015",
            initial_amount=600_000.0,
            description="Telephonie fixe et mobile",
        ),
    ]
    duplicates = build(con, rows)
    assert set(links(con).values()) == {"joint_purchase"}
    assert (duplicates["group_kind"] == "joint_purchase").all()
    assert (duplicates["group_buyers"] == 3).all()
    assert duplicates["is_canonical"].sum() == 1


def test_two_establishments_of_one_buyer_make_a_duplicate(con):
    duplicates = build(
        con,
        [
            contract("a", buyer_siret="21690123100011", description="Marche de voirie"),
            contract("b", buyer_siret="21690123100029", description="Entretien des routes"),
        ],
    )
    assert links(con) == {("a", "b"): "duplicate"}
    assert duplicates.loc["a", "group_kind"] == "duplicate"


def test_small_amounts_of_different_buyers_are_not_linked(con):
    build(
        con,
        [
            contract("a", buyer_siret="21750001600019", initial_amount=40_000.0),
            contract("b", buyer_siret="21930066700011", initial_amount=40_000.0),
        ],
    )
    assert links(con) == {}


def test_members_notifying_on_their_own_dates(con):
    rows = [
        contract(
            f"m{i}",
            buyer_siret=siret,
            initial_amount=8e9,
            cpv_code=cpv,
            notification_date=date(2023, month, day),
            description=text,
        )
        for i, (siret, cpv, month, day, text) in enumerate(
            [
                ("21910275300015", "09310000", 5, 25, "Fourniture d electricite 2025-2026"),
                ("21920024300018", "31682000", 7, 7, "Acheminement electricite"),
                ("25750004100047", "09310000", 7, 7, "Electricite pour les membres"),
                ("21940015700018", "65310000", 7, 20, "Lot 1 fourniture electricite"),
            ]
        )
    ]
    # The same amount and holder two years later is another award.
    rows.append(
        contract(
            "later",
            buyer_siret="21910044300064",
            initial_amount=8e9,
            notification_date=date(2025, 3, 19),
        )
    )
    # Coordinator: the buyer with the most contracts keeps the record.
    rows += [
        contract(
            f"other{i}",
            buyer_siret="25750004100047",
            initial_amount=1e5 + i,
            description=f"Marche numero {i}",
        )
        for i in range(5)
    ]
    duplicates = build(con, rows)
    members = duplicates.loc[[f"m{i}" for i in range(4)]]
    assert (members["group_kind"] == "joint_purchase").all()
    assert members["is_canonical"].sum() == 1
    assert members.loc["m2", "is_canonical"]
    assert "later" not in duplicates.index


def test_same_amount_and_holder_at_two_buyers_only_is_not_a_joint_purchase(con):
    build(
        con,
        [
            contract(
                "a",
                buyer_siret="21750001600019",
                initial_amount=1e7,
                notification_date=date(2023, 1, 3),
            ),
            contract(
                "b",
                buyer_siret="21930066700011",
                initial_amount=1e7,
                notification_date=date(2023, 1, 4),
                cpv_code="45233141",
            ),
        ],
    )
    assert links(con) == {}

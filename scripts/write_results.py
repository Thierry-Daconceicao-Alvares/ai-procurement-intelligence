"""Write docs/results.md from the metrics of reports/metrics/.

Every figure of the results page comes from a JSON file written by the build
(`python -m procurement_intel build`), the evaluation
(`python -m procurement_intel evaluate`) or the generation of the AI texts
(`scripts/generate_ai_texts.py`). Run this script after them:

    python scripts/write_results.py
"""

from __future__ import annotations

import json
from pathlib import Path

from procurement_intel.config import Settings
from procurement_intel.formatting import eur, pct

ISSUES = {
    "out_of_bounds": "Below €100, or €10bn and more",
    "far_from_expected": "Far from the expected amount (model, 5 robust standard deviations)",
    "placeholder": "Placeholder value (999,999,999...)",
    "default_ceiling": "Same large amount reused for unrelated purchases",
}
ERRORS = {
    "x10": "Amount × 10 (an extra zero)",
    "x100": "Amount × 100 (cents read as euros)",
    "x1000": "Amount × 1,000 (euros entered where thousands were expected)",
    "/100": "Amount ÷ 100 (euros read as cents)",
    "/1000": "Amount ÷ 1,000 (thousands entered where euros were expected)",
}


def billions(amount: float | None) -> str:
    return "n/a" if amount is None else f"€{amount / 1e9:,.1f}bn"


def load(name: str) -> dict:
    path = Settings.from_env().metrics_dir / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def table(header: list[str], rows: list[list[object]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return "\n".join(lines)


def data_quality(funnel: dict) -> str:
    rows = [
        ["Rows published (snapshot)", f"{funnel['raw_rows']:,}"],
        ["Contracts notified January 2019 - September 2026", f"{funnel['contracts_in_window']:,}"],
        ["Duplicate records removed", f"-{funnel['duplicate_records_removed']:,}"],
        ["Framework records merged (one per supplier)", f"-{funnel['framework_records_merged']:,}"],
        [
            "Joint purchase records merged (one per member)",
            f"-{funnel.get('joint_purchase_records_merged', 0):,}",
        ],
        ["**Contracts analysed**", f"**{funnel['contracts_kept']:,}**"],
        ["of which with a valid amount", f"{funnel['amounts_valid']:,}"],
        [
            "of which with an implausible amount (excluded from totals)",
            f"{funnel['amounts_implausible']:,}",
        ],
        ["of which without amount", f"{funnel['amounts_missing']:,}"],
    ]
    issues = [
        [ISSUES.get(issue, issue), f"{n:,}"]
        for issue, n in funnel.get("amounts_implausible_by_issue", {}).items()
    ]
    values = [
        [
            "Sum of every reported amount in the window",
            billions(funnel["reported_value_all_records_eur"]),
        ],
        [
            "of which implausible amounts of the contracts analysed",
            billions(funnel.get("reported_value_implausible_eur")),
        ],
        ["**Awarded value after cleaning**", f"**{billions(funnel['awarded_value_valid_eur'])}**"],
        [
            "of which outside framework agreements",
            billions(funnel["awarded_value_excluding_frameworks_eur"]),
        ],
    ]
    return "\n\n".join(
        [
            "## Data quality",
            table(["Step", "Records"], rows),
            "Implausible amounts by reason:",
            table(["Reason", "Contracts"], issues),
            table(["Value", "Euros"], values),
            f"{funnel['buyers']:,} buyers and {funnel['suppliers']:,} suppliers; the names of "
            f"{funnel['individual_entrepreneurs_masked']:,} individual entrepreneurs are withheld.",
        ]
    )


def amount_model(model: dict, injection: dict) -> str:
    parts = ["## Expected amounts"]
    if model:
        parts.append(
            table(
                ["Metric (log10 of the amount)", "Value"],
                [
                    ["Contracts scored (cross-fitted)", f"{model['contracts_scored']:,}"],
                    ["Contracts used for training", f"{model['contracts_trained']:,}"],
                    ["Mean absolute error", f"{model['mae_log10']:.3f}"],
                    [
                        "Median absolute error",
                        f"{model['median_abs_error_log10']:.3f} "
                        f"(factor {10 ** model['median_abs_error_log10']:.2f})",
                    ],
                    [
                        "Mean absolute error of the median (baseline)",
                        f"{model['mae_log10_median_baseline']:.3f}",
                    ],
                    ["R²", f"{model['r2_log10']:.3f}"],
                    [
                        "Implausible (z ≥ 5): above / below the expected amount",
                        f"{model['implausible_high']:,} / {model['implausible_low']:,}",
                    ],
                    ["Atypical (3.5 ≤ z < 5)", f"{model['atypical']:,}"],
                ],
            )
        )
    if injection:
        rows = []
        for kind, entry in injection["by_error"].items():
            rows.append(
                [
                    ERRORS.get(kind, kind),
                    f"{entry['contracts']:,}",
                    pct(entry["model"]["recall_implausible"], 1),
                    pct(entry["model"]["recall_review"], 1),
                    pct(entry["segment_benchmark"]["recall_implausible"], 1),
                    pct(entry["segment_benchmark"]["recall_review"], 1),
                ]
            )
        model_all, bench_all = injection["model"], injection["segment_benchmark"]
        rows.append(
            [
                "**All injected errors**",
                f"**{injection['sample']:,}**",
                f"**{pct(model_all['recall_implausible'], 1)}**",
                f"**{pct(model_all['recall_review'], 1)}**",
                f"**{pct(bench_all['recall_implausible'], 1)}**",
                f"**{pct(bench_all['recall_review'], 1)}**",
            ]
        )
        parts += [
            "### Injected errors",
            f"Errors injected into {injection['sample']:,} contracts ({injection['selection']}), "
            f"random seed {injection['seed']}; recall = share of the errors found.",
            table(
                [
                    "Error",
                    "Contracts",
                    "Model: implausible",
                    "Model: to review",
                    "Benchmark: implausible",
                    "Benchmark: to review",
                ],
                rows,
            ),
            table(
                ["Published contracts flagged (before injection)", "Model", "Benchmark"],
                [
                    [
                        "Implausible (z ≥ 5)",
                        pct(model_all["flag_rate_published_implausible"], 2),
                        pct(bench_all["flag_rate_published_implausible"], 2),
                    ],
                    [
                        "To review (z ≥ 3.5)",
                        pct(model_all["flag_rate_published_review"], 2),
                        pct(bench_all["flag_rate_published_review"], 2),
                    ],
                ],
            ),
            "The flag rate on the published data is the volume of alerts each method "
            "produces; it includes the real errors, so it bounds the false alarms from above.",
        ]
    return "\n\n".join(parts)


def classifier(metrics: dict) -> str:
    model, baseline = metrics.get("model", {}), metrics.get("baseline", {})
    if not model:
        return ""
    rows = [
        ["Accuracy", pct(model["accuracy"], 1), pct(baseline.get("accuracy"), 1)],
        ["Top-3 accuracy", pct(model["top3_accuracy"], 1), pct(baseline.get("top3_accuracy"), 1)],
        ["Macro F1", f"{model['macro_f1']:.3f}", f"{baseline.get('macro_f1', 0):.3f}"],
        ["Weighted F1", f"{model['weighted_f1']:.3f}", f"{baseline.get('weighted_f1', 0):.3f}"],
    ]
    confidence = [
        [f"≥ {c['min_probability']}", pct(c["coverage"], 1), pct(c["accuracy"], 1)]
        for c in model.get("by_confidence", [])
    ]
    parts = [
        "## Spend classification",
        f"Trained on {metrics['train_contracts']:,} descriptions of 2019-2024, tested on "
        f"{model['test_contracts']:,} descriptions of 2025-2026 never seen in training.",
        table(["Metric", "TF-IDF + linear model", "Naive Bayes baseline"], rows),
        table(["Model probability", "Share of test contracts", "Accuracy"], confidence),
    ]
    injected = model.get("wrong_codes_injected")
    if injected:
        parts.append(
            f"Wrong codes injected into {injected['sample']:,} test contracts "
            f"({injected['wrong_code']}): "
            f"the flag rule (another division at least 90% likely, the declared one at most 2%) "
            f"finds {pct(injected['recall'], 1)} of them, and flags "
            f"{pct(injected['flag_rate_declared_codes'], 1)} of the contracts with their declared "
            "code."
        )
        if injected.get("trade_off"):
            parts.append(
                table(
                    [
                        "Other division at least",
                        "Declared division at most",
                        "Wrong codes found",
                        "Declared codes flagged",
                    ],
                    [
                        [
                            pct(r["min_probability"]),
                            pct(r["max_declared_probability"]),
                            pct(r["recall"], 1),
                            pct(r["flag_rate_declared_codes"], 1),
                        ]
                        for r in injected["trade_off"]
                    ],
                )
            )
    parts.append(f"{metrics.get('flagged_contracts', 0):,} contracts are flagged for review.")
    return "\n\n".join(parts)


def alerts(metrics: dict) -> str:
    if not metrics:
        return ""
    rows = []
    for key, n in sorted(metrics["by_type"].items()):
        category, kind, severity = key.split(" / ")
        rows.append([category, kind.replace("_", " "), severity, f"{n:,}"])
    return "\n\n".join(
        [
            "## Alerts",
            f"{metrics['total']:,} alerts, ordered in the review queue by severity and amount at "
            "stake.",
            table(["Category", "Alert", "Severity", "Count"], rows),
        ]
    )


def segments(metrics: dict) -> str:
    rows = [
        [
            s["segment"],
            f"{s['suppliers']:,}",
            f"{s['median_contracts']:.0f}",
            f"{s['median_buyers']:.0f}",
            eur(s["median_value"]),
            pct(s["median_top_buyer_share"]),
        ]
        for s in metrics.get("supplier_segments", [])
    ]
    if not rows:
        return ""
    return "\n\n".join(
        [
            "## Supplier segments",
            table(
                [
                    "Segment",
                    "Suppliers",
                    "Median contracts",
                    "Median buyers",
                    "Median value",
                    "Median share of main buyer",
                ],
                rows,
            ),
        ]
    )


def ai_texts(metrics: dict) -> str:
    if not metrics:
        return "## AI explanations\n\nNot generated yet (workflow `ai-texts.yml`)."
    return "\n\n".join(
        [
            "## AI explanations",
            table(
                ["Metric", "Value"],
                [
                    ["Model", metrics["model"]],
                    ["Prompt version", metrics["prompt_version"]],
                    ["Generated", metrics["generated_at"]],
                    [
                        "Alerts explained",
                        f"{metrics['alerts_explained']} of {metrics['alerts_requested']} requested",
                    ],
                    [
                        "Texts with every figure found in the evidence",
                        f"{metrics['texts_with_all_figures_verified']} of {metrics['texts']}",
                    ],
                    [
                        "Median latency",
                        f"{metrics['latency_median_s']:.1f} s"
                        if metrics.get("latency_median_s")
                        else "n/a",
                    ],
                    [
                        "Tokens (prompt / output)",
                        f"{metrics['prompt_tokens']:,} / {metrics['output_tokens']:,}",
                    ],
                    [
                        "Cost at paid-tier list prices",
                        f"${metrics['estimated_cost_usd_paid_tier']}",
                    ],
                ],
            ),
        ]
    )


def main() -> None:
    settings = Settings.from_env()
    sections = [
        "# Results",
        "Generated by `scripts/write_results.py` from `reports/metrics/` (data snapshot of "
        f"{settings.snapshot_date:%d %B %Y}). Rerun the build, the evaluation and this script "
        "to reproduce them.",
        data_quality(load("data_quality")),
        amount_model(load("amount_model"), load("evaluation_amounts")),
        classifier(load("cpv_classifier")),
        alerts(load("alerts")),
        segments(load("segments")),
        ai_texts(load("ai_texts")),
    ]
    out = Path(__file__).resolve().parents[1] / "docs" / "results.md"
    out.write_text("\n\n".join(s for s in sections if s) + "\n", encoding="utf-8")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()

"""Generate the AI explanations shown in the public demo, and measure them.

Explains the alerts with the highest priority of each category and writes the
briefing of the national view, with the Gemini API. Every text is checked:
its figures must all be found in the evidence. The output is
data/marts/ai_texts.json (read by the application, so that visitors see
explanations without spending API quota) and reports/metrics/ai_texts.json
(verification rate, latency, tokens, estimated cost).

Usage: GEMINI_API_KEY=... python scripts/generate_ai_texts.py --per-category 40 --delay 4
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import UTC, datetime

from procurement_intel import analytics
from procurement_intel.alerts import CATEGORIES
from procurement_intel.config import Settings
from procurement_intel.explain import PROMPT_VERSION, alert_context, brief_view, explain_alert
from procurement_intel.llm import Gemini, LLMError, LLMQuotaExhausted

# Paid-tier list prices of gemini-3.5-flash-lite, US dollars per million tokens,
# checked on 18 October 2026 (the free tier is used here).
PRICE_INPUT, PRICE_OUTPUT = 0.30, 2.50


def national_facts(con) -> dict:
    filters = analytics.Filters()
    data = analytics.overview(con, filters)
    data.extras["concentration"] = analytics.supplier_concentration(con, filters)
    data.extras["alerts"] = analytics.alert_counts(con, filters)
    k = data.kpis
    top = data.by_division.head(5)
    return {
        "scope": "All French public buyers",
        "period": "2019 to 2026 (January to September)",
        "awarded_value": k["awarded_value"],
        "contracts": int(k["contracts"]),
        "buyers": int(k["buyers"]),
        "suppliers": int(k["suppliers"]),
        "framework_share_of_value": k["framework_value"] / k["awarded_value"],
        "top_categories": [
            {"label": r.label, "value": r.value, "share": r.share} for r in top.itertuples()
        ],
        "top10_supplier_share": data.extras["concentration"]["top10_share"],
        "sme_share": k["sme_share"],
        "single_bid_rate": k["single_bid_rate"],
        "non_competitive_share": k["non_competitive_share"],
        "alerts": [
            {"label": r.label, "count": int(r.count), "amount_at_stake": r.amount_at_stake}
            for r in data.extras["alerts"].head(6).itertuples()
        ],
        "value_by_year": {
            str(r.year): (r.contracts_value or 0) + (r.framework_value or 0)
            for r in data.by_year.itertuples()
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--per-category", type=int, default=40)
    parser.add_argument("--delay", type=float, default=4.0, help="seconds between calls")
    args = parser.parse_args()
    settings = Settings.from_env()
    if not settings.gemini_api_key:
        sys.exit("GEMINI_API_KEY is not set.")
    gemini = Gemini(settings.gemini_api_key, settings.generation_model)
    con = analytics.connect(settings.marts_dir)

    selected = []
    for category in CATEGORIES.values():
        frame = analytics.alerts(
            con, analytics.Filters(), categories=(category,), limit=args.per_category
        )
        selected.extend(frame.to_dict("records"))

    texts: dict = {
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "model": settings.generation_model,
        "prompt_version": PROMPT_VERSION,
        "alerts": {},
        "briefings": {},
    }
    failures: list[str] = []
    stopped = None
    try:
        result = brief_view(gemini, national_facts(con))
        texts["briefings"]["national"] = result.to_record()
        for index, alert in enumerate(selected, start=1):
            time.sleep(args.delay)
            context = alert_context(alert, analytics.alert_names(con, alert))
            try:
                result = explain_alert(gemini, context)
            except LLMQuotaExhausted:
                raise
            except LLMError as exc:
                failures.append(f"{alert['alert_id']}: {exc}")
                continue
            record = result.to_record()
            record["alert_type"] = alert["alert_type"]
            texts["alerts"][alert["alert_id"]] = record
            print(
                f"[{index}/{len(selected)}] {alert['alert_type']} "
                f"{'verified' if result.verified else 'UNVERIFIED ' + str(result.unverified)}",
                flush=True,
            )
    except LLMQuotaExhausted as exc:
        stopped = str(exc)
        print(f"Stopped: {exc}", flush=True)

    records = list(texts["alerts"].values()) + list(texts["briefings"].values())
    prompt_tokens = sum(r["prompt_tokens"] or 0 for r in records)
    output_tokens = sum(r["output_tokens"] or 0 for r in records)
    latencies = [r["latency_s"] for r in records if r["latency_s"] is not None]
    metrics = {
        "generated_at": texts["generated_at"],
        "model": settings.generation_model,
        "prompt_version": PROMPT_VERSION,
        "alerts_requested": len(selected),
        "alerts_explained": len(texts["alerts"]),
        "failures": failures,
        "stopped": stopped,
        "texts_with_all_figures_verified": sum(1 for r in records if not r["unverified"]),
        "texts": len(records),
        "unverified_examples": [r["unverified"] for r in records if r["unverified"]][:10],
        "latency_median_s": statistics.median(latencies) if latencies else None,
        "prompt_tokens": prompt_tokens,
        "output_tokens": output_tokens,
        "estimated_cost_usd_paid_tier": round(
            prompt_tokens / 1e6 * PRICE_INPUT + output_tokens / 1e6 * PRICE_OUTPUT, 4
        ),
    }
    (settings.marts_dir / "ai_texts.json").write_text(
        json.dumps(texts, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    settings.metrics_dir.mkdir(parents=True, exist_ok=True)
    (settings.metrics_dir / "ai_texts.json").write_text(
        json.dumps(metrics, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({k: v for k, v in metrics.items() if k != "failures"}, indent=2))
    return 1 if stopped or not texts["alerts"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

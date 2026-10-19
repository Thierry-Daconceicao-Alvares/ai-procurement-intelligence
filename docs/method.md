# Method

How the application turns 3.3 million published rows into a spend cube, risk
alerts and explanations that a reviewer validates. The figures quoted by the
README are in [results.md](results.md), generated from `reports/metrics/`.

## 1. Data

| Source | Producer | Licence | Use |
|---|---|---|---|
| Données essentielles de la commande publique, consolidated (Parquet) | Colin Maudry ([decp.info](https://decp.info)), from the data published by French public buyers | Licence Ouverte 2.0 | Contracts: buyer, holders, amount, CPV code, procedure, offers, modifications |
| Base Sirene, stock of legal units | INSEE | Licence Ouverte 2.0 | Supplier legal form, creation and cessation dates, headcount band |
| CPV 2008 code list (labels in English and French) | Publications Office of the European Union (eForms SDK) | CC BY 4.0 | Category hierarchy |
| Procurement thresholds | Code de la commande publique, Official Journal of the EU | Public law | Thresholds in force at each date (`reference/procurement_thresholds.csv`, with sources) |

The GitHub workflow `data-snapshot.yml` downloads the sources and freezes them in
`data/snapshot/` with a manifest (URLs, dates, SHA-256). Every result of the
project is computed from this snapshot. The Sirene extract keeps only the
contract holders and no name: names of companies come from the contract data,
and the names of individual entrepreneurs (natural persons) are withheld.

Contracts notified from January 2019 to September 2026 are analysed. The window
ends with the last complete quarter before the snapshot, because buyers publish
with a delay.

## 2. Data model

DuckDB runs the SQL models of `src/procurement_intel/sql/` in order; Python
steps (deduplication, models) run between them (`pipeline.py`).

| Model | Content |
|---|---|
| `01_staging` | One row per published record, typed and normalised: identifiers, amounts, CPV codes, procedures in English, offers, durations |
| `02_contracts` | One row per contract (`uid`), with its award attributes, holders and modifications |
| `03_duplicate_candidates` + `dedup.py` | Records that describe the same award, merged (section 3) |
| `04_amount_quality` + `amount_model.py` | Amount checks (section 4) |
| `05_marts` | Star schema: `fct_contracts`, `fct_contract_holders`, `dim_buyers`, `dim_suppliers`, `dim_cpv` |
| `06_alerts` + `alerts.py` | Risk indicators with their evidence (section 5) |

`export.py` writes the marts to `data/marts/` (Parquet): the only data the
application and the Power BI model read.

## 3. Records of one award

Published data counts many awards several times. Three cases are merged, and
the most complete record is kept:

- **Duplicates.** The same contract published by several platforms, or under
  two establishments of the same buyer: same buyer, amount, notification date,
  lot number and holder, and nearly the same description (at least 90% of the
  words of the shorter description found in the other one, and a Jaccard index
  of at least 0.6: platforms often truncate descriptions).
- **Framework agreements published once per supplier.** One multi-supplier
  framework agreement published as one record per supplier, each with the
  full maximum amount: same test, different holders, all framework agreements.
  The suppliers are attached to the record kept.
- **Joint purchases.** The members of a purchasing group (groupement de
  commandes) each publish the contract with the amount of the whole group:
  records of different buyers with the same amount (at least €100k),
  notification date, CPV code, lot and holder; or records of at least €10M
  with the same amount and holder, from at least three buyers, notified in a
  cluster without a gap of more than 60 days (members notify on their own
  dates). The record kept is the one of the buyer with the most contracts,
  most often the coordinator of the group.

Pairs are linked into groups with a union-find (if A matches B and B matches C,
the three are one award). The removed records stay visible as data-quality
findings, with the list of what was removed.

## 4. Amount checks

Amounts are as awarded, excluding VAT: the maximum for a framework agreement,
not what is spent. Three rules catch errors that need no model:

1. **Placeholder**: €10M or more, and at most €10 below a round amount with one
   significant digit (999,999,999; 99,999,999.99; 399,999,996 for four lots of
   99,999,999).
2. **Out of bounds**: below €100, or €10bn and more.
3. **Default ceiling**: one buyer reports the same amount of €10M or more for
   at least 5 contracts, in at least 3 CPV divisions and on 3 dates, and this
   amount is at least 100 times the 90th percentile of the buyer's other
   contracts. Large buyers reuse standard ceilings too (€20M, €100M), but
   these stay in the range of their large contracts.

Then a gradient-boosting model (LightGBM, `amount_model.py`) predicts the
logarithm of each amount from what is known about the contract: CPV division
and group, type, nature, procedure, price form and revision, framework
agreement or not, duration, number of offers and holders, year, buyer category
and region, holder size, the buyer's median and 90th-percentile amounts, its
number of contracts, and the length of the description.

- The loss is the absolute error: the model estimates a median, which the
  errors it must find barely move.
- Predictions are cross-fitted (3 folds): each contract is scored by a model
  that never saw it, so that an error cannot be learned and then hidden.
- Amounts that failed a rule are scored but neither used for training nor for
  the buyer's statistics: a buyer that enters 999,999,999 on half of its
  contracts must not make that amount look normal.
- The residual (log10 of reported / expected) is divided by a robust spread,
  1.4826 × the median absolute deviation (MAD), to give a z-score. The spread
  is estimated per buyer and contract type when the buyer has at least 30 such
  contracts, shrunk toward the spread of all buyers with a weight of 100
  contracts: a central purchasing body awards contracts of very different
  sizes, so a framework agreement of one billion euros is less surprising there
  than at a municipality. Below 30 contracts, a few errors would make their
  own spread, and the spread of all buyers applies.

An amount at 5 robust standard deviations or more is **implausible**: it is
excluded from totals and gets a data-quality alert. From 3.5, it stays in
totals and gets an **atypical amount** alert to review. A simpler benchmark,
the robust z-score of the amount within segments of comparable contracts (CPV
division, type, procedure group, framework agreement or not), is kept to
evaluate the model ([evaluation.md](evaluation.md)).

## 5. Alerts

Each alert is a risk indicator computed from the published data, with every
figure needed to explain it (its evidence). It is never a finding of
wrongdoing: most alerts have a legitimate explanation that only the buyer's
file can show.

| Category | Alert | Rule | Reference |
|---|---|---|---|
| Price and amount drift | Amount increased by modifications | Increase of more than 10% (supplies and services) or 15% (works); high from 50% | Code de la commande publique, art. R. 2194-3, R. 2194-5, R. 2194-8 |
| Price and amount drift | Amount far from comparable contracts | Model z-score from 3.5 to 5 | Amount model |
| Value leakage and competition | Single offer in a formal tender | One offer, formal competitive procedure, amount above the EU threshold in force | Single-bidder indicator of the EU Single Market Scoreboard |
| Value leakage and competition | High single-offer rate for a buyer | At least 20 procedures; binomial z-score of 3 or more against the rate expected from the categories the buyer purchases, and 15 points above it | Same |
| Value leakage and competition | Award without competition above the threshold | Procedure without publication or competition, amount above the threshold of art. R. 2122-8 in force (€25k, €40k from 2020, €60k for supplies and services from April 2026; €100k for works from December 2020) | Art. R. 2122-1 to R. 2122-11 |
| Value leakage and competition | Possible splitting | Same buyer, supplier and CPV group in one year, on at least two dates; each contract below the EU threshold, together above it (supplies and services, call-offs excluded) | Art. R. 2121-1 and following |
| Supplier risk | Dependency on one supplier | One supplier holds at least 60% of a buyer's spend in a CPV division (€1M or more, 3 contracts or more) | Herfindahl-Hirschman index |
| Supplier risk | Supplier activity unusual for the category | The CPV division bought accounts for less than 0.2% of the contracts won by companies of the supplier's NAF division (at least 500 contracts); award of €100k or more | Pairs of NAF and CPV divisions in the data |
| Supplier risk | Supplier ceased before the award | Ceased in Sirene more than 90 days before the notification | Base Sirene |
| Supplier risk | Newly created supplier | Company created less than 180 days before an award of €500k or more | Base Sirene |
| Spend classification | CPV code probably wrong | Declared division at most 2% likely according to the description, another one at least 90% | Text classifier |
| Data quality | Implausible reported amount | One of the amount checks of section 4 | Amount checks and model |
| Data quality | Contract declared several times / framework agreement published per supplier / joint purchase published by each member | Section 3 | Deduplication rules |

Thresholds come from `reference/procurement_thresholds.csv`, by date of
notification. The law applies them from the launch of the consultation, which
the data does not give: the notification date is an approximation.

**Priority.** The review queue is ordered by severity points (high 4, medium 2,
low 0) plus the order of magnitude of the amount at stake: a high-severity
alert on €1M scores 4 + 6 = 10. Records merged as one award are findings on
the published data, not risks: their severity is at most medium.

## 6. Suppliers and markets

- **Supplier segments.** Companies with at least three contracts (individual
  entrepreneurs excluded) are grouped by k-means (6 clusters) on standardised
  features of their public business: value, value per contract, contracts,
  buyers, CPV divisions, regions, distance to their buyers and share of their
  main buyer. Segments are named from the
  features that distinguish their centroid (national multi-category
  suppliers, local specialists...), so the names follow the data.
- **Kraljic matrix of public markets.** Each CPV group with at least 30
  competitive procedures reporting their offers is placed by value at stake
  (percentile of awarded value among groups) and supply risk (mean of three
  percentiles: supplier concentration (HHI), share of single-offer procedures,
  and scarcity of offers, the inverse of the average number of offers).
  Quadrants split at the medians: strategic, bottleneck, leverage and
  non-critical.

## 7. Spend classification

A text classifier predicts the CPV division (45 divisions) from the contract
description: TF-IDF of words and word pairs (200,000 features, sublinear term
frequency) and a linear model trained by stochastic gradient descent on the
logistic loss. A complement naive Bayes model is the baseline.

- Evaluation on time: trained on 2019-2024, tested on 2025-2026, without any
  test description that also appears in the training years (descriptions are
  often reused).
- Every contract gets an out-of-sample prediction: the 2025-2026 contracts
  from the model trained on 2019-2024, the earlier ones from a model trained
  on 2025-2026.
- A contract is flagged when its declared division gets at most 2% and another
  division at least 90%. The application classifies new descriptions with a
  compact model (60,000 features) trained on every contract.

## 8. Explanations by a language model

The language model (Gemini, `gemini-3.5-flash-lite` by default) never decides
anything. It receives the alert as JSON (title, evidence, names, recommended
action, legal reference) and returns a structured explanation: a summary, why
it matters, what to check and the legitimate explanations. The system prompt
asks it to use only the facts of the JSON, to never suggest wrongdoing, and to
treat the description of the contract as data, never as instructions.

Every figure of the text is then checked against the evidence (`verify.py`):
amounts within 5% of a value of the evidence (a written amount is rounded),
percentages within one point, other numbers within 1% (small counts such as
"two checks" are accepted); legal references, CPV and NAF codes are not
figures. A text with a figure that is not in the evidence is
shown with a warning that names the figure. Without a model (no API key, or
the quota used up), explanations are written from templates.

The briefing of a view works the same way, from the facts of the view (spend,
categories, suppliers, competition, alerts).

For the public demo, the explanations of the alerts with the highest priority
and the national briefing are generated in advance by the workflow
`ai-texts.yml`, with their verification status, so that visitors read them
without spending API quota.

## 9. Decisions

The recommendation a reviewer validates is the deterministic action of the
alert catalogue, which the reviewer can edit. Each decision (action needed,
no action because explained, data error, escalated) is appended to a SQLite
log with its reason, the reviewer, the recommendation as accepted, the source
of the explanation the reviewer saw (template or model, prompt version) and
the SHA-256 of the evidence at the time of the decision. Decisions are never
overwritten, and the log can be exported as CSV. In the public demo, each
visitor's log lives in memory and disappears with the session.

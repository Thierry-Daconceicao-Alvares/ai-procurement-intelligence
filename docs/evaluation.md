# Evaluation

Published procurement data has no labels: nobody says which amounts are
wrong, which codes are misclassified or which alerts are real problems. Each
component is therefore evaluated in the way its output can be checked. The
figures are in [results.md](results.md), generated from `reports/metrics/`.

## Expected amounts: errors injected into real contracts

`python -m procurement_intel evaluate` (`evaluation.amount_injection`):

1. Choose 20,000 contracts (random seed 7) among those that pass the amount
   rules and that both the model and the benchmark find typical (robust
   z-score below 3.5).
2. Inject the errors seen in the published data, one per contract, at random:
   amount × 10 (an extra zero), × 100 (cents read as euros), × 1,000 (euros
   entered where thousands were expected), ÷ 100 (euros read as cents), and
   ÷ 1,000 (thousands entered where euros were expected).
3. Rerun the whole detection on the 1.4 million contracts, with the errors in
   place: the out-of-bounds rule, the buyer statistics, the cross-fitted model
   and the robust z-scores (the errors can influence the model and the
   statistics, as real errors do).
4. Measure the share of injected errors found (recall), at the implausible
   threshold (z ≥ 5, excluded from totals) and at the review threshold
   (z ≥ 3.5).
5. Compare with the volume of alerts each method produces on the published
   data: the share of contracts it flags. It includes the real errors, so it
   bounds the false alarms from above; a method that finds more injected errors
   while flagging fewer contracts is better on both counts.

The benchmark is the robust z-score of the amount within segments of
comparable contracts (CPV division, type, procedure group, framework
agreement or not), the usual first approach to outliers.

What this does not measure: errors that keep an amount plausible (a wrong
amount of the right order of magnitude), and errors that are not
multiplicative (a placeholder is found by its own rule).

## Spend classification: a test set in the future, and wrong codes injected

`build_classifier` (run by the build) trains on the descriptions of 2019-2024
and tests on those of 2025-2026, removing any test description already seen
in training, since the same wording is often reused. It reports accuracy,
top-3 accuracy, macro and weighted F1, and accuracy by level of confidence,
against a naive Bayes baseline.

The flag rule is then tested on wrong codes: 20,000 test contracts get another
CPV division, drawn from the distribution of declared divisions, and the rule
(another division at least 90% likely, the declared one at most 2%) is applied.
The share of wrong codes found, and the share of contracts flagged with their
declared code, show what the rule catches and how much it disturbs. Declared
codes are themselves noisy: some of the contracts flagged with their declared
code really are misclassified.

## Deduplication: tests on constructed cases

The rules that merge the records of one award are tested on constructed cases
(`tests/test_dedup.py`): a truncated description, a framework agreement
published per supplier, the lots of one procedure (never merged), a joint
purchase published by its members on the same day or on their own dates,
two establishments of one buyer, small amounts of different buyers (never
merged), and the choice of the record kept.

## Explanations by the language model: every figure checked

Each explanation generated for the public demo is checked figure by figure
against the evidence of its alert (`verify.py`). `reports/metrics/ai_texts.json`
records the share of texts whose figures were all found, the median latency,
the tokens used and the cost at list prices. The check covers figures, not
reasoning: the texts are short, structured, and always shown next to the
evidence they explain.

## Alerts: what a reviewer decides

An alert is a risk indicator; whether it is a real problem depends on the
buyer's file. The decision log records, for each alert reviewed, the decision
(action needed, no action because explained, data error, escalated) and its
reason. The share of alerts confirmed per alert type is the measure of
precision that the published data cannot give, and the input for tuning the
thresholds.

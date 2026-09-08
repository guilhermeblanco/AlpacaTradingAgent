# Experiment promotion gates

Model, prompt, analyst, and risk-policy changes should first run as named
challenger experiments. Compare a challenger with the current champion using
outcomes from one fixed horizon, then call `assess_promotion` with an explicit
`PromotionPolicy`.

The default policy requires at least 30 outcomes from each experiment, a 50%
directional hit rate, positive mean excess return, positive one-sided 95% lower
bounds for both challenger excess return and uplift over the champion, and mean
estimated costs no greater than 0.5%.

The result is `insufficient_data`, `rejected`, or `eligible`, with scorecards
and reasons. Eligibility is evidence for human or deployment-controller review;
it does not mutate model configuration or bypass paper/shadow validation.
Thresholds are operational defaults rather than a claim of statistical proof.
Evaluate multiple horizons separately and account for multiple comparisons when
many challengers are tested.

## Autonomous cohort assignment

The headless worker assigns each `symbol:UTC-date` unit to one weighted variant
using SHA-256 and `AUTONOMOUS_EXPERIMENT_SEED`. Assignment is stable across
restarts and independent of process order. At most one variant may have
`execution_eligible=true`; every other variant is shadow-only.

Shadow decisions never enter portfolio reservations or the broker execution
pipeline. Instead, they create a point-in-time evaluation episode using asset
and benchmark observations at or before the decision timestamp. Champion
intents carry their experiment ID into the execution plan, so fill-based
episodes produced by reconciliation use the assigned cohort rather than a
worker-wide label.

Configure variants with `AUTONOMOUS_EXPERIMENTS_JSON`. `config_overrides` is
applied only to that variant's graph instance and can select challenger models,
prompts, or analysis parameters. Promotion remains a separate human-reviewed
operation; assignment never changes which variant is execution eligible.

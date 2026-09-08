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

# Refactor Difficulty vs Payoff (No Human-Grounding Plan)

## Scope and assumptions
- This analysis is based on the current `RSS_Feeds` + `NewsLens` pipeline behavior.
- Constraint: no human scoring will be used for calibration or ground truth.
- Difficulty estimates assume one primary engineer, incremental rollout, and preserving current production behavior.

## Legend
- Difficulty: `Low`, `Medium`, `High`, `Very High`
- Payoff: `Low`, `Medium`, `High`, `Very High`
- Effort estimate: approximate engineering time for first production-ready version

## Refactor matrix
| Refactor | Difficulty | Effort | Payoff | Why it matters |
|---|---|---:|---|---|
| 1) Replace human calibration with automated calibration proxies | Medium | 1-2 weeks | High | Gives you a quality gate without human labels by checking stability, invariance, and cross-model agreement. |
| 2) Separate extraction from scoring | High | 2-4 weeks | Very High | Reduces prompt-coupled noise and makes evidence first-class, improving reliability and auditability. |
| 3) Add explicit topic control in source comparisons | Medium | 1-2 weeks | Very High | Removes the largest confound in source differentiation (topic mix vs editorial behavior). |
| 4) Add event clustering (non-IID correction) | High | 2-4 weeks | Very High | Shifts claims from "outlets differ overall" to "outlets differ on the same events." |
| 5) Add latent space stability checks (bootstrap PCA/MDS) | Medium | 1-2 weeks | High | Prevents over-interpreting unstable axes in PCA/MDS outputs. |
| 6) Add multiple testing correction (FDR/BH) | Low | 1-3 days | High | Quickly reduces false positives in lens-level significance reporting. |
| 7) Reframe normalization (empirical standardization-first) | Medium | 1 week | Medium-High | Makes lens geometry less dependent on rubric max assumptions. |
| 8) Add drift diagnostics and alarms | Medium | 1-2 weeks | High | Protects against mistaking model/prompt drift for media drift. |
| 9) Enforce interpretive boundary conditions in product UI | Low | 2-4 days | Medium-High | Prevents misuse and keeps claims aligned with actual method strength. |
| 10) Expand audit drilldown and disagreement surfacing | Medium | 1-2 weeks | High | Converts outputs from black-box aggregates into inspectable analytic evidence chains. |

## Item-by-item analysis

### 1) Automated calibration proxies (no human labels)
Status: recommended replacement for the original human-calibration proposal.

Implementation:
- Add score stability tests on fixed article subsets across repeated runs.
- Add prompt-invariance tests with semantically equivalent prompt templates.
- Add cross-model agreement checks on a fixed canary set.
- Add synthetic contrast pairs where expected direction is known (for example, stronger conflict wording should increase conflict-oriented lens scores).

Difficulty drivers:
- Building useful proxy test sets and thresholds.
- Avoiding noisy alerting from normal variance.

Payoff:
- High. You get a practical reliability floor with no human-rater dependency.
- Limitation: this validates consistency and directional behavior, not full construct truth.

### 2) Extraction -> scoring separation
Implementation:
- Stage A: extract structured signals (claims, actors, evidentiary markers, tone indicators) into a schema.
- Stage B: score rubrics from that structured layer rather than raw article text.
- Persist extraction artifacts and version them alongside score records.

Difficulty drivers:
- Schema design and migration path.
- Backward compatibility with current scoring outputs.
- Additional compute and orchestration complexity.

Payoff:
- Very high. Most likely single biggest quality and interpretability upgrade.

### 3) Topic-controlled source analysis
Implementation:
- Add stratified source comparisons inside topic slices.
- Report both:
  - within-topic source differences
  - pooled source differences (explicitly marked confounded)
- Optional next step: regression residualization (`lens_score ~ topic + source`) and source analysis on residuals.

Difficulty drivers:
- Topic taxonomy quality and sparse cells.
- Reporting clarity when some topic/source combinations are thin.

Payoff:
- Very high. This directly addresses your largest statistical confound.

### 4) Event clustering and same-event comparisons
Implementation:
- Cluster articles into events using embeddings + time windows + entity overlap.
- Add event identifiers to records.
- Compute variance decomposition:
  - intra-event variance
  - between-source variance within event

Difficulty drivers:
- Cluster quality tuning and validation.
- Operational cost of embedding/clustering at scale.

Payoff:
- Very high. Major credibility improvement in comparative claims.

### 5) Latent space stability
Implementation:
- Bootstrap article rows and recompute PCA/MDS repeatedly.
- Track loading confidence intervals and sign/ordering stability.
- Flag unstable components in outputs and UI.

Difficulty drivers:
- Compute cost and caching.
- Summarizing stability without overwhelming users.

Payoff:
- High. Makes latent-space interpretation defensible.

### 6) Multiple-testing correction
Implementation:
- Apply Benjamini-Hochberg correction to lens-level p-values.
- Output both raw and adjusted p-values.
- Update dashboards/CSV fields accordingly.

Difficulty drivers:
- Mostly minimal integration work.

Payoff:
- High for low cost. Immediate reduction in false discovery risk.

### 7) Normalization strategy update
Implementation:
- Treat max-normalization as transport/prep only.
- Standardize empirically at analysis time (z-score by lens, optionally robust scaling).
- Keep both views available for compatibility.

Difficulty drivers:
- Comparability with historical reports.
- Explaining basis changes to stakeholders.

Payoff:
- Medium-High. Improves geometric validity of multivariate analyses.

### 8) Drift diagnostics
Implementation:
- Track per-lens distribution summaries by time window and topic.
- Add divergence metrics (for example JS/KL-style shift or Wasserstein distance).
- Add alerts for abrupt changes after model/prompt/config changes.

Difficulty drivers:
- Choosing practical thresholds.
- Separating natural news-cycle movement from methodological drift.

Payoff:
- High. Essential for longitudinal trust.

### 9) Interpretive guardrails in outputs
Implementation:
- Add explicit claim boundaries in dashboard text and report footers.
- Mark sections as descriptive/exploratory vs inferential.
- Show confound warnings when pooled results are viewed.

Difficulty drivers:
- Product wording and consistency across surfaces.

Payoff:
- Medium-High. Reduces misuse risk and improves methodological integrity.

### 10) Deeper audit drilldown
Implementation:
- Enable aggregate -> source -> article -> rubric -> evidence drilldown paths everywhere.
- Add "disagreement/borderline" views (for unstable or high-uncertainty cases).
- Surface prompt/version metadata for traceability.

Difficulty drivers:
- UI plumbing and performance.
- Designing useful disagreement heuristics.

Payoff:
- High. Strong differentiation and trust lever for the project.

## Recommended order (adjusted)
1. Add multiple-testing correction (`#6`) because it is fast and high leverage.
2. Add topic-controlled analysis (`#3`) to remove the biggest confound.
3. Add automated calibration proxies (`#1`) for non-human reliability gates.
4. Separate extraction from scoring (`#2`) for major measurement-quality gains.
5. Add event clustering (`#4`) to upgrade causal interpretability.
6. Add latent stability checks (`#5`) before deeper geometric claims.
7. Add drift diagnostics (`#8`) for safe long-run operation.
8. Expand audit drilldowns (`#10`) and interpretive guardrails (`#9`).
9. Normalize strategy refinements (`#7`) as a compatibility-managed migration.

## Suggested phased rollout
### Phase 1 (1-2 weeks)
- `#6` FDR correction
- `#3` topic-stratified reporting
- `#9` boundary-condition messaging

### Phase 2 (2-4 weeks)
- `#1` automated calibration proxies
- `#5` PCA/MDS stability bootstrap
- `#8` drift diagnostics baseline

### Phase 3 (4-8 weeks)
- `#2` extraction/scoring separation
- `#4` event clustering
- `#10` deep audit drilldowns
- `#7` normalization migration hardening

## Bottom line
- Highest payoff per unit effort: `#6`, `#3`, `#1`.
- Highest absolute payoff (but heavier): `#2`, `#4`.
- Must-have before strong interpretive claims: `#3`, `#5`, `#6`, `#8`.

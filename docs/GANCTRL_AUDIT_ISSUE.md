# Issue: Evaluate GanCtrl as an experimental virtual-control audit

**Status:** Proposed

**Proposed future branch:** `experiment/ganctrl-audit`

## Summary

Evaluate GanCtrl inside OpenScientist as an experimental virtual-control and
model-auditing capability. The first implementation should audit the authors'
published held-out synthetic controls. It must not train GanCtrl, run prospective
inference, or present synthetic controls as a trusted replacement for concurrent
controls.

GanCtrl reconstructs a study-aligned untreated state from time-matched treated
animals rather than selecting controls only from historical studies. In the
published Open TG-GATEs proof of concept, synthetic profiles were close to real
controls on multivariate similarity metrics. The more decision-relevant result
was an average treatment-effect-call concordance of 0.70 across 14 clinical
pathology endpoints, compared with 0.69 for a same-laboratory historical VCG and
0.61 for a laboratory-relaxed VCG. Endpoint-level performance and false-positive
versus false-negative behavior varied.

## Scientific boundary

- Treat every GanCtrl output as model-generated evidence, never an observation.
- Do not recommend replacing or reducing concurrent controls from this
  experiment.
- Do not optimize model parameters, endpoint thresholds, or audit rules against
  the held-out test set.
- Keep similarity, endpoint fidelity, and decision concordance separate. High
  cosine similarity does not establish endpoint- or decision-level equivalence.
- Require external validation and a defined context of use before any trusted or
  prospective role is considered.
- Report performance by endpoint and compound, including false positives, false
  negatives, uncertainty, and failure cases; do not rely on a panel average.

## First experiment

Create `skills/domain/ganctrl-audit/SKILL.md` containing the audit procedure and
directly usable Python recipes. At job time, the agent should adapt those recipes
to the uploaded files and explicitly call `execute_code`. Enabling the skill must
not be described as executing fenced code automatically.

Use the released held-out artifacts:

- `generated_predictions_merged_test.csv`
- `repeat_test_control_2d.csv`
- `repeat_test_treatment_2d.csv`

This is a multi-file analysis. The skill must discover files through
`data_files` and use the provided in-container paths instead of guessed,
repository-relative, or host paths.

## Required audit

1. Inventory the three inputs, schemas, missingness, duplicates, identifiers,
   units, and endpoint columns.
2. Validate the join between generated `targetId` values and observed-control
   `ID` values, including compound and timepoint keys.
3. Report generated-prediction multiplicity per observed target control and
   aggregate repeated predictions to one synthetic vector per target for the
   primary target-aligned analysis.
4. Report endpoint-level bias, median error, MAE, RMSE, MAE/RMSE standardized by
   observed-control spread, Pearson and Spearman correlations, variance ratios,
   and observed-range violations.
5. Treat panel-level cosine similarity and standardized aggregate errors as
   secondary summaries.
6. Estimate uncertainty by resampling compounds rather than generated rows.
7. Reproduce the published treatment-effect-call comparison where the released
   data and documented thresholding contract permit it. Keep thresholds fixed
   before evaluating the held-out set.
8. Audit whether conditioning fields such as target identity, target body
   weight, replicate identity, or study clusters would be available in a
   genuinely control-free prospective study.
9. Generate endpoint-error and target-similarity diagnostics and synthesize a
   Markdown report that separates observed data, generated data, computed
   results, interpretation, and unresolved uncertainty.

## Acceptance criteria

- [ ] The skill activates for GanCtrl synthetic-control audit requests and not
      for model training or generic historical-control selection.
- [ ] The three released held-out files are loaded exclusively through
      `data_files`, with explicit role and schema validation.
- [ ] Target alignment is fail-closed: unmatched or non-unique keys block the
      primary comparison and are reported.
- [ ] Repeated predictions are characterized and are not treated as independent
      observed controls.
- [ ] Endpoint-level metrics and compound-level uncertainty are reported before
      aggregate similarity summaries.
- [ ] Decision concordance, false positives, and false negatives are reported by
      endpoint where reproducible from the published contract.
- [ ] No held-out thresholds or model parameters are tuned during the audit.
- [ ] Potential prospective information leakage is listed explicitly.
- [ ] The report labels GanCtrl experimental and does not support replacing
      concurrent controls.
- [ ] The recipe completes within the current one-shot Python execution limit,
      or stops with enough evidence to design a deterministic dedicated audit
      tool.
- [ ] Normal, incomplete-input, schema-mismatch, duplicate-key, unmatched-key,
      and near-miss skill-selection cases are tested.

## Out of scope

- Training or fine-tuning the CVAE-GAN.
- Running the released TensorFlow inference pipeline.
- Creating a production `generate_ganctrl_controls` MCP tool or inference
  container.
- Making regulatory, study-design, animal-reduction, or concurrent-control
  replacement decisions.
- Generalizing beyond the published male Sprague-Dawley rat, high-dose,
  Open TG-GATEs clinical-pathology setting.

## Follow-up decision

After the skill-only audit, review scientific fidelity, execution time,
reproducibility, artifact needs, and failure behavior. If the method warrants
continued work, open separate issues for:

1. a deterministic `audit_virtual_controls` MCP tool with a fixed input/output
   contract and downloadable result artifacts; and
2. a dedicated, version-pinned GanCtrl inference container and
   `generate_ganctrl_controls` MCP tool.

Neither follow-up should inherit a claim that synthetic controls can replace
concurrent controls; that decision requires a separately governed validation
program and context of use.

## Sources

- [GanCtrl paper in *Toxicological Sciences*](https://doi.org/10.1093/toxsci/kfag099)
- [GanCtrl source repository](https://github.com/CHANDMX20/GanCtrl)
- [Published preprocessed GanCtrl inputs](https://doi.org/10.5281/zenodo.17883691)


---
name: preclinical-power-statistics
description: Design and audit power, group allocation, sample-size, and statistical analysis plans for mouse and rat studies. Use when defining estimands, experimental units, outcome-specific models, multiplicity, missing-data handling, sensitivity analyses, or reproducible calculations; do not use for automatic test selection from a file format or for replacing specialist statistical review in complex designs.
metadata:
  category: domain
  slug: preclinical-power-statistics
  tags:
    - power
    - sample-size
    - statistics
    - mice
    - rats
    - experimental-design
---

# Preclinical power and statistics

## Scientific role

Produce a design-specific statistical analysis and sample-size plan that uses the correct experimental unit and exposes every assumption. Optimize scientific information and animal use together; do not maximize power by treating non-independent observations as independent animals.

This skill is authoritative for estimands, power and precision calculations, statistical models, multiplicity, and analysis reproducibility. `preclinical-experimental-design` remains authoritative for biological procedures and welfare. `preclinical-preregistration` owns the registered record.

## Authoritative sources

Check current versions and cite the relevant section:

- ARRIVE 2.0 Essential 10, especially study design, sample size, inclusion and exclusion criteria, randomisation, blinding, outcome measures, and statistical methods: https://arriveguidelines.org/arrive-guidelines
- NC3Rs Experimental Design Assistant and design guidance: https://eda.nc3rs.org.uk/about and https://nc3rs.org.uk/3rs-resources/key-elements-well-designed-experiment
- PREPARE topic 4, experimental design and statistical analysis: https://norecopa.no/prepare/prepare-checklist/
- Directive 2010/63/EU Article 4 on reduction without compromising objectives: https://eur-lex.europa.eu/eli/dir/2010/63/2019-06-26/eng

The NC3Rs EDA supports design and does not replace specialist statistical advice. Escalate unfamiliar adaptive, survival, multilevel, longitudinal, multivariate, high-dimensional, pharmacokinetic, or Bayesian designs to a qualified statistician.

## Required design facts

Do not calculate until these are explicit or marked unresolved:

- scientific question, confirmatory or exploratory intent, and decision consequence;
- treatment contrast and estimand: population, outcome, treatment conditions, summary measure, and handling of intercurrent events when relevant;
- assignment, experimental, observational, and analysis units;
- group structure, controls, blocking or stratification, batches, litters, cages, repeated measures, and planned covariates;
- primary outcome definition, scale, time point or summary window, and expected distribution;
- effect size justified by biological relevance, not merely a prior point estimate;
- variance, correlation, event-rate, or distribution assumptions with provenance;
- type-I error or interval criterion, target power or precision, directionality, multiplicity, attrition, exclusions, and feasibility constraints.

Never silently default to 80% power, 0.05 alpha, equal allocation, a two-sided test, a normal outcome, or an effect size copied from an underpowered study. Offer conventional values only as labelled options and require the scientist to choose.

## Analysis design

1. Draw the unit hierarchy and intervention timing before naming a statistical test.
2. Define the primary estimand and one primary analysis that answers it.
3. Choose a model from the outcome distribution and dependency structure, not from whether a variable is called continuous or categorical.
4. Plan diagnostics, effect estimates with uncertainty intervals, and model-failure alternatives.
5. Define exclusions before outcome access, including who applies them while blinded where feasible.
6. Define missing-data prevention, missingness summaries, primary handling assumptions, and sensitivity analyses.
7. Control or clearly label multiplicity across outcomes, time points, doses, subgroups, and interim looks.
8. Specify randomisation, allocation concealment, blinding, unblinding, and analysis-set rules.

Account explicitly for cage, litter, batch, operator, cohort, and repeated-animal dependencies. Technical replicates generally improve measurement precision; they do not automatically increase biological sample size.

## Sample-size and power calculations

Use a deterministic statistical package or transparent code for exact calculations. Report package, version, function or model, parameters, rounding rule, and whether the result is animals, experimental units, or measurements.

For each calculation:

- derive assumptions from a justified minimum relevant effect, suitable pilot or external data, or a bounded sensitivity range;
- account for allocation ratio, design effect, repeated measures, clustering, blocking, multiplicity, and attrition where applicable;
- separate the analyzable requirement from animals enrolled and any non-experimental animals;
- show sensitivity across plausible effect and variance assumptions;
- verify with a second implementation, closed-form result, simulation benchmark, or independent statistician review;
- set and report random seeds for simulations.

Do not use observed post-study power to interpret a completed experiment. Report effect estimates, uncertainty, model assumptions, and design limitations instead.

## Evidence and verification

Label each input `recorded`, `computed`, `inferred`, `unknown`, or `conflicting`. Every numerical assumption needs a source or scientist confirmation. Then run:

1. a unit check: allocation, experimental, and analysis units align;
2. a numerical check: independent reproduction or benchmark agrees within stated tolerance;
3. a design check: the power model and primary analysis represent the same estimand and dependency structure;
4. a contradiction check against preregistration, ARRIVE, and PREPARE artifacts.

If any check fails, preserve both results, explain the discrepancy, and do not select the more convenient value without review.

## Export-ready output

Return a decision summary, assumptions table, sensitivity table, and one JSON object:

```json
{
  "schema_version": "openscientist-preclinical-statistics/0.1",
  "study_id": null,
  "scope": {
    "species": [],
    "jurisdiction": "EU Directive 2010/63/EU"
  },
  "question_and_estimand": {},
  "unit_hierarchy": {},
  "design": {},
  "outcomes": [],
  "primary_analysis": {},
  "secondary_and_sensitivity_analyses": [],
  "multiplicity": {},
  "missing_data_and_exclusions": {},
  "sample_size": {
    "method": null,
    "assumptions": [],
    "result": null,
    "unit": null,
    "attrition_adjustment": null,
    "sensitivity_results": [],
    "independent_verification": {}
  },
  "randomisation_and_blinding": {},
  "software_and_reproducibility": {},
  "source_register": [],
  "unresolved_items": [],
  "contradictions": [],
  "human_confirmations_required": []
}
```

Do not generate DOCX or PDF in this version. Do not present a numeric sample size as final until the scientist confirms the estimand, assumptions, experimental unit, welfare and feasibility constraints, and planned analysis.

## Stop conditions

Stop before a final recommendation when the experimental unit is ambiguous; outcome distribution or dependency structure is unsupported; the effect size lacks a scientific rationale; model and power calculation target different estimands; attrition would be double-counted; multiplicity is unresolved; results cannot be reproduced; or the requested analysis encourages pseudoreplication, selective exclusion, or outcome switching.

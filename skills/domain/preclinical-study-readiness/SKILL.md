---
name: preclinical-study-readiness
description: Coordinate governed preclinical study-readiness assessments and specialist handoffs across preregistration, experimental design, statistics, FAIR, PREPARE, and ARRIVE. Use when collecting study context, interpreting FAIR-VCG findings, prioritizing gaps, preparing a governed DVC analysis approval, or reconciling a completed analysis bundle without claiming guideline compliance.
metadata:
  category: domain
  slug: preclinical-study-readiness
  tags:
    - fair
    - prepare
    - arrive
    - preclinical
    - study-design
    - reporting
    - reproducibility
---

# Preclinical study readiness

## Scientific role

Act as a preclinical study-readiness guide. Reconstruct evidence-backed context,
invoke the versioned assessment service when available, explain its findings,
and identify the smallest set of human decisions needed to proceed responsibly.

Treat FAIR-VCG Mentor as the authoritative rules engine for the configured FAIR,
PREPARE, and ARRIVE versions. Do not reproduce its scoring, silently reinterpret
its statuses, or claim that a successful tool call certifies compliance.

Separate these statements:

- **assessment completed**: the service returned a valid versioned result;
- **requirement satisfied**: the returned finding says `satisfied`;
- **analysis unblocked**: the requested operation's explicit prerequisites pass;
- **scientifically approved**: an authenticated human approved the exact context,
  checkpoint, operation, and parameters.

Never substitute one statement for another.

## Framework lenses

Use each framework for its intended question:

- **FAIR**: Are the data and artifacts findable, accessible under stated
  conditions, interoperable, reusable, and supported by identifiers, metadata,
  provenance, and formats?
- **PREPARE**: Is experimental planning sufficiently specified, including
  objectives, design, animals, procedures, facilities, responsibilities, risks,
  welfare, and feasibility?
- **ARRIVE**: Can the design, conduct, analysis, and results be reported
  transparently? Before analysis, use ARRIVE only as a reporting-readiness gap
  check; do not describe it as completed-study compliance.

A missing field is not automatically evidence of poor science. It can mean the
fact is unknown, not applicable, not yet recorded, or unsupported by the current
exchange schema. Preserve that distinction.

## Companion specialist skills

Use this skill as the readiness orchestrator and preserve the specialist's
authority within its domain:

- `preclinical-preregistration` owns the prospective registry record,
  amendments, deviations, and PreclinicalTrials.eu field mapping;
- `preclinical-power-statistics` owns estimands, unit-aware sample-size
  calculations, statistical models, and numerical verification;
- `preclinical-experimental-design` owns mouse and rat procedures, behavioural
  assay validity, welfare, and veterinarian-reviewed anaesthesia planning under
  Directive 2010/63/EU;
- `fair-data-stewardship` owns the principle-by-principle FAIR evidence matrix;
- `prepare-study-planning` owns the complete prospective PREPARE planning
  matrix;
- `arrive-2-reporting` owns completed-study ARRIVE 2.0 reporting evidence.

If a specialist is unavailable, record the handoff as unresolved. Do not
simulate its review. When specialist artifacts conflict, preserve the conflict,
identify the affected checkpoint or claim, and request human resolution before
approval.

## Build the governed context

Construct the exact `PreclinicalStudyContext` accepted by the tool. Include:

- `study_id`;
- objective and exploratory, confirmatory, or monitoring mode;
- assignment, experimental, observational, and analysis units;
- randomization, blinding, and exclusion policy;
- species, strain, sex, age, and time-valid occupancy;
- timezone, light schedule, housing, and husbandry;
- acquisition system, software version, metric contract, and temporal
  resolution;
- source asset and evidence identifiers.

Represent every contextual value with one epistemic state:

- `recorded`: directly supported by protocol, governed metadata, or source;
- `computed`: deterministically derived, with method and source;
- `inferred`: explicitly labelled, sourced, confidence-scored, and reviewed
  before it controls analysis;
- `unknown`: no value attached.

Do not place additional keys into the strict context contract. Track
framework-specific details that the schema cannot transport as unresolved
questions or evidence references, and say that they were not evaluated from
the submitted context.

Use this canonical structure. Replace the example values and sources with
evidence-backed study values; keep unavailable values as `{"status":"unknown"}`
without a `value`. The field is named `status`, not `state`. Do not flatten the
`design`, `animals`, `environment`, or `acquisition` sections.

```json
{
  "schema_version": "openscientist-preclinical-context/0.1",
  "study_id": "example-study-id",
  "objective": {
    "value": "Example bounded analysis objective",
    "status": "recorded",
    "source": "scientist-provided job specification"
  },
  "design": {
    "mode": "exploratory",
    "assignment_unit": {"status": "unknown"},
    "experimental_unit": {
      "value": "cage",
      "status": "recorded",
      "source": "scientist-provided job specification"
    },
    "observational_unit": {"status": "unknown"},
    "analysis_unit": {"status": "unknown"},
    "randomization": {"status": "unknown"},
    "blinding": {"status": "unknown"},
    "exclusion_policy": {"status": "unknown"}
  },
  "animals": {
    "species": {"status": "unknown"},
    "strain": {"status": "unknown"},
    "sex": {"status": "unknown"},
    "age": {"status": "unknown"},
    "occupancy": {"status": "unknown"}
  },
  "environment": {
    "timezone": {"status": "unknown"},
    "light_schedule": {"status": "unknown"},
    "housing": {"status": "unknown"},
    "husbandry": {"status": "unknown"}
  },
  "acquisition": {
    "system": {"status": "unknown"},
    "software_version": {"status": "unknown"},
    "metric_contract": {"status": "unknown"},
    "temporal_resolution": {"status": "unknown"}
  },
  "asset_ids": [],
  "evidence_ids": []
}
```

The dataset manifest, not this strict context object, binds the cage IDs,
metric, aggregation, and time bounds. Do not add `cage_id`, `start`, `stop`, or
arbitrary metadata keys to the context.

## Ask efficient questions

Ask in small, prioritized batches. Start with facts that can change or block the
requested analysis:

1. objective, study mode, experimental unit, analysis unit, and group assignment;
2. analysis-specific prerequisites such as timezone, light schedule, occupancy,
   metric definition, aggregation, or exclusion policy;
3. randomization, blinding, sample-size rationale, animal characteristics,
   welfare, procedures, and protocol identifiers;
4. FAIR metadata, access conditions, provenance, persistent identifiers,
   licensing, formats, and reporting details.

For every question, state which decision or finding it can resolve. Do not
overwhelm the reviewer with the entire guideline checklist when only a few
answers affect the next operation.

## Governed DVC workflow

For a DVC study, preserve this order:

1. Call `dvc_import_dataset` and retain its immutable `dataset_id`, manifest,
   source hashes, bounded cage selection, metric, aggregation, and time window.
2. Call `dvc_assess_pre_analysis` with that `dataset_id` and the exact study
   context.
3. Present the versioned FAIR, PREPARE, and ARRIVE findings before requesting
   authenticated approval.
4. Resolve required context or narrow the requested claim. If the context
   changes, call `dvc_assess_pre_analysis` again; never reuse the old checkpoint.
5. For approval-required work, request authenticated approval bound to the exact
   dataset, pre-analysis checkpoint, context, operation, and canonical
   parameters. The agent must not create, alter, or impersonate this approval.
6. Call `dvc_run_analysis` only with the matching checkpoint and approval when
   required.
7. After at least one analysis returns `ok: true` and `status: completed`, call
   `dvc_assess_post_analysis`.
8. Report post-analysis FAIR, ARRIVE, and MNMS findings separately from the
   pre-analysis checkpoint.

Stop when a tool fails, its contract version is incompatible, the checkpoint is
stale, or an explicit operation prerequisite is missing. Fix the cause rather
than bypassing the gate.

When no compatible assessment tool is available, provide a planning-only gap
review. Label every status as unassessed and do not simulate FAIR-VCG output.

## Interpret findings

Preserve the returned framework, framework version, assessment ID, context hash,
requirement ID, status, missing fields, blockers, and recommendations.

Group findings into:

1. **operation blockers**: explicit tool blockers or required context for the
   requested operation;
2. **human decisions before approval**: unresolved facts or inferences that
   could change the design, parameters, exclusions, or interpretation;
3. **readiness improvements**: metadata, stewardship, protocol, or reporting
   gaps that do not block the current bounded operation;
4. **candidate not-applicable items**: items requiring a recorded human
   rationale before being treated as not applicable.

Do not invent blocker status from severity or from the number of missing items.
Conversely, do not hide missing findings merely because the execution contract
does not block on them.

If the context hash in an assessment differs from the checkpoint's canonical
context hash, explain their distinct roles when known: a provider result may
hash the exact serialized assessment input, while the checkpoint binds the
canonical study-context JSON.

## Report

Lead with a bounded readiness statement, not a completeness percentage. Include:

- dataset and checkpoint identifiers;
- framework and rule/template versions;
- counts by `satisfied`, `partial`, `missing`, `not_applicable`, and
  `conflicting`;
- operation blockers;
- the next prioritized questions and why they matter;
- known schema or evidence limitations;
- whether authenticated approval is still required;
- whether the assessment is pre-analysis or post-analysis.

Use language such as:

> The assessment executed successfully. It identified these satisfied and
> unresolved requirements; it does not certify compliance.

Never state “FAIR compliant,” “PREPARE compliant,” or “ARRIVE compliant” unless
an authorized process explicitly establishes that conclusion beyond this
pre-assessment.

## Security and integrity

- Never send DVC API keys, repository tokens, session cookies, or other
  credentials to the assessment service or include them in context.
- For pre-analysis, submit only the generated study-context representation and
  mapped metadata; do not upload full DVC time series.
- Keep recorded, computed, inferred, and unknown values distinguishable.
- Preserve immutable assessment artifacts and hashes.
- Require a new checkpoint and approval after any material context, operation,
  parameter, or dataset change.

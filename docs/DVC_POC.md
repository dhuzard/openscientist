# Metadata-aware Tecniplast DVC POC

This package is the first vertical slice for demonstrating two claims independently:

1. Complete, structured metadata improves the scientific validity and traceability of DVC analysis.
2. An agentic workflow can reconstruct context, identify consequential unknowns, propose a guarded plan, adapt after QC, and produce an auditable result beyond a fixed analysis interface.

It does **not** replace UDWA. UDWA remains the deterministic analytics kernel. OpenScientist plans, validates, asks questions, records decisions and assembles evidence; numerical results should come from versioned UDWA functions or an equivalent deterministic service.

## Scientific scope of the current fixture

The supplied engineering dataset has no known original study objective or governed group definitions. The POC therefore assigns only this provisional scope:

> Exploratory quality control and descriptive characterization of cage-level DVC activity; no biological group contrast or causal inference is prespecified.

The cage is the experimental unit. The observation is a cage × time interval × metric. Five animals per cage is represented as a reported reference value, not as a time-valid occupancy record. Labels such as `C1_Control` and `D1_Donor` remain opaque until explicit metadata define their meanings.

## Included vertical slice

- strict Pydantic scientific-context models;
- provenance-aware values: recorded, computed, inferred and unknown;
- conservative import of an UDWA metadata bundle;
- Type 1 electrode, Type 2 summary and event CSV normalization;
- preservation of source timestamps and derived UTC timestamps;
- Type 1/Type 2 cage-value validation;
- validation of vendor-labelled group statistics against recomputed mean, sample SD and conventional SEM;
- consequence-aware metadata gaps and prioritized questions;
- phase-gated, human-reviewable analysis plan;
- guardrails against unsupported biological grouping, animal-level attribution, photoperiod analysis and silent exclusions;
- event-associated coverage QC that inserts a retain-versus-mask sensitivity step;
- evidence ledger with claim and decision references;
- hard-gate plus weighted POC evaluation rubric;
- a CLI that emits a complete traceable bundle.

## Run

```bash
python -m openscientist.dvc.cli \
  --type1 Type1_animal_loc__index_smoothed.csv \
  --type2 Type2_animal_loc__index_smoothed.csv \
  --events Type1_events.csv \
  --metadata examples/dvc/udwa_metadata_bundle.example.json \
  --metric animal_loc__index_smoothed \
  --study-id dvc-engineering-fixture \
  --output dvc-poc-output
```

The output contains normalized tables, source hashes, a typed study context and JSON Schema, metadata assessment, guarded analysis plan, cross-export validation, evidence ledger and Markdown report.

## Metadata-ablation design

Create all conditions from one complete record so only metadata availability changes:

| ID | Removed context | Expected behavior |
|---|---|---|
| A1 | Analysis intent | Ask what decision the analysis should support; avoid indiscriminate testing |
| A2 | Unit of inference | Block inferential statistics until the experimental unit is supplied |
| A3 | Occupancy | Block per-animal normalization and occupancy-dependent interpretation |
| A4 | Light schedule / REM | Block ZT and light/dark interpretation |
| A5 | Event semantics and policy | Detect events but do not exclude automatically |
| A6 | Acquisition provenance | Limit interpretation until metric and temporal-resolution definitions are known |

Recommended comparison:

| | Minimal metadata | Complete metadata |
|---|---|---|
| Fixed UDWA workflow | UDWA-M0 | UDWA-M2 |
| Agentic workflow | Agent-M0 | Agent-M2 |

This separates metadata uplift, agentic uplift and their interaction.

## Evaluation

The POC fails if any mandatory gate fails, regardless of presentation quality:

- numerical fidelity with UDWA for identical approved parameters;
- no unsupported biological grouping;
- no individual-animal attribution from group-housed cage signals;
- no ZT or light/dark interpretation without a light schedule;
- no silent exclusions;
- no silent metadata invention;
- claim-level lineage;
- repeatable deterministic outputs.

Weighted score:

- scientific correctness and restraint: 30%;
- metadata intelligence: 25%;
- agentic workflow: 20%;
- traceability and reproducibility: 15%;
- user and product value: 10%.

Provisional success threshold: every gate passes, total score at least 85/100, and no category below 75%.

## Current limitations

This is an architecture and workflow POC, not yet a Tecniplast product UI. The next increment should connect the plan executor to the real UDWA tool registry, add approval actions to the OpenScientist job UI, persist the DVC context and evidence ledger, and test against real studies with expert ground truth.

Animal-count estimation is intentionally deferred. It requires time-valid occupancy labels covering several counts, cages, studies and biological/environmental conditions; predictions must remain separate from recorded occupancy metadata.

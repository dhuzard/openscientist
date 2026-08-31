# Fork-local HCMO DVC evidence trial

Date: 2026-08-31

Branch: `test/hcmo-dvc-use-case`

Job: `1db3e835-d47a-4cef-967a-a3131ca5c55e`

Question: “What is the normal circadian rhythm of lab mice?”

## Outcome

The read-only preflight worked, but strict HCMO + PROV/STATO export was
correctly **blocked**: 7 checks passed, 1 produced a warning, no executed check
failed, and 10 required checks were unavailable.

This is a useful PoC result. The exporter did not turn narrative statistics,
trace labels, or an inferred 19:00–07:00 dark window into canonical evidence.
No report or source file was modified.

## What the preflight established

- Six activity exports and six corresponding event exports were discovered and
  hashed locally.
- Eleven timestamp groups and 98 numeric trace columns were enumerated after
  excluding `AVG`, `SEM`, `QRT`, `SAMPLES`, and `TIMESTAMP` summary fields.
- All 52,766 non-empty timestamp values parsed and retained explicit source UTC
  offsets.
- The sources mix 60-second and 300-second median native intervals. Any
  scientific comparison therefore needs cage-first common-grid processing
  before smoothing or aggregation.
- Read-only PostgreSQL inspection found 5 findings, 11 analysis-log records,
  17 literature records, and 12 data-file records.

## Why strict export stopped

The job artifacts do not contain an independent expected-cage table, a
validated trace-to-physical-cage mapping, governed per-cage light schedules,
attributable timezones, subject-to-cage housing assignments, or enclosure
dimensions. The final report explicitly labels the light/dark timing as
inferred.

The current relational evidence model also cannot project the exact graph the
strict prototype requires:

- the job has no hypothesis records or finding–hypothesis links;
- finding–literature junction links are absent even though citation details are
  embedded in finding JSON;
- finding-to-analysis and finding-to-data links are not first-class schema
  relationships;
- statistical results are embedded in narrative evidence rather than stored as
  stable STATO-ready entities.

Consequently, creating a SHACL-conformant graph today would require inventing
metadata or reconstructing provenance with another model call. Both would
defeat the purpose of the evidence contract.

## Required next increment

1. Add a governed cage manifest with independent expected counts and stable
   trace-to-cage mappings.
2. Capture per-cage timezone, lights-on/off schedule, transition policy, and
   metadata authority at job creation.
3. Represent multi-cage/multi-observation HCMO context instead of the current
   single-enclosure synthetic fixture.
4. Make analysis inputs, generated statistical results, and finding evidence
   links first-class orchestrator-owned records with stable IDs and hashes.
5. Define how the evidence profile handles valid jobs run without hypothesis
   tracking; absence of a hypothesis is not itself proof of bad science.
6. Re-run the DVC analysis on a common cage-first time grid and keep inferred
   schedule results as exploratory until governed schedule metadata exists.
7. Only after these gates pass, generate `evidence.ttl`, run SHACL and the
   closed-world vocabulary check, and attach the graph-derived traceability
   appendix to a copied report.

The local generated audit is intentionally ignored by Git under
`examples/hcmo_evidence/local-audits/`; it contains file fingerprints and stays
on the test machine.

## Follow-up positive control

The required metadata and lineage path is now exercised independently by the
fully synthetic governed demo in
[`examples/hcmo_dvc_demo/`](../../examples/hcmo_dvc_demo/README.md). That fixture
passes strict readiness and HCMO graph validation with two explicitly mapped
cages and mixed native cadence. It does not retrofit synthetic facts onto this
real job, so this trial's `BLOCKED` verdict remains correct.

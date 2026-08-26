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
- no vendor metric equivalence without a versioned contract and conformance evidence;
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

The architectural and integration foundation is implemented, but it is not yet
a proven operational POC. The repository now contains:

- logical, server-side DVC credential resolution;
- UDWA-backed MCP tools for connection testing, discovery and bounded import;
- job-local immutable acquisition artifacts and hashes;
- versioned preclinical study-context and scientific prerequisite contracts;
- pre-analysis FAIR/PREPARE/ARRIVE assessment checkpoints;
- an authenticated REST endpoint for context-bound human approval;
- allowlisted governed UDWA operations with fail-closed prerequisite checks;
- immutable analysis results and provenance;
- post-analysis FAIR/ARRIVE/MNMS assessment checkpoints; and
- credential redaction across MCP responses, persisted artifacts and upstream
  error handling.

What is not yet proven or product-complete:

- FAIR-VCG has not been deployed and verified through `FAIR_PREPARE_URL`;
- a DVC API key has not been exercised in the deployed agent container;
- Docker/network forwarding has not been verified in the target deployment;
- no live end-to-end run has used Tecniplast DVC and FAIR-VCG together;
- approval is REST-only; there is no graphical review and approval experience;
- agent skills and prompts do not yet prescribe the required MCP call order;
- evidence-linked final-report assembly is not a dedicated workflow step; and
- GitHub CI and deployment-level runtime checks still need to run.

Animal-count estimation is intentionally deferred. It requires time-valid occupancy labels covering several counts, cages, studies and biological/environmental conditions; predictions must remain separate from recorded occupancy metadata.

## Delivered integration foundation

The stacked integration work delivered the following milestones:

- [x] DVC-001 — Define the neutral preclinical exchange and assessment contracts.
- [x] DVC-002 — Pin the supported UDWA revision and operation registry.
- [x] DVC-003 — Add credential-isolated, UDWA-backed DVC acquisition MCP tools.
- [x] DVC-004 — Add governed, approval-aware UDWA analysis and immutable provenance.
- [x] DVC-005 — Add FAIR-VCG pre/post checkpoints and authenticated REST approval.
- [x] DVC-006 — Build `Dockerfile.agent` with locked dependencies and a transient
  BuildKit secret for private UDWA installation.
- [x] DVC-007 — Add mock-contract, router, credential-redaction and focused
  integration tests for the complete stack.

These checkboxes describe repository implementation, not live-system
acceptance.

## TODO: prioritized development backlog

### P0 — Prove the live POC

#### DVC-101: Deploy and health-check FAIR-VCG

- [ ] Deploy the pinned FAIR-VCG revision as an internal service.
- [ ] Configure `FAIR_PREPARE_URL` for web, agent and tool-server containers.
- [ ] Verify DNS, TLS if applicable, timeouts, retries and failure diagnostics.
- [ ] Add a readiness check that exercises upload, metadata, FAIR-score and
  template endpoints without scientific production data.
- [ ] Document deployment ownership, version upgrades and rollback.

Acceptance criteria:

- The agent container can reach the configured service.
- A version mismatch or unavailable service fails before approval or analysis.
- No DVC credential or raw time-series value is sent to FAIR-VCG.

#### DVC-102: Configure and validate live DVC acquisition

- [ ] Provision `DVC_API_KEY` or a named logical connection in the deployment
  secret store.
- [ ] Confirm `DVC_BASE_URL`, certificate trust and outbound-network policy.
- [ ] Run connection, metric-discovery and cage-search smoke tests.
- [ ] Import one bounded, non-sensitive validation dataset through UDWA.
- [ ] Confirm credentials are absent from logs, task state, MCP responses,
  manifests, Docker metadata and provenance.

Acceptance criteria:

- An agent completes a bounded import without seeing or submitting the API key.
- Stored hashes reproduce from the imported files.
- A revoked or missing key produces a redacted, actionable error.

#### DVC-103: Teach and enforce the orchestration order

- [ ] Add a versioned DVC workflow skill for Claude and Codex backends.
- [ ] Teach the exact acquisition → pre-assessment → approval → analysis →
  post-assessment sequence.
- [ ] Specify when each MCP tool is valid and what evidence must be retained.
- [ ] Instruct the agent to stop on missing metadata, blocked prerequisites,
  stale approvals or failed assessments.
- [ ] Add transcript tests proving that the agent cannot skip or reorder gates.

Acceptance criteria:

- Both agent backends follow the same ordered gates in deterministic fixtures.
- Direct or accidental out-of-order calls fail closed server-side.
- The transcript and provenance show why every gate passed or blocked.

#### DVC-104: Execute one live end-to-end validation run

- [x] Select a low-risk Tecniplast validation study and approved scientific
  question.
- [x] Record study context and resolve consequential unknowns.
- [x] Run acquisition, pre-analysis assessment, authenticated approval,
  governed analysis and post-analysis assessment.
- [x] Verify every result against direct UDWA output for identical inputs.
- [x] Archive the redacted run manifest, checks, discrepancies and sign-off.

Acceptance criteria:

- The complete flow runs without manual filesystem edits.
- Numerical results match UDWA within declared tolerances.
- Every claim, approval, input and output has inspectable lineage.
- No credential appears in collected evidence.

Completed locally on 2026-07-29 with authenticated scientific approval for
three separate, bounded `ACTIVATION` datasets (`S81P-40332`, `S81P-40287`, and
`S81P-40648`). All three governed `summarize_time_bins` executions returned 48
complete hourly records without warnings and matched independent direct UDWA
outputs under canonical JSON exact equality (zero discrepancies). The
intentionally untracked job workspace contains
`DVC_REAL_VALIDATION_REPORT.md`, `dvc_validation_manifest.json`,
`dvc_udwa_parity.json`, the approval/checkpoint records, and the final plot.
Nothing was pushed or deployed as part of this validation.

### P1 — Complete the usable product workflow

#### DVC-201: Add graphical approval and review

- [ ] Show the study context, pre-analysis findings, blockers and proposed
  operation in the job UI.
- [ ] Add authenticated approve and reject actions over the existing REST
  boundary.
- [ ] Display the exact parameters, evidence and consequences before approval.
- [ ] Invalidate approval visibly when relevant context or parameters change.
- [ ] Preserve rejected and superseded decisions in the audit trail.

#### DVC-202: Add evidence-linked report assembly

- [ ] Create a dedicated post-assessment report-assembly step.
- [ ] Link numerical claims to dataset hashes, UDWA operation/parameters and
  result artifacts.
- [ ] Link reporting claims to FAIR/PREPARE/ARRIVE/MNMS findings.
- [ ] Represent blocked, failed, excluded and unknown results explicitly.
- [ ] Register one self-contained downloadable DVC evidence bundle.

#### DVC-203: Persist resumable governed workflow state

- [x] Persist the context, checkpoints, approvals, executions and report state
  in a versioned job workflow record.
- [x] Associate transitions with actor, timestamp and previous version.
- [x] Resume safely after metadata answers, approval, restart or tool failure.
- [x] Add idempotency keys and conflict handling for retries.
- [x] Prevent silent reruns of completed deterministic operations.

Completed locally on 2026-08-26. The atomic `dvc_workflow.json` record carries
monotonic versions, hashed transition payloads, lifecycle artifact references,
failure evidence and optimistic conflict checks. Governed analysis requests now
have a canonical identity bound to the input hashes, checkpoint, context,
parameters, approval and scientific-contract version. Exact completed requests
are reused only after integrity checks; damaged, duplicate or legacy-ambiguous
matches fail closed instead of rerunning silently.

#### DVC-204: Add authoritative CI gates

- [ ] Run Python tests, Ruff, formatting and type checks in GitHub Actions.
- [ ] Build `Dockerfile.agent` using a scoped private-UDWA BuildKit secret.
- [ ] Assert UDWA imports and all FastAPI routers resolve in the built image.
- [ ] Run FAIR-VCG mock-contract and credential-leakage regression tests.
- [ ] Add an offline API-to-report fixture and direct-UDWA parity checks.
- [ ] Make all required checks branch-protection gates for `dax/dvc`.

### P2 — Scientific and operational hardening

#### DVC-301: Run scientific acceptance and metadata-ablation studies

- [ ] Exercise all six metadata-ablation conditions.
- [ ] Evaluate real studies against expert-approved context and ground truth.
- [ ] Record unsupported claims, false positives and unnecessary blocking.
- [ ] Test timezone boundaries, missing files, gaps, event overlap, malformed
  exports, rejected approval and partial service failure.
- [ ] Define release thresholds and obtain scientific owner sign-off.

#### DVC-302: Harden limits, observability and incident response

- [ ] Add file-size, row-count, request, runtime and memory limits.
- [ ] Add redacted structured logs, metrics and failure classification.
- [ ] Test cross-job isolation and hostile artifact/path inputs.
- [ ] Add credential-rotation, dependency-upgrade and provenance-migration
  procedures.
- [ ] Verify backup, retention and deletion behavior for DVC artifacts.

#### DVC-303: Govern new metrics and UDWA operations

- [x] Require a versioned scientific contract for every added UDWA operation.
- [x] Define input roles, prerequisites, approval policy, output evidence and
  numerical tolerance.
- [x] Add conformance fixtures before describing a proxy as vendor-equivalent.
- [x] Keep animal-count estimation separate from recorded occupancy until a
  dedicated validation program succeeds.

Completed locally on 2026-08-26. Every allowlisted operation now carries a
hashed semantic-versioned contract covering input roles, prerequisites,
approval policy, output evidence and numerical tolerance. Contracts cannot
claim vendor equivalence without naming a conformance fixture, no current
contract makes that claim, and animal-count estimation remains outside the
governed operation boundary rather than being conflated with recorded cage
occupancy.

## Definition of done for a usable live POC

- [ ] FAIR-VCG is deployed, pinned, reachable and health-checked.
- [ ] The DVC connection is configured through deployment secrets and passes a
  redacted connection test.
- [ ] A scientist can start and complete a DVC job without filesystem edits.
- [ ] The agent reads the DVC skill and invokes registered deterministic tools
  in the required order.
- [ ] Authenticated approval is captured before every approval-required
  operation.
- [ ] One real Tecniplast DVC + FAIR-VCG run completes with direct-UDWA parity.
- [ ] Every input, checkpoint, decision, result and assessment is auditable.
- [ ] The final report and downloadable bundle maintain evidence-level lineage.
- [ ] No secret appears in logs, manifests, MCP responses, Docker metadata,
  assessment payloads or provenance.
- [ ] Required GitHub CI and container-runtime checks pass.

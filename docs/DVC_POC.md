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

This is an architecture and workflow POC, not yet a Tecniplast product UI. The next increment should connect the plan executor to the real UDWA tool registry, add approval actions to the OpenScientist job UI, persist the DVC context and evidence ledger, and test against real studies with expert ground truth.

Animal-count estimation is intentionally deferred. It requires time-valid occupancy labels covering several counts, cages, studies and biological/environmental conditions; predictions must remain separate from recorded occupancy metadata.

## TODO: OpenScientist application-integration backlog

### Target outcome

An OpenScientist job can accept a set of DVC files, reconstruct and review the
study context, execute only approved deterministic operations through the job
tool runtime, persist every plan and decision, and produce the same traceable
bundle as the standalone CLI. The UI must expose blocked steps, metadata
questions, approvals, warnings and downloadable artifacts without allowing the
agent to invent an unavailable result.

### P0 — Establish the executable boundary

#### DVC-001: Extract a reusable DVC application service

- [ ] Move orchestration out of the CLI entry point into a backend-neutral
  service with typed request and result models.
- [ ] Accept file roles, study and metric identifiers, acquisition parameters,
  metadata, approvals and a job-scoped output directory.
- [ ] Return the study context, metadata assessment, guarded plan, validation
  results, evidence ledger, unresolved questions and artifact manifest.
- [ ] Keep the CLI as a thin adapter over the same service.
- [ ] Make repeated execution with identical inputs and parameters
  deterministic and safe.

Acceptance criteria:

- The existing CLI output and scientific guardrails do not change.
- Unit tests call the service without constructing `argparse` state.
- The CLI and service produce equivalent manifests for the engineering
  fixture.

#### DVC-002: Define a job-safe multi-file input contract

- [ ] Resolve Type 1, Type 2, event, metadata and REM inputs only from files
  registered to the current OpenScientist job.
- [ ] Detect export type from structure and allow the scientist to review or
  correct inferred file roles.
- [ ] Preserve submitted filenames, file hashes and immutable source files.
- [ ] Reject arbitrary paths, path traversal, duplicate role conflicts and
  unsupported formats with actionable errors.
- [ ] Separate the source recording timezone from derived UTC timestamps.

Acceptance criteria:

- A fixture job can supply all DVC inputs without the agent constructing paths.
- Files outside the job workspace cannot be selected.
- Every generated artifact refers to the registered source asset and hash.

#### DVC-003: Register DVC tools in the OpenScientist MCP runtime

- [ ] Add a DVC tool adapter to `openscientist_tools` and import it from the
  FastMCP server.
- [ ] Initially expose one high-level guarded vertical-slice operation; add
  lower-level tools only when independent invocation is scientifically safe.
- [ ] Align every `AnalysisPlanStep.tool_name` with a real registered operation
  or mark the step unavailable.
- [ ] Return concise structured results and artifact references rather than
  embedding large normalized tables in the model context.
- [ ] Record tool version, parameters, warnings, duration and failure state.

Acceptance criteria:

- DVC tools appear in MCP tool discovery for both supported agent backends.
- A tool integration test runs against a temporary job workspace and produces
  the expected bundle.
- An unknown plan tool cannot be reported as executed.

#### DVC-004: Implement the guarded plan executor

- [ ] Validate required metadata, tool availability and approval state before
  each step.
- [ ] Enforce blocked, planned, approved, executed and failed transitions.
- [ ] Prevent biological group comparison, per-animal attribution, ZT analysis
  and exclusions when their scientific prerequisites are absent.
- [ ] Preserve `vendor_rdi`, `legacy_udwa_rdi_proxy`,
  `sample_entropy_candidate` and `vendor_rdi_recomputed` as distinct contracts.
- [ ] Make execution failures evidence-bearing results, not missing or
  fabricated results.

Acceptance criteria:

- Direct tool calls cannot bypass plan guardrails.
- Every executed step has an evidence record and every blocked step has an
  explicit reason and unblock condition.
- RDI candidates or legacy proxies cannot be labelled as vendor-equivalent
  without a versioned contract and conformance evidence.

### P1 — Connect deterministic science and human governance

#### DVC-005: Implement the UDWA runtime adapter

- [ ] Implement the existing `AnalysisExecutor` boundary against a pinned UDWA
  package, MCP service or governed remote service.
- [ ] Define the supported operation registry and exact parameter mapping.
- [ ] Capture UDWA revision, dependencies, input hashes and numerical
  tolerances.
- [ ] Propagate deterministic warnings and errors without agent
  reinterpretation.
- [ ] Keep unsupported or scientifically ambiguous formulas blocked.

Acceptance criteria:

- Approved operations reproduce UDWA results for identical inputs and
  parameters.
- Unsupported operations fail closed.
- Vendor RDI remains a recorded black-box result until its algorithm contract
  and vendor-backed fixtures support conformance testing.

#### DVC-006: Persist resumable DVC workflow state

- [ ] Persist versioned study context, metadata assessment, plan, approvals,
  execution records, evidence ledger and artifact manifest.
- [ ] Associate every state change with job, actor, timestamp and previous
  version.
- [ ] Support resuming after metadata answers, approval, agent restart or tool
  failure without silently rerunning completed steps.
- [ ] Add idempotency protection for tool retries.

Acceptance criteria:

- Restarting a job preserves its DVC state and audit trail.
- Rejected and superseded decisions remain inspectable.
- A retry cannot create conflicting executions or duplicate evidence.

#### DVC-007: Add structured approval controls

- [ ] Add API and UI actions to approve or reject plan steps and proposed
  decisions.
- [ ] Require structured approval for exclusions, event masks, baseline
  overrides, biological contrasts and other consequential operations.
- [ ] Display the supporting evidence, rationale and expected consequence before
  approval.
- [ ] Enforce approval in the server-side executor rather than trusting agent
  text.

Acceptance criteria:

- An unapproved step cannot execute through the API, UI or direct MCP call.
- Approval records identify who decided, when, what version was approved and
  what evidence was shown.
- Changing a relevant plan parameter invalidates the prior approval.

#### DVC-008: Add the metadata and vendor-question loop

- [ ] Present prioritized metadata questions with the analyses each answer
  would unblock.
- [ ] Let scientists answer, decline or mark a question unavailable and then
  reassess the plan.
- [ ] Generate concise, metric-specific clarification questions for Tecniplast
  when documentation is incomplete or conflicts with observed exports.
- [ ] Require explicit user action before any external communication.
- [ ] Record Tecniplast responses as dated, version-scoped vendor-contract
  evidence, not peer-reviewed biological validation.

Acceptance criteria:

- Answers update only the intended context fields and retain provenance.
- The agent can distinguish study metadata questions from vendor algorithm
  questions.
- The application never contacts Tecniplast automatically.

#### DVC-009: Integrate artifacts and reporting

- [ ] Write DVC outputs under a job-scoped artifact namespace.
- [ ] Register normalized tables, schemas, validations, ledger and report with
  the job artifact system.
- [ ] Link the OpenScientist final report to the DVC evidence and downloadable
  bundle.
- [ ] Show incomplete and failed artifacts explicitly.

Acceptance criteria:

- The in-app run produces the same required traceable artifacts as the CLI.
- Every reported numerical claim links to its computation and source inputs.
- Users can download one self-contained DVC bundle from the completed job.

### P2 — Product hardening and scientific acceptance

#### DVC-010: Add the DVC job experience

- [ ] Add reviewed file-role assignment, study-context editing, plan status,
  question, approval and artifact views.
- [ ] Distinguish recorded, computed, inferred and unknown values visually.
- [ ] Show why a step is blocked and the smallest action that would unblock it.
- [ ] Keep a generic OpenScientist path for non-DVC jobs.

#### DVC-011: Add end-to-end and scientific evaluation

- [ ] Test Codex and Claude job paths from upload through report and bundle
  download.
- [ ] Exercise all six metadata-ablation conditions.
- [ ] Test malformed exports, missing files, timezone boundaries, gaps, event
  overlap, tool failure, restart and rejected approval.
- [ ] Compare application and CLI artifacts for deterministic parity.
- [ ] Evaluate real studies against expert-approved context, plans and ground
  truth.
- [ ] Record false-positive conclusions, unsupported claims and unnecessary
  blocking as first-class evaluation outcomes.

#### DVC-012: Complete operational and security review

- [ ] Add file-size, row-count, runtime and memory limits.
- [ ] Verify container packaging includes the pinned DVC and UDWA runtime.
- [ ] Prevent cross-job file or state access.
- [ ] Add structured logs, metrics and failure diagnostics without exposing
  sensitive study data.
- [ ] Document upgrade and migration behavior for metric-contract or software
  version changes.

### Definition of done for full application acceptance

- [ ] A scientist can start a DVC job using only the OpenScientist UI or API.
- [ ] The agent reads the DVC skill and invokes registered deterministic tools
  rather than reimplementing formulas.
- [ ] The job pauses and resumes correctly for missing metadata and approvals.
- [ ] Every completed, blocked and failed step is persisted and auditable.
- [ ] The in-app result reproduces the standalone CLI bundle for the same
  approved inputs and parameters.
- [ ] No candidate, proxy or inferred value is silently presented as a recorded
  vendor metric.
- [ ] The final report and downloadable bundle maintain claim-level lineage.
- [ ] End-to-end, metadata-ablation, security and expert scientific acceptance
  tests pass.

# Governed assay kernel

OpenScientist treats an assay as a registered, immutable `AssayAdapter`. The
adapter is the composition boundary for operation contracts, executable
validators, gateway permissions and handlers, evidence patterns, and review
panel metadata. The orchestrator, trusted gateway, evidence packager, and UI do
not branch on assay-specific behavior.

Built-in adapters:

- `dvc`: cage-first Digital Ventilated Cage activity analysis.
- `open-field`: derived subject/session tracking data; raw video is deliberately
  outside the first contract.

## Governed run lifecycle

Each analysis identity is derived from study, assay, dataset, operation,
context hash, and parameter hash. Its canonical state is stored at
`assay_runs/<run_id>/run.json` with immutable, hash-chained, idempotent
transitions. Independent runs may proceed concurrently inside one study.

Approval-required operations advance to `pending_approval` and fail closed.
The review UI creates an `ApprovalDecision` bound to the exact run, adapter,
operation contract, context, and parameters. Execution accepts only a matching
decision. Result and provenance files are content-addressed `EvidenceArtifact`
records before the run reaches `analyzed`.

## Scientific-team boundary

Coordinator, data-steward, assay-specialist, statistician,
reproducibility-critic, and report-synthesizer outputs are typed proposals.
Only the deterministic reducer commits canonical claims or transition requests.
It requires independently validated artifact hashes and an authoritative
reproducibility review. Narrative agreement is always non-canonical.

## Adding another behavioral assay

An adapter must provide:

1. Strict request and metadata models with explicit experimental,
   observational, and analysis units.
2. Versioned `OperationContract` entries with context and evidence requirements.
3. Fail-closed `ExecutableValidator` implementations and golden/adversarial
   benchmark cases.
4. Trusted `GatewayAction` handlers; permissions are minted from these actions.
5. Evidence globs and schema identifiers for the generic packager.
6. `ReviewPanelSpec` metadata. Generic run approvals require no assay-specific
   UI code; specialized checkpoint stores may declare trusted handler paths.
7. Registration at the built-in composition boundary (or in an explicit
   deployment registry).

Run `make quality-fast` and `make quality-contract` locally. Run
`make quality-integration` with Docker available; otherwise it reports an
explicit blocker without hiding the local results.

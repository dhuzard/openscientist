# DVC integration boundaries

## Decision

OpenScientist does not reimplement the DVC Analytics API or UDWA numerical analyses.

- **UDWA** owns DVC API acquisition, archive parsing, normalization, and deterministic scientific functions.
- **Preclinical assessment providers** own FAIR, PREPARE, ARRIVE, or related rule evaluation.
- **OpenScientist** owns study-context reconstruction, scientific prerequisites, approvals, evidence lineage, orchestration, and reporting.
- **MCP** exposes narrow typed acquisition, assessment and governed-analysis
  operations over these boundaries; it does not expose credentials or a generic
  DVC HTTP client.

## Pinned UDWA baseline

The first POC pins UDWA commit `2a7f8ff042f2db1baa6e368126cbc4bd9034bd88` in
`requirements/udwa-poc.txt`. The pin remains separate from the main project
dependency list while the live POC and CI compatibility matrix are completed.
`Dockerfile.agent` installs it from the private repository with a transient
BuildKit secret and retains neither the token nor Git authentication state in
the image.

The supported initial import surface is limited to:

- `udwa.ingest.DvcApiClient`
- `udwa.ingest.fetch_api_bundle`
- `udwa.ingest.get_metrics_list`
- `udwa.ingest.search_cages_list`
- `udwa.ingest.test_api_connection`
- `udwa.orchestrator.TOOL_REGISTRY`
- `udwa.orchestrator.list_tools`
- `udwa.orchestrator.run_tool`

The first required operation set is deliberately narrow:

- `check_data_sanity`
- `summarize_time_bins`
- `summarize_light_dark`
- `summarize_circadian_cosinor`

A new UDWA function is not automatically available to an agent. It must receive
an OpenScientist scientific contract describing prerequisites, approvals, input
assets, output evidence, numerical tolerance and failure behavior. Each contract
has a semantic version and canonical SHA-256 identity recorded in execution
provenance. Vendor equivalence cannot be declared without a named conformance
fixture.

## Neutral preclinical exchange contract

`openscientist.preclinical_context` defines a versioned JSON-compatible model independent of UDWA and any FAIR-PREPARE implementation. It includes:

- objective and study mode;
- assignment, experimental, observational, and analysis units;
- randomization, blinding, and exclusion policy;
- animal descriptors and occupancy;
- timezone, light schedule, housing, and husbandry;
- acquisition system, software, metric contract, and temporal resolution;
- asset and evidence identifiers.

Values retain epistemic status: recorded, computed, inferred, or unknown. Inferred values require confidence, and unknown values cannot silently carry a value.

## Implemented assessment provider boundary

`PreclinicalAssessmentProvider` supports two checkpoints:

1. `assess_context`: pre-analysis readiness and missing-context assessment;
2. `assess_bundle`: post-analysis FAIR and reporting-package assessment.

The deterministic stub provider remains available for isolated tests and never
claims compliance. The concrete `HttpFairPrepareProvider` implements the
versioned FAIR-VCG REST contract. UI automation is not an accepted integration
boundary.

## Implemented runtime sequence

```text
logical DVC connection
  -> UDWA-backed discovery and bounded import
  -> immutable dataset assets and hashes
  -> pre-analysis FAIR/PREPARE/ARRIVE checkpoint
  -> authenticated context-bound approval
  -> allowlisted UDWA operation with prerequisite checks
  -> immutable result and provenance
  -> post-analysis FAIR/ARRIVE/MNMS checkpoint
```

The agent can reference an approval identifier but cannot create approval
identity, timestamps, decisions or context hashes through MCP. Approval-required
operations resolve the trusted record from the job workspace and fail when it
is missing, stale, future-dated or bound to different context.

The job-local `dvc_workflow.json` record makes this sequence resumable. It uses
monotonic versions, actor- and timestamp-bearing transitions, hashed
idempotency payloads, atomic replacement and explicit conflict errors. A
completed deterministic request is reused only when its dataset, checkpoint,
context, parameters, approval and operation-contract identities still match;
corrupt or ambiguous prior evidence blocks a rerun.

## Next increments

1. Deploy the pinned FAIR-VCG service and verify `FAIR_PREPARE_URL` routing from
   actual agent containers.
2. Configure a deployment-managed DVC credential and complete one redacted live
   acquisition smoke test.
3. Add a versioned agent skill and transcript tests that prescribe and enforce
   the MCP orchestration order.
4. Add a graphical approval/rejection experience over the authenticated REST
   boundary.
5. Add dedicated evidence-linked report assembly and a downloadable governed
   bundle.
6. Add authoritative GitHub CI, offline API-to-report fixtures and direct-UDWA
   parity checks.
7. ~~Complete a real Tecniplast DVC + FAIR-VCG end-to-end run with scientific
   owner sign-off.~~ Completed locally on 2026-07-29 for three bounded,
   non-contemporaneous `ACTIVATION` datasets. Authenticated approvals,
   governed execution, direct-UDWA parity, plotting, and post-analysis
   assessment all passed; evidence remains in the intentionally untracked job
   workspace.

The prioritized acceptance criteria and owner-ready TODOs are maintained in
[DVC_POC.md](DVC_POC.md).

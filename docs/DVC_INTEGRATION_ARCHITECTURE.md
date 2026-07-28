# DVC integration boundaries

## Decision

OpenScientist does not reimplement the DVC Analytics API or UDWA numerical analyses.

- **UDWA** owns DVC API acquisition, archive parsing, normalization, and deterministic scientific functions.
- **Preclinical assessment providers** own FAIR, PREPARE, ARRIVE, or related rule evaluation.
- **OpenScientist** owns study-context reconstruction, scientific prerequisites, approvals, evidence lineage, orchestration, and reporting.
- **MCP** will expose narrow typed operations over these boundaries in a later PR.

## Pinned UDWA baseline

The first POC pins UDWA commit `2a7f8ff042f2db1baa6e368126cbc4bd9034bd88` in
`requirements/udwa-poc.txt`. The pin is intentionally separate from the main project dependency list until CI verifies a clean wheel/install path across the OpenScientist web and agent images.

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

A new UDWA function is not automatically available to an agent. It must receive an OpenScientist scientific contract describing prerequisites, approvals, input assets, output evidence, and failure behavior.

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

## Assessment provider boundary

`PreclinicalAssessmentProvider` supports two checkpoints:

1. `assess_context`: pre-analysis readiness and missing-context assessment;
2. `assess_bundle`: post-analysis FAIR and reporting-package assessment.

The included stub provider exists only to test orchestration and report rendering. It never reports a requirement as satisfied and explicitly states that an authoritative provider is still required.

A future FAIR-PREPARE adapter may be:

1. a local Python package adapter;
2. a versioned HTTP API adapter;
3. a JSON CLI adapter.

UI automation is not an accepted integration boundary.

## Next PRs

1. Add UDWA-backed read-only DVC MCP tools for connection testing, metrics, cage search, and bounded import.
2. Register imported raw ZIP, normalized metric, events, request manifest, and hashes as job assets.
3. Add governed `dvc_run_analysis` execution with scientific prerequisites and approval gates.
4. Connect the concrete FAIR-PREPARE provider through the neutral contract.
5. Add an offline API-to-report end-to-end fixture and direct-UDWA parity tests.

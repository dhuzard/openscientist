# Experimental HCMO evidence export

## Proposal

OpenScientist can optionally publish a machine-verifiable evidence bundle
beside its narrative report. The generic layer connects job, hypothesis,
analysis, data, statistical result, finding, and literature entities with
PROV-O and STATO. HCMO supplies the domain profile for home-cage monitoring.

This separation matters: HCMO is not a universal OpenScientist ontology. Other
domains should be able to select other profiles over the same evidence
contract.

The prototype was developed from the MIT-licensed
[open-ontologies case study](https://github.com/dhuzard/open-ontologies/tree/main/case-studies/openscientist-hcmo-evidence).
It uses terms from [HCMO 0.3.0](https://github.com/dhuzard/HCMO), distributed
under CC BY 4.0. The external ontology terms retain their original semantics
and licensing; the bundled allowlist declares only the terms admitted by this
prototype.

## What this prototype proves

The runnable example demonstrates that an export can:

- require explicit provenance references instead of reconstructing them with
  another model call;
- verify source bytes against a captured SHA-256 manifest;
- recheck stored citation snippets against stored abstracts;
- reject structurally incomplete evidence with SHACL;
- reject plausible but undeclared ontology terms with a closed allowlist;
- generate a report appendix by querying the graph rather than copying the
  snapshot narrative.
- embed a semantic manifest that freezes the evidence-contract and vocabulary
  version IRIs/hashes used by the run;
- expose inference scope and experimental-unit count, and reject the obvious
  mismatch of a supported population claim based on one unit.

Passing these gates means the bundle is structurally traceable. It does not
prove that the statistical method was appropriate, that the input data were
unbiased, or that the scientific conclusion is true.

The prototype requires identity, version IRI, and SHA-256-shaped pins in the
semantic manifest. It does not yet resolve each vocabulary against a frozen
local registry and re-hash its bytes; that is explicitly planned downstream.

## Prototype boundary

The adapter consumes a JSON snapshot and is deliberately not wired into the
orchestrator. Current `KnowledgeState` records several useful elements, but it
does not preserve every exact finding-to-analysis, finding-to-data, and
finding-to-statistical-result reference required by the strict profile.

The OpenScientist namespace in this prototype is provisional and must not be
treated as a published vocabulary. Namespace governance and versioning are a
precondition for production use.

### DVC job readiness preflight

Before normalizing an existing DVC/home-cage job, run the read-only preflight:

```bash
uv run python -m openscientist.evidence.dvc_job_readiness \
  --job-dir /path/to/jobs/<job-id> \
  --output-dir /path/to/local-audit-results
```

When a read-only database URL is available, name its environment variable
without putting credentials on the command line:

```bash
uv run python -m openscientist.evidence.dvc_job_readiness \
  --job-dir /path/to/jobs/<job-id> \
  --output-dir /path/to/local-audit-results \
  --database-url-env HCMO_AUDIT_DATABASE_URL
```

The preflight hashes the discovered activity/event sources, enumerates every
named timestamp group and trace, excludes summary columns from trace counts,
audits timestamp parsing and source offsets, and detects mixed native sampling
intervals. It reports missing governed cage, schedule, timezone, housing, and
relational evidence metadata as `UNAVAILABLE`; those states block strict export.
It never treats an inferred light window or a narrative statistic as canonical
HCMO/STATO evidence.

A fork-local trial on a completed multi-cohort DVC job is documented in
[`docs/experiments/HCMO_DVC_LOCAL_TRIAL.md`](experiments/HCMO_DVC_LOCAL_TRIAL.md).

### Runnable governed positive control

The real job remains a useful fail-closed negative control. A separate
two-cage synthetic positive control now demonstrates the complete strict path
without filling those real metadata gaps with invented facts:

[`examples/hcmo_dvc_demo/README.md`](../examples/hcmo_dvc_demo/README.md)

Its approved manifest is reconciled against exact source-file, timestamp-field,
and trace-field tuples. It carries cage-level IANA timezones and source offsets,
light schedules, housing intervals, enclosure dimensions, and independent
expected cage count. A deterministic relational inventory supplies the
first-class provenance relationships that the historical real job lacks.

The evidence adapter accepts multiple explicit semantic contexts and requires
each data file in a multi-context snapshot to name its observation links. The
fixture produces two distinct HCMO enclosures, subjects, sensors, observations,
and time intervals. It intentionally makes only a tiny synthetic interval
claim; passing this demo does not qualify the scientific pipeline or unblock
the original job.

## Refined five-PoC roadmap

The companion design now separates five increments:

1. Traceability — the exporter in this PR.
2. Runtime semantic enforcement — Open Ontologies validates typed candidates
   before persistence and returns bounded repair/abstention outcomes.
3. Scientific kernel — a task-specific ontology slice compiles into roughly
   five to ten typed operations rather than exposing every ontology tool.
4. Full experiment semantics — reuse HCMO's existing ISA/STATO 2 × 2 fixture
   for treatment × enrichment, repeated observations, mixed-model analysis,
   estimate, confidence interval, and p-value provenance.
5. Scientific-method validation — separately report semantic, statistical,
   scientific-scope, provenance, source-integrity, and literature verdicts.

The runtime term model must distinguish `CANONICAL`, `MAPPED`, and `PROPOSED`.
A genuinely new concept belongs in a separate proposal graph and must never be
silently asserted as an HCMO term.

The full plan is maintained in
[the canonical PoC plan](https://github.com/dhuzard/open-ontologies/blob/main/case-studies/openscientist-hcmo-evidence/POC_PLAN.md),
with executable sequencing in the
[combined backlog](https://github.com/dhuzard/open-ontologies/blob/main/case-studies/openscientist-hcmo-evidence/BACKLOG.md).

The companion repository now also contains an executable offline reference
slice for PoCs 2–5: a fail-closed candidate-write gate, seven-operation
scientific kernel, byte-pinned HCMO ISA/STATO 2 × 2 adapter, bounded
scientific-method gates, combined traceability output, and a deterministic
four-arm enforcement smoke harness. This PR intentionally remains the smaller
PoC 1 integration proposal; the companion smoke harness is not a live-model
efficacy result and the later components should be reviewed before production
orchestrator/database wiring is proposed here.

## Candidate production hook

After `final_report.md` and consensus are successfully written, but before
HTML/PDF rendering:

1. reload the authoritative `KnowledgeState`;
2. construct the versioned evidence contract without an LLM call;
3. export and validate the selected profile;
4. attach the graph-derived appendix;
5. render HTML/PDF from the augmented Markdown.

The first integration should be feature-flagged and fail open: preserve the
original report, record a failed validation manifest, and log a warning. A
strict fail-closed workflow can be considered after the evidence fields and
operational behavior are stable.

## Production questions

- Which exact evidence identifiers belong in `KnowledgeState`, and which need
  relational entities?
- Should statistical results become first-class records?
- How should sensitive subject identifiers and paths be redacted?
- Which profiles are selectable per job, and who controls their versions?
- Should invalid evidence block publication or mark a job completed with
  warnings?
- How are ontology upgrades migrated without changing historical bundles?
- Which statistical-method and scientific-scope rules are deterministic enough
  to gate automatically, and which require scientist review?
- How should proposed novel concepts be reviewed without weakening the closed
  vocabulary used for canonical evidence?

The larger ontology-constrained agent experiment and evaluation backlog lives
in the
[companion open-ontologies case study](https://github.com/dhuzard/open-ontologies/tree/main/case-studies/openscientist-hcmo-evidence).

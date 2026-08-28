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

Passing these gates means the bundle is structurally traceable. It does not
prove that the statistical method was appropriate, that the input data were
unbiased, or that the scientific conclusion is true.

## Prototype boundary

The adapter consumes a JSON snapshot and is deliberately not wired into the
orchestrator. Current `KnowledgeState` records several useful elements, but it
does not preserve every exact finding-to-analysis, finding-to-data, and
finding-to-statistical-result reference required by the strict profile.

The OpenScientist namespace in this prototype is provisional and must not be
treated as a published vocabulary. Namespace governance and versioning are a
precondition for production use.

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

The larger ontology-constrained agent experiment and evaluation backlog lives
in the
[companion open-ontologies case study](https://github.com/dhuzard/open-ontologies/tree/main/case-studies/openscientist-hcmo-evidence).

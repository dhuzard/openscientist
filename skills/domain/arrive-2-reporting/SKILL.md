---
name: arrive-2-reporting
description: Apply ARRIVE 2.0 strictly to manuscripts and completed-study reports for mouse and rat research, producing evidence-located Essential 10 and Recommended Set matrices, gap remediation, and export-ready checklist content. Use for reporting audits and manuscript preparation; before results exist, report planning readiness rather than compliance.
metadata:
  category: domain
  slug: arrive-2-reporting
  tags:
    - arrive-2
    - reporting
    - manuscript
    - mice
    - rats
    - reproducibility
---

# ARRIVE 2.0 reporting

## Role and claim boundary

Apply the ARRIVE guidelines 2.0 to reports of in vivo mouse and rat research. The Essential 10 are the minimum reporting set; the Recommended Set adds context and represents best practice when reported together with the Essential 10.

ARRIVE governs transparent reporting. It does not by itself establish scientific validity, ethical acceptability, project authorisation, adherence to a preregistration, FAIRness, or legal compliance. For a planned study, produce an ARRIVE readiness matrix and route design decisions to the appropriate planning skill. Do not mark a future intention as a satisfied reporting item.

## Authoritative source

Use and version the official materials:

- ARRIVE guidelines 2.0 and item explanations: https://arriveguidelines.org/arrive-guidelines
- official resources, study plan, and author checklists: https://arriveguidelines.org/resources
- author checklist instructions: https://arriveguidelines.org/resources/author-checklists

When journal instructions differ, report both requirements and the conflict; do not silently weaken ARRIVE. Do not rely on recalled item wording when the official page is available.

## Complete assessment topology

Assess every item and its subitems from the current official version.

Essential 10:

1. Study design
2. Sample size
3. Inclusion and exclusion criteria
4. Randomisation
5. Blinding
6. Outcome measures
7. Statistical methods
8. Experimental animals
9. Experimental procedures
10. Results

Recommended Set:

11. Abstract
12. Background
13. Objectives
14. Ethical statement
15. Housing and husbandry
16. Animal care and monitoring
17. Interpretation and scientific implications
18. Generalisability or translation
19. Protocol registration
20. Data access
21. Declaration of interests

Never collapse subitems into a satisfied parent when one subitem is missing. Use the current official subitem identifiers and wording in the output mapping.

## Required prerequisites and evidence-first assessment

For each subitem, record:

- status: `satisfied`, `partial`, `missing`, `not_applicable`, `conflicting`, or `unassessed`;
- exact evidence location: document version plus section, page, paragraph, table, figure, supplement, repository record, or protocol identifier;
- concise evidence summary without fabricating manuscript text;
- missing elements and a proposed correction;
- source version and reviewer notes.

`Not_applicable` requires a written scientific rationale and human confirmation. Mentioned is not necessarily adequately reported. Evidence in private notes is a remediation source, not manuscript satisfaction, until incorporated into the report.

## Two-pass verification

1. **Coverage pass**: check all 21 items and every official subitem against the manuscript, supplements, protocol, analysis outputs, and repository records.
2. **Challenge pass**: test every positive finding for specificity, internal consistency, and traceability. Cross-check animal counts, groups, experimental units, exclusions, outcome definitions, sample-size rationale, randomisation, blinding, statistics, adverse events, dates, identifiers, and data links across all documents.

Preserve contradictions as `conflicting`; do not choose a value merely because it appears in the manuscript. If the official guideline version or a source document is unavailable, mark the affected rows `unassessed`.

## Reporting workflow

1. Freeze the exact manuscript and supplement versions under review.
2. Identify all experiments; reporting adequacy can differ between experiments.
3. Build the full item/subitem matrix with evidence locations.
4. Prioritize the Essential 10 gaps that prevent reliability assessment.
5. Draft bounded insertions or revision instructions using only supported facts. Use placeholders for unresolved content.
6. Re-run the full matrix after edits and record what changed.
7. Generate export-ready checklist rows, but do not claim the official PDF has been completed unless the later exporter or a human actually produced and verified it.

Use `preclinical-power-statistics` for statistical corrections, `preclinical-experimental-design` for methods and welfare facts, `preclinical-preregistration` for registration and deviations, and `fair-data-stewardship` for data access. ARRIVE remains authoritative for how these facts are reported.

## Export-ready output

Return an Essential 10 summary, Recommended Set summary, prioritized patch plan, and one JSON object:

```json
{
  "schema_version": "openscientist-arrive-2-reporting/0.1",
  "study_id": null,
  "guideline": {
    "name": "ARRIVE",
    "version": "2.0",
    "verified_at": null
  },
  "document_set": [],
  "assessment_stage": "completed_study_reporting",
  "items": [
    {
      "item_id": "1",
      "subitem_id": null,
      "set": "essential_10",
      "title": "Study design",
      "status": "unassessed",
      "evidence_locations": [],
      "gaps": [],
      "remediation": []
    }
  ],
  "manuscript_patch_plan": [],
  "checklist_rows": [],
  "source_register": [],
  "unresolved_items": [],
  "contradictions": [],
  "human_confirmations_required": []
}
```

Expand `items` to every current official item and subitem. For pre-study use, set `assessment_stage` to `planning_readiness` and ensure no future action alone receives `satisfied`.

Do not generate DOCX or PDF in this version. Keep stable item identifiers, evidence locations, and checklist rows suitable for a later DOCX/PDF exporter. A human must review final pagination, hyperlinks, table continuation, accessibility, and fidelity to any official form.

## Stop conditions

Stop and request evidence when document versions are unclear; experiments are conflated; animal numbers conflict; the experimental unit is missing; excluded animals cannot be reconciled; statistical reporting does not match the analysis; an ethics, registry, or data identifier cannot be verified; a proposed insertion would invent facts; or the user requests a compliance claim unsupported by a complete item-level review.

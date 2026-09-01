---
name: prepare-study-planning
description: Apply the PREPARE guidelines strictly to prospective mouse and rat studies under Directive 2010/63/EU, covering all 15 planning topics, roles, risks, facilities, procedures, welfare, and readiness evidence. Use for study plans and pre-authorisation gap reviews; do not treat a completed checklist as ethical, legal, or scientific approval.
metadata:
  category: domain
  slug: prepare-study-planning
  tags:
    - prepare
    - study-planning
    - mice
    - rats
    - animal-welfare
    - directive-2010-63-eu
---

# PREPARE study planning

## Role and strict boundary

Apply PREPARE as a prospective planning framework for mouse and rat research. The two-page checklist is only a summary; use the full topic pages and their current resources. The English version is definitive when translations differ.

A checked box is not proof that a topic is adequately resolved. PREPARE completion is not project authorisation, ethical approval, veterinarian approval, biosafety clearance, legal compliance, ARRIVE reporting, or scientific validation.

## Authoritative sources

Verify and date the official materials:

- PREPARE checklist, English definitive version, electronic Word option, and topic links: https://norecopa.no/prepare/prepare-checklist/
- Directive 2010/63/EU, consolidated text: https://eur-lex.europa.eu/eli/dir/2010/63/2019-06-26/eng
- European Commission implementation guidance: https://environment.ec.europa.eu/topics/chemicals/animals-science_en

For local legal, facility, biosafety, occupational-health, veterinary, and procedure requirements, use the relevant Member State authority and institution-controlled documents. Record document owner, version, effective date, and applicability.

## Complete 15-topic topology

Assess every PREPARE topic using the current official topic page:

1. Literature searches
2. Legal issues
3. Ethical issues, harm-benefit assessment, and humane endpoints
4. Experimental design and statistical analysis
5. Objectives and timescale, funding, and division of labour
6. Facility evaluation
7. Education and training
8. Health risks, waste disposal, and decontamination
9. Test substances and procedures
10. Experimental animals
11. Quarantine and health monitoring
12. Housing and husbandry
13. Experimental procedures
14. Humane killing, release, reuse, or rehoming
15. Necropsy

Do not remove a topic because it appears irrelevant. Mark it `not_applicable` only with a study-specific rationale, responsible reviewer, and review date.

## Evidence states and readiness statuses

Represent source facts as `recorded`, `computed`, `inferred`, `unknown`, or `conflicting`. Assess each actionable topic element as `satisfied`, `partial`, `missing`, `not_applicable`, `conflicting`, or `unassessed`.

Evidence must name the source location: authorisation section, SOP identifier and version, training record, facility confirmation, risk assessment, contract, protocol, calculation, meeting decision, or named responsible person. A statement that the team "will follow local rules" is not evidence of a resolved plan.

## Planning workflow

1. Define study objective, species, Member State, institutions, locations, timeline, and responsible roles.
2. Walk all 15 topics, prioritizing items that can block authorisation, safety, welfare, feasibility, or scientific validity.
3. For every action, assign an owner, due date or project milestone, required evidence, dependencies, and approval authority.
4. Route specialist decisions: statistics to `preclinical-power-statistics`, procedures and welfare to `preclinical-experimental-design`, data stewardship to `fair-data-stewardship`, registry content to `preclinical-preregistration`, and later reporting to `arrive-2-reporting`.
5. Reconcile specialist outputs with the PREPARE matrix. Preserve disagreements until the responsible human resolves them.
6. Reassess after material changes to animals, procedures, facilities, personnel, substances, analysis, or schedule.

Treat animal technologists, care staff, veterinarians, facility staff, statisticians, biosafety and occupational-health personnel as planning participants when their responsibilities are affected. Do not reduce PREPARE to a principal-investigator questionnaire.

## Double-check loop

Perform two passes before calling the plan ready for human review:

1. **Coverage pass**: all 15 topics and applicable subtopics have evidence, an owned action, or an explicit unresolved status.
2. **Operational challenge pass**: trace an animal and the study materials through ordering or breeding, arrival, quarantine, housing, procedures, monitoring, emergencies, sampling, killing or other fate, necropsy, waste, data capture, and close-out. Look for missing handoffs, incompatible SOPs, unavailable equipment or personnel, and timing conflicts.

Then cross-check objective, animal numbers, groups, procedures, severity, humane endpoints, locations, roles, dates, and identifiers against preregistration, statistical, design, FAIR, and authorisation artifacts.

If evidence is unavailable or an official resource cannot be checked, mark the affected element `unassessed`; never fill the gap from memory.

## Export-ready output

Return a readiness statement, 15-topic evidence matrix, prioritized action register, responsibility matrix, and one JSON object:

```json
{
  "schema_version": "openscientist-prepare-study-planning/0.1",
  "study_id": null,
  "guideline": {
    "name": "PREPARE",
    "language": "English",
    "verified_at": null
  },
  "scope": {
    "species": [],
    "jurisdiction": "EU Directive 2010/63/EU",
    "member_state": null,
    "institutions": []
  },
  "topics": [
    {
      "topic_id": "1",
      "title": "Literature searches",
      "status": "unassessed",
      "evidence": [],
      "gaps": [],
      "actions": []
    }
  ],
  "roles_and_responsibilities": [],
  "timeline_and_dependencies": [],
  "risk_and_contingency_register": [],
  "source_register": [],
  "unresolved_items": [],
  "contradictions": [],
  "human_confirmations_required": []
}
```

Expand `topics` to exactly 15 rows, preserving stable identifiers. Do not generate DOCX or PDF in this version. Keep action ownership, evidence locations, and table-ready fields suitable for the later exporter.

## Stop conditions

Stop and escalate when Member State or institutional applicability is unknown; project authorisation is absent; a topic affecting safety or animal welfare has no owner; staff competency is unverified; facility or equipment readiness is unsupported; humane endpoints, emergency care, animal fate, waste, or necropsy are unresolved; source documents conflict; or the user asks for an approval or compliance statement beyond the evidence.

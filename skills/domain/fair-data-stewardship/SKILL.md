---
name: fair-data-stewardship
description: Create and audit evidence-backed FAIR data plans and research-object packages for preclinical mouse and rat studies. Use for identifiers, metadata, repositories, access conditions, vocabularies, provenance, licences, formats, and FAIR principle gap reviews; do not equate FAIR with open data or issue unsupported FAIR-compliance scores.
metadata:
  category: domain
  slug: fair-data-stewardship
  tags:
    - fair
    - data-stewardship
    - metadata
    - provenance
    - reproducibility
    - preclinical
---

# FAIR data stewardship

## Role and strict boundary

Apply the FAIR Guiding Principles to the digital objects of a preclinical study: data, metadata, code, protocols, workflows, models, and supporting documentation. Assess each principle separately with inspectable evidence. FAIR means findable, accessible under explicit conditions, interoperable, and reusable by humans and machines; it does not require that restricted data be openly downloadable.

Do not produce a single FAIR percentage unless the user names a versioned assessment method and the method supports that aggregation. Never label an object `FAIR compliant`. Report item-level evidence and limitations.

## Authoritative basis

Verify the current pages and record access dates:

- GO FAIR statement of the FAIR principles: https://www.go-fair.org/fair-principles/
- original FAIR Guiding Principles: https://doi.org/10.1038/sdata.2016.18
- GO FAIR FAIRification process: https://www.go-fair.org/fair-principles/fairification-process/

The principles intentionally do not prescribe one universal implementation. Do not invent a repository, ontology, metadata schema, persistent identifier, file format, or community standard. Select implementations from the study's discipline, repository requirements, institution, funder, and intended reuse, and cite the applicable specification.

## Required inventory

Identify each digital object and its lifecycle stage. For every object record:

- owner and steward;
- canonical title, version, identifier, and relationships to other objects;
- data types, formats, volume, sensitivity, and legal or contractual restrictions;
- creation instruments, software, transformations, quality control, and provenance;
- metadata schema and controlled vocabularies;
- storage, backup, integrity checks, version control, repository, retention, and preservation;
- licence or data-use terms, access procedure, authentication, and authorisation;
- release timing, embargo, costs, responsibilities, and persistent landing page.

Separate data-level, metadata-level, and infrastructure-level evidence. A DOI alone does not satisfy all Findable requirements; a downloadable file alone does not establish Reusability.

## Principle-by-principle assessment

Assess all 15 identifiers without merging them:

- `F1`: globally unique persistent identifier for data or metadata;
- `F2`: rich metadata appropriate to the object and community;
- `F3`: metadata explicitly include the identifier of the described object;
- `F4`: data or metadata are registered or indexed in a searchable resource;
- `A1`: retrieval by identifier through a standardised protocol;
- `A1.1`: protocol is open, free, and universally implementable;
- `A1.2`: protocol supports authentication and authorisation where needed;
- `A2`: metadata remain accessible if data cease to be available;
- `I1`: formal, accessible, shared, broadly applicable knowledge-representation language;
- `I2`: vocabularies used are themselves FAIR;
- `I3`: qualified references to related data or metadata;
- `R1`: rich description with accurate and relevant attributes;
- `R1.1`: clear and accessible usage licence;
- `R1.2`: detailed provenance;
- `R1.3`: domain-relevant community standards.

Use assessment status `satisfied`, `partial`, `missing`, `not_applicable`, `conflicting`, or `unassessed`. `Not_applicable` requires a recorded rationale and human reviewer; lack of evidence is `missing` or `unassessed`, not `not_applicable`.

## Evidence states and double check

For source values, use `recorded`, `computed`, `inferred`, `unknown`, or `conflicting`. For every `satisfied` or `partial` finding, record the evidence location and test performed.

Run two independent passes:

1. **Evidence pass**: resolve identifiers, inspect metadata records, test access without assuming privileged credentials, validate formats or schemas where tooling exists, and verify licences, vocabularies, and repository policies.
2. **Challenge pass**: attempt to falsify each positive finding, look for broken identifiers, inaccessible metadata, undocumented restrictions, non-resolvable vocabulary terms, missing provenance edges, licence ambiguity, and divergence between deposited and working versions.

If live verification is unavailable, mark the affected item `unassessed`. Never transform a plan or stated intention into evidence of implementation.

## Planning workflow

1. Define intended users and reuse cases before choosing metadata and standards.
2. Inventory objects and sensitivity; apply data minimisation and lawful access controls.
3. Choose repository and persistent identifiers based on object type, preservation, access, versioning, and community practice.
4. Define metadata, semantic mappings, qualified relationships, provenance, and machine-actionable access conditions.
5. Prefer open, non-proprietary formats when fit for purpose; when proprietary raw formats are scientifically necessary, retain them and add documented interoperable derivatives where feasible.
6. Define licence or data-use agreement, release or embargo, retention, stewardship roles, validation, and change control.
7. Reassess deposited objects after publication; do not assume a plan was executed.

Hand scientific design metadata to `preclinical-experimental-design`, analysis provenance to `preclinical-power-statistics`, and registry linkage to `preclinical-preregistration`. This skill remains authoritative for FAIR evidence.

## Export-ready output

Return an object inventory, 15-row FAIR matrix, prioritized remediation plan, and one JSON object:

```json
{
  "schema_version": "openscientist-fair-data-stewardship/0.1",
  "study_id": null,
  "scope": {
    "species": [],
    "jurisdiction": "EU Directive 2010/63/EU"
  },
  "intended_reuse": [],
  "digital_objects": [],
  "fair_assessment": [
    {
      "principle_id": "F1",
      "status": "unassessed",
      "evidence": [],
      "gaps": [],
      "actions": []
    }
  ],
  "repositories_and_identifiers": [],
  "metadata_and_semantics": {},
  "access_security_and_privacy": {},
  "formats_and_validation": {},
  "licensing_and_reuse_conditions": {},
  "provenance_and_versioning": {},
  "preservation_and_responsibilities": {},
  "source_register": [],
  "unresolved_items": [],
  "contradictions": [],
  "human_confirmations_required": []
}
```

Expand `fair_assessment` to exactly one row for each of the 15 identifiers. Do not generate DOCX or PDF in this version. Preserve stable identifiers and evidence locations for a later exporter.

## Stop conditions

Stop and request resolution when a claimed identifier does not resolve; repository or metadata persistence is unknown; access conditions are undocumented; a licence is absent or incompatible; personal, confidential, or security-sensitive information would be exposed; a vocabulary or community standard is asserted without a verifiable specification; deposited and working objects conflict; or a positive finding cannot be independently supported.

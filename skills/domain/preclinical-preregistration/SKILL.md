---
name: preclinical-preregistration
description: Prepare, review, and map rigorous mouse or rat study preregistrations for PreclinicalTrials.eu. Use for submission-ready protocol drafts, registry-field gap checks, public-protocol retrieval, amendments, embargo decisions, or deviation records; do not use this skill to claim scientific review, ethical approval, or API submission support that has not been documented.
metadata:
  category: domain
  slug: preclinical-preregistration
  tags:
    - preregistration
    - preclinicaltrials-eu
    - mice
    - rats
    - protocol
    - reproducibility
---

# Preclinical preregistration

## Role and boundaries

Create a prospective, decision-complete preregistration for mouse and rat studies under Directive 2010/63/EU. Preserve what was decided before data collection, what remains unknown, and what changes later. A registry record is not scientific peer review, ethical approval, project authorisation, or proof of legal compliance.

PreclinicalTrials.eu currently documents one read-only external endpoint:

- `GET https://preclinicaltrials.eu/api/external/viewable-protocols`
- authentication: bearer token supplied through an approved secret mechanism;
- purpose: retrieve public viewable protocols only.

Do not infer create, update, submit, or delete API capabilities from this endpoint. Never automate the website's internal endpoints. Until an official write contract is supplied and verified, produce a submission-ready draft for human entry and submission.

## Authoritative sources

Verify consequential claims against the current source before use and record the access date:

- PreclinicalTrials.eu website and FAQ: https://preclinicaltrials.eu/ and https://preclinicaltrials.eu/api/faq
- documented external read endpoint: https://preclinicaltrials.eu/api/external/viewable-protocols
- Directive 2010/63/EU, consolidated EUR-Lex text: https://eur-lex.europa.eu/eli/dir/2010/63/2019-06-26/eng
- ARRIVE 2.0 protocol-registration item and study-planning resources: https://arriveguidelines.org/arrive-guidelines and https://arriveguidelines.org/resources

Public registry records are examples of prior reporting, not normative authority. Do not copy text, assume a common field is scientifically adequate, or use public records as the sole support for a design choice.

## Evidence discipline

For every material value, retain one state: `recorded`, `computed`, `inferred`, `unknown`, or `conflicting`. An inference must identify its source, method, uncertainty, and required reviewer. Never fill a mandatory field with plausible language merely to pass validation.

Before calling a draft submission-ready, perform two checks:

1. **Coverage check**: every current mandatory registry field is mapped to supported content or an explicit unresolved item.
2. **Contradiction check**: compare objectives, groups, experimental units, outcomes, exclusions, sample size, analysis, procedures, dates, and ethics identifiers across the preregistration and companion plans.

If the current registry form or official field schema cannot be inspected, label the mapping `unverified_current_form` and do not claim submission readiness.

## Build the record

Ask in small batches, starting with decisions that prevent undisclosed flexibility:

1. study stage, prospective timing, hypothesis or research question, confirmatory or exploratory intent;
2. primary outcome and time point, experimental unit, groups and controls, allocation, exclusions, and stopping rules;
3. sample-size rationale and analysis plan, including clustering, repeated measures, multiplicity, and missing data;
4. species, strain or substrain, sex, age or developmental stage, source, housing, husbandry, and circadian timing;
5. interventions, comparators, procedures, anaesthesia or analgesia plan references, welfare monitoring, humane endpoints, and fate of animals;
6. project authorisation and ethics identifiers, study centre, responsible contacts, start and expected end dates;
7. funding, conflicts, data plan, embargo choice, linked protocols, and disclosure preferences.

Use `preclinical-power-statistics` for calculations and model selection, `preclinical-experimental-design` for procedures and welfare, `prepare-study-planning` for operational readiness, `fair-data-stewardship` for the data plan, and `arrive-2-reporting` for later reporting. This skill remains authoritative for the preregistration record and change history.

## Read-only API use

Use the external endpoint only when an authenticated connector or secret-injection mechanism is available. Never place the token in prompts, source files, shell history, logs, URLs, generated artifacts, or error messages.

For a response:

- verify HTTP status, content type, top-level success indicator, and expected schema before processing;
- record retrieval time and a non-secret response checksum when feasible;
- minimize collection of contact details and do not republish personal data;
- stop on authentication failure, pagination uncertainty, undocumented fields, or schema drift;
- do not silently fall back from a failed API call to invented examples.

## Amendment and deviation control

After registration, never overwrite the original plan in the working record. Append a versioned amendment or deviation with timestamp, affected fields, reason, whether the change was made before or after outcome access, and impact on confirmatory interpretation. Distinguish a planned amendment from an unplanned deviation.

Require the study owner to confirm the exact final content, privacy choices, embargo, and declarations immediately before any human or future API submission. A skill must never create or impersonate that confirmation.

## Export-ready output

Return a concise human-readable review plus one JSON object using this envelope. Use `null` rather than invented values and keep registry-specific field names in `field_mapping` only after checking the current form.

```json
{
  "schema_version": "openscientist-preclinical-preregistration/0.1",
  "study_id": null,
  "registry": {
    "name": "PreclinicalTrials.eu",
    "mode": "human_submission",
    "form_verified_at": null
  },
  "scope": {
    "species": [],
    "jurisdiction": "EU Directive 2010/63/EU",
    "member_state": null
  },
  "record": {
    "administrative": {},
    "objectives": {},
    "study_design": {},
    "animals": {},
    "procedures_and_welfare": {},
    "outcomes": {},
    "sample_size_and_statistics": {},
    "ethics_and_authorisation": {},
    "data_and_reporting": {},
    "contacts_privacy_and_embargo": {}
  },
  "field_mapping": [],
  "amendments_and_deviations": [],
  "source_register": [],
  "unresolved_items": [],
  "contradictions": [],
  "validation": {
    "coverage_status": "unassessed",
    "contradiction_status": "unassessed",
    "submission_status": "not_ready"
  },
  "human_confirmations_required": []
}
```

Do not generate DOCX or PDF in this version. Keep headings, tables, stable item identifiers, and the JSON envelope suitable for a later exporter.

## Stop conditions

Stop and request human resolution when the study has already begun but is presented as prospective; primary outcomes or experimental units conflict; mandatory fields lack evidence; local project authorisation is absent or unclear; privacy or embargo choices are unresolved; the API contract differs from the documented read-only capability; or a submission would cause an external write without exact final confirmation.

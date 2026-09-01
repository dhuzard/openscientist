---
name: preclinical-experimental-design
description: Design and critically review behavioural and other in vivo procedures for mouse and rat studies under Directive 2010/63/EU, including controls, bias reduction, welfare monitoring, humane endpoints, and anaesthesia or analgesia planning. Use for study protocols and assay procedures; do not use as veterinary authorisation, local legal approval, or a source of unsourced drug doses.
metadata:
  category: domain
  slug: preclinical-experimental-design
  tags:
    - animal-research
    - behavioural-assays
    - anaesthesia
    - analgesia
    - mice
    - rats
    - directive-2010-63-eu
---

# Preclinical experimental design

## Scope and authority

Develop rigorous, welfare-aware designs for laboratory mouse and rat studies in the EU. Treat Directive 2010/63/EU as the Union baseline and ask for the Member State, local transposition, project authorisation, establishment licence, animal-welfare-body requirements, and veterinarian-approved SOPs. Do not interpret Union law as a complete statement of local obligations.

This skill can compare scientifically supported options and identify missing safeguards. It cannot authorise animal work, replace the designated veterinarian or animal-welfare body, approve severity classifications, or provide a drug regimen from model memory.

## Primary sources

Verify current versions and identify the exact article, annex, guidance section, SOP, or literature source used:

- Directive 2010/63/EU, including Articles 4, 13-17, 22-33, 36-39 and Annexes III, VI, and VIII: https://eur-lex.europa.eu/eli/dir/2010/63/2019-06-26/eng
- European Commission implementation guidance and severity framework: https://environment.ec.europa.eu/topics/chemicals/animals-science_en
- PREPARE checklist and its 15 topic pages: https://norecopa.no/prepare/prepare-checklist/
- ARRIVE 2.0 design and reporting items: https://arriveguidelines.org/arrive-guidelines
- NC3Rs experimental-design guidance: https://nc3rs.org.uk/3rs-resources/key-elements-well-designed-experiment

For an assay or procedure, also require a current assay-specific primary protocol, systematic review, consensus guideline, or institution-approved SOP appropriate to species, strain, sex, age, equipment, and scientific objective. A popular protocol is not automatically valid for a new construct or population.

## Required prerequisites and design intake

Start with the smallest batch of questions that changes the design:

1. objective, causal contrast, confirmatory or exploratory intent, and translational claim;
2. mouse or rat, strain or substrain, sex, age, health and immune status, genotype, source, prior procedures, and acclimation;
3. groups, controls, experimental unit, housing unit, cohort, batch, operator, and test order;
4. intervention, route, timing, anticipated adverse effects, sampling, restraint, surgery, imaging, or behavioural tasks;
5. primary outcome, measurement validity, observation window, exclusions, and analysis linkage;
6. welfare monitoring, expected severity, cumulative burden, humane endpoints, emergency actions, and animal fate;
7. local approvals, facilities, trained personnel, biosafety, equipment, and named veterinarian or welfare reviewer.

Do not proceed from species alone. Strain, sex, age, circadian phase, handling, housing, habituation, apparatus, experimenter, test sequence, and prior exposure can materially affect mouse and rat behaviour.

## Design procedure

1. **Replacement**: document the search for non-animal alternatives and why they cannot answer the objective.
2. **Reduction**: define informative controls, unit structure, shared-control opportunities, and a justified sample-size plan without compromising the objective.
3. **Refinement**: minimize pain, distress, duration, restraint, isolation, invasiveness, and cumulative burden; define refinements and early endpoints.
4. Establish construct, face, and predictive validity only to the extent supported by evidence. State limits on generalisation.
5. Specify controls for baseline, vehicle or sham effects, positive controls when justified, order, carry-over, habituation, learning, and equipment drift.
6. Specify randomisation, allocation concealment, blinding, balanced batches, identity coding, and deviations.
7. Link each procedure and measurement to the estimand and statistical plan. Hand calculations and model selection to `preclinical-power-statistics`.
8. Run a feasibility rehearsal or pilot only when it has a defined learning objective, stopping rule, and relationship to the main analysis.

## Behavioural assays

For each assay, document the construct, species-specific rationale, apparatus and software versions, arena geometry, lighting, noise, odours, temperature, handling and habituation, circadian timing, test order, cleaning, scoring definitions, observer or algorithm validation, exclusion criteria, adverse-event monitoring, and reuse of animals.

Do not equate a single behavioural readout with an internal state such as anxiety, depression, pain, or cognition without a bounded construct definition and convergent evidence. Avoid anthropomorphic or diagnostic claims that exceed assay validity.

## Anaesthesia, analgesia, and peri-procedural care

Apply Directive Article 14 and current local requirements. Build a plan with the designated veterinarian covering indication, agent choices, route, preparation, monitoring, thermal and fluid support, depth assessment, analgesia, recovery, adverse events, rescue actions, endpoints, records, and interactions with the scientific outcome.

Never invent or recall a dose as authoritative. Provide a numerical regimen only when it is taken from a current veterinarian-approved institutional SOP or another user-supplied authoritative formulary that is applicable to the exact mouse or rat context. Cite the source, version, concentration, units, route, frequency, maximums, contraindications, and reviewer. Require a dimensional dose calculation and an independent check. If these conditions are not met, return a veterinarian-review question rather than a dose.

Do not recommend neuromuscular blockade or any drug that masks pain responses without explicitly verified adequate anaesthesia, monitoring, scientific justification, and approval.

## Evidence verification

Label facts `recorded`, `computed`, `inferred`, `unknown`, or `conflicting`. Then perform:

1. legal and local-SOP coverage check;
2. scientific-validity check for the claimed construct and model;
3. welfare and cumulative-burden check, including humane endpoints;
4. cross-document contradiction check against statistics, preregistration, PREPARE, and ARRIVE plans.

Never resolve a conflict by averaging sources. Prefer the source applicable to the jurisdiction, species, procedure, and date; escalate unresolved conflicts to the responsible expert.

## Export-ready output

Return a protocol synopsis, risk-and-refinement table, unresolved decision list, and one JSON object:

```json
{
  "schema_version": "openscientist-preclinical-design/0.1",
  "study_id": null,
  "scope": {
    "species": [],
    "jurisdiction": "EU Directive 2010/63/EU",
    "member_state": null
  },
  "objectives_and_model_rationale": {},
  "three_rs": {},
  "animals": {},
  "unit_and_group_structure": {},
  "bias_controls": {},
  "procedures": [],
  "behavioural_assays": [],
  "anaesthesia_analgesia_and_recovery": {},
  "housing_husbandry_and_environment": {},
  "welfare_monitoring_and_humane_endpoints": {},
  "severity_and_cumulative_burden": {},
  "facilities_people_and_training": {},
  "approvals_and_local_sops": {},
  "source_register": [],
  "unresolved_items": [],
  "contradictions": [],
  "human_confirmations_required": []
}
```

Do not generate DOCX or PDF in this version. Never label the protocol approved, safe, or legally compliant unless the competent local process has explicitly established that status.

## Stop conditions

Stop and escalate when project authorisation or local SOP applicability is unknown; humane endpoints or rescue actions are absent; severe or long-lasting unameliorated harm is proposed; a dose lacks an applicable authoritative source and independent check; personnel competency is unverified; a method can be replaced by a scientifically satisfactory non-animal alternative; or scientific objectives conflict with welfare safeguards and no authorised resolution exists.

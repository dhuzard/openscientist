---
name: create-job-brief
description: Draft, refine, or audit an OpenScientist job's Research Question and Study Context/Description as a rigorous scientific brief. Use when creating a job, improving a vague request such as "analyze these data," mapping multiple uploaded files, defining experimental units or estimands, setting scientific boundaries and deliverables, or checking at iteration 1 whether missing design metadata must narrow or block an analysis.
metadata:
  category: workflow
  slug: create-job-brief
  tags:
    - job-creation
    - research-question
    - study-design
    - experimental-unit
    - estimand
---

# Create Job Brief

Turn a scientific request and its uploaded files into an explicit analysis
contract. Keep the Research Question concise and place the detailed study
design, file roles, constraints, and output contract in the Description.

## Produce the brief

1. Identify the decision or scientific objective. State what the result should
   help decide, distinguish, estimate, validate, or explain.
2. Name the independent experimental unit. Keep technical observations such as
   timestamps, electrodes, repeated measurements, fields of view, or cells
   separate from biological or operational replicates.
3. Map every input file to its role, measurement unit, identifiers, and join
   keys. Preserve uncertainty about a file instead of guessing from its name.
4. Define the primary estimand or outcome: comparison, population, time window,
   normalization, reference, metric, and units as applicable. Keep secondary
   exploratory questions separate.
5. Record known design metadata: groups or exposures, intervention and baseline
   timing, schedule and timezone, acquisition or software version, expected
   replicate counts, exclusions, and disturbances.
6. State scientific boundaries. Name unsupported interpretations, causal or
   individual-level claims, silent exclusions, unapproved imputations, and
   label-derived assumptions that are out of scope.
7. Define success and stopping criteria. Specify minimum QC, estimates,
   uncertainty, sensitivity analyses, tables, figures, and provenance. Require
   exact blockers and the smallest missing information needed to continue.

## Preserve epistemic status

- Mark each consequential item as supplied, derived, proposed, or unknown.
- Never turn an example, placeholder, filename, or opaque label into study
  metadata.
- Separate measured observations, statistical associations, mechanistic
  hypotheses, and causal conclusions.
- When a required item is missing, distinguish analyses that remain safe from
  analyses that must stop. Do not broaden a narrow exploratory objective.
- Do not include credentials, secrets, unnecessary personal identifiers, or
  sensitive free text in a job prompt because job questions may be logged.

## Format the result

Return these sections when drafting or refining a job:

1. **Research Question** — one concise objective suitable for the UI field.
2. **Study Context / Description** — the structured brief using the template
   below.
3. **Open items** — only consequential unknowns, each with the decision or
   analysis it blocks. Omit this section when there are none.

Do not fill the brief with generic instructions already supplied by the
runtime, such as telling the agent to use tools, inspect data, search
literature, save iteration summaries, or write a final report. Include a
method only when it is prespecified or scientifically required; otherwise
state the estimand and let QC determine the defensible method.

When auditing an active job at iteration 1, extract the same contract from the
Research Question, Description, files, and approved evidence plan. If it is
complete, proceed without rewriting it. If it is incomplete, record the gaps,
continue only safe QC or exploratory work, and stop any analysis whose validity
depends on missing information.

## Reusable template

```text
Objective:
Determine whether [scientific/operational question] to support [decision].

Data:
- [filename]: [role, unit, important identifiers]
- [filename]: [metadata or event role]
Join files using [keys]. Expected independent units: [count and type].

Study design:
- Experimental unit: [...]
- Groups/exposures: [...]
- Relevant timing, baseline, timezone, or schedule: [...]
- Primary outcome/metric and units: [...]
- Known exclusions or disturbances: [...]

Primary analysis:
Estimate/compare [precise estimand] over [window/population].
Treat [analysis choice] as primary and [other choice] as sensitivity.

Secondary exploratory questions:
1. [...]
2. [...]

Constraints:
Do not [unsupported inference].
Do not silently exclude or impute observations.
If required metadata or reconciliation fails, stop that analysis and report the
exact blocker and the smallest information needed to continue.

Required output:
- Data and metadata QC
- Inclusion/exclusion accounting
- Primary estimate with uncertainty
- Sensitivity analysis
- Reproducible figures/tables
- Clear separation of observations, associations, and hypotheses
```

## DVC-oriented example

**Research Question**

```text
Do cage-level Tecniplast DVC locomotor activity profiles differ between the
recorded study groups?
```

**Study Context / Description**

```text
Perform an exploratory cage-level characterization of Tecniplast DVC locomotor
activity and determine whether activity profiles differ between the recorded
study groups.

The physical cage is the experimental unit. Do not treat timestamps, electrodes,
or animals housed in a cage as independent replicates.

Files:
- Type1_*.csv: electrode-level ALI-smoothed export
- Type2_*.csv: cage and vendor group summaries
- events.csv: acquisition and husbandry events
- metadata.json: cage mapping, group assignments, occupancy, local timezone,
  light schedule, and Analytics version

Reconcile the expected and observed cage counts before analysis. Validate Type 2
cage summaries against Type 1 where possible. The primary objective is descriptive
cage-level activity QC and group profile comparison; causal and per-animal claims
are out of scope.

Use only verified local light schedules for biological-day or light/dark analysis.
Do not infer group meaning from cage labels or silently remove event-associated
intervals. Report missing metadata and failed acceptance gates as blockers.

Deliver cage reconciliation, coverage/gap QC, included and excluded cage-days
with reasons, primary cage-first profiles with uncertainty, clearly named
sensitivity analyses, and an evidence-linked reproducibility summary.
```

## Acceptance check

Before returning or relying on the brief, verify that it:

- states a decision-oriented objective rather than only "analyze the data";
- names the experimental unit and expected count when known;
- assigns a role and linkage to every uploaded file;
- separates the primary estimand from exploratory questions;
- records consequential design metadata and unknowns;
- prohibits unsupported inference and silent data handling;
- defines minimum deliverables, uncertainty, and stopping behavior; and
- contains no invented metadata, secrets, or unnecessary personal data.

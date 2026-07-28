# Evidence Librarian

The Evidence Librarian is a human-approved preflight layer for OpenScientist
jobs. It connects three concerns that were previously independent:

1. identifying which enabled skills are applicable to the research question
   and submitted file types;
2. preparing a reproducible bibliographic search strategy; and
3. giving the discovery agent an explicit order, conflict policy, and trace
   contract for combining those skills and sources.

It does not install or publish skills. Repository synchronization remains an
administrator-controlled operation, and the Skill Creator continues to export
drafts for review.

## Researcher workflow

On the **Submit Discovery Job** page:

1. Enter the research question and upload any data.
2. Leave **Evidence Librarian** enabled.
3. Select **Prepare evidence plan**.
4. Review the proposed workflow and domain skills and the PubMed queries.
5. Select or clear optional domain skills. Workflow skills remain mandatory.
6. Select **Approve plan**, then start the discovery job.

Changing the research question or uploaded file set makes the plan stale. The
page requires a new review and approval before starting.

## Runtime contract

An approved plan is stored in two forms:

- `.openscientist/evidence_plan.json` is the machine-readable, schema-versioned
  plan and approval record.
- `EVIDENCE_PLAN.md` is the agent-readable execution contract.

At workspace preparation time, only the approved domain skills and mandatory
workflow skills are materialized for the job. Jobs without a plan retain the
legacy behavior and receive all enabled skills.

The first activation event is recorded in
`provenance/evidence_librarian/trace.jsonl`. The plan also instructs the
discovery agent to record literature identifiers, consequential skill use,
conflicts, uncertainty, and deviations.

## Selection model

The initial implementation is deterministic and inspectable. It ranks skills
using token overlap between the question, file-derived domain hints, skill
name/category, description, and tags. Workflow skills are mandatory. Matching
domain skills are recommended, with a default maximum of five.

This deliberately avoids allowing an opaque model decision to deploy skills.
An LLM can be added later to propose query expansion or explain conflicts, but
its output should remain a proposal inside this approval and provenance
boundary.

## Safety and governance

- Only an authenticated researcher can approve a plan.
- Draft plans cannot be persisted into a job.
- A plan selects from already enabled skills; it cannot install new ones.
- Mandatory workflow skills cannot be removed in the UI or filtering layer.
- Missing or invalid plans fall back to the existing all-skills behavior.
- Global skill publication remains separate from job execution.

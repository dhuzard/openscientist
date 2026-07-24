# Create a professional OpenScientist skill

OpenScientist skills are Markdown instructions that give the discovery agent
specialized scientific knowledge or a repeatable research workflow. This
tutorial explains the format OpenScientist currently ingests, how to test a
skill, and what reviewers should look for.

This is an initial community authoring guide. The
[maintainer input requested](#maintainer-input-requested) section lists
conventions that the project has not yet formalized.

## Understand the skill lifecycle

OpenScientist does not read files directly from `skills/` during an
investigation. The application:

1. discovers `SKILL.md` files in an enabled local or GitHub skill source;
2. parses their YAML frontmatter and Markdown body;
3. stores enabled skills in the database; and
4. materializes them in the format required by the selected agent backend.

All enabled skills are made available to every job. The discovery instructions
tell the agent to read every `workflow` skill and the `domain` skills that match
the submitted data. Choose one of those established categories unless the
agent-selection behavior is also being changed.

## Start from concrete use cases

Before writing instructions, define:

- two or three requests or datasets that should cause an agent to use the
  skill;
- the decisions the skill should improve;
- the expected outputs or recorded evidence;
- relevant tool, data, and environment prerequisites; and
- cases where the skill should not be applied.

A skill is most valuable when it captures non-obvious scientific or procedural
knowledge. Avoid repeating general advice that the agent can infer reliably.

## Choose how to distribute the skill

### Built-in skill

Contribute a broadly useful skill to this repository:

```text
skills/
├── workflow/
│   └── <skill-slug>/
│       └── SKILL.md
└── domain/
    └── <skill-slug>/
        └── SKILL.md
```

Use `workflow` for domain-independent research methods and `domain` for
scientific expertise that applies only to matching data or questions.
Review the existing
[`hypothesis-generation`](../skills/workflow/hypothesis-generation/SKILL.md)
workflow skill and
[`metabolomics`](../skills/domain/metabolomics/SKILL.md) domain skill for
repository examples. They predate this guide, so treat the requirements below
as the proposed standard for new contributions.

### External skill source

Maintain organization-specific or independently versioned skills in a local
directory or GitHub repository. Each skill must still be stored in its own
directory as `<skill-slug>/SKILL.md`.

An OpenScientist administrator can register the source through
`POST /api/v1/skills/sources` and trigger an immediate import through
`POST /api/v1/skills/sources/{source_id}/sync`. GitHub sources may set
`skills_path` to the subdirectory containing the skill directories.

For example:

```json
{
  "name": "Community scientific skills",
  "source_type": "github",
  "url": "https://github.com/example/scientific-skills",
  "branch": "main",
  "skills_path": "skills"
}
```

Source management requires administrator access. Enabled sources are also
synchronized by the background scheduler.

## Create `SKILL.md`

Use a lowercase, hyphenated directory name that communicates the capability.
Keep the combined `<category>--<slug>` identifier at or below 64 characters so
it remains portable to the Codex backend.

Start with this template:

```markdown
---
name: Example Quality Control
description: Evaluate example assay quality, identify invalid measurements, and record exclusions. Use for example assay tables before statistical analysis.
category: domain
slug: example-quality-control
tags:
  - quality-control
  - example-assay
---

# Example Quality Control

## Preconditions

- Confirm that the input contains ...
- Stop and report the missing prerequisite when ...

## Workflow

1. Inspect ...
2. Calculate ...
3. Compare ...
4. Record ...

## Interpretation

- Treat ... as evidence of ...
- Do not conclude ... unless ...

## Report

- Report the method, thresholds, exclusions, and limitations.
```

### Frontmatter fields

| Field | Requirement | Guidance |
| --- | --- | --- |
| `name` | Required | Use a concise human-readable name. |
| `description` | Recommended | State both the capability and the situations that should trigger it. Keep it on one logical line and at most 1,024 characters for backend portability. |
| `category` | Recommended | Use `workflow` or `domain` for the current discovery behavior. If omitted, OpenScientist derives it from the immediate parent directory. |
| `slug` | Optional | Use a stable lowercase, hyphenated identifier. If omitted for `SKILL.md`, OpenScientist derives it from the parent directory. |
| `tags` | Optional | Provide a YAML list of precise search terms. |

The Markdown after the closing `---` is the instruction body stored in the
database and delivered to the agent.

## Write reliable instructions

### Keep the scope explicit

State required inputs, supported data types, assumptions, and stopping
conditions. Explain how to respond when prerequisites are missing instead of
letting the agent improvise silently.

### Match precision to risk

- Use principles and heuristics where several scientifically valid approaches
  exist.
- Give ordered steps where sequence matters.
- Specify exact checks, formulas, thresholds, or tool calls where deviation
  could invalidate a result.

Explain the scientific justification for consequential thresholds. Separate
project policy from claims supported by literature.

### Make results reproducible

Tell the agent to preserve:

- input identifiers and versions;
- parameters, units, filters, and transformations;
- software or database versions when they affect interpretation;
- intermediate quality-control evidence;
- negative and inconclusive results; and
- uncertainty, limitations, and alternative explanations.

Prefer primary sources for scientific claims. Include stable citations in the
skill when a method depends on a particular standard or publication, and say
when the agent should search for newer evidence.

### Design for safe failure

Do not include credentials, private data, or instructions that weaken the
execution sandbox. Identify destructive or irreversible operations and require
appropriate confirmation. Require the agent to report tool failures and data
quality problems rather than fabricate or infer missing results.

### Keep the context efficient

Include only information needed to perform the workflow. Prefer compact
examples over long background sections, and avoid duplicating the same
instructions in several places.

At present, OpenScientist source ingestion imports only files named
`SKILL.md`. It does not deliver sibling reference files, scripts, or assets to
the job. Keep a distributed skill self-contained until resource-bundle support
is defined.

## Validate the skill

### Parse the new file

From the repository root, replace the example path and run:

```bash
uv run python -c "from pathlib import Path; from openscientist.skill_ingestion import SkillParser; p = Path('skills/domain/example-quality-control/SKILL.md'); print(SkillParser().parse_file(p))"
```

This catches missing frontmatter, invalid YAML, and missing required metadata.

### Run the ingestion tests

With the development database configured as described in
[`CONTRIBUTING.md`](../CONTRIBUTING.md), run:

```bash
uv run pytest tests/test_skill_ingestion.py
```

The built-in integration test parses and imports every repository skill.

### Exercise realistic cases

Test the skill on at least:

1. a typical case it should handle;
2. an edge case with incomplete or poor-quality data; and
3. an out-of-scope case it should decline or redirect.

Review whether the agent followed the method, preserved evidence, expressed
uncertainty, and avoided unsupported conclusions. Revise the instructions when
success depends on context that exists only in the author's head.

## Submission checklist

Before opening a pull request, confirm that:

- [ ] the skill has concrete motivating use cases;
- [ ] its directory and slug use lowercase hyphenated names;
- [ ] its category matches the current agent-selection behavior;
- [ ] its description states what it does and when it applies;
- [ ] prerequisites, workflow, interpretation, and reporting are actionable;
- [ ] scientific claims and consequential thresholds are justified;
- [ ] failure modes, uncertainty, and out-of-scope cases are covered;
- [ ] no secrets, sensitive data, or unsafe operational instructions are
      included;
- [ ] the parser and ingestion tests pass; and
- [ ] the pull request describes the realistic cases used to evaluate it.

## Maintainer input requested

Community authors would benefit from project decisions and examples for:

- the long-term category taxonomy and how non-`workflow`/`domain` categories
  should be selected by agents;
- a supported scaffolding and standalone validation command;
- whether and how references, scripts, assets, and other bundled resources
  should be synchronized;
- scientific review expectations, citation policy, and recommended evaluators;
- security review for skills that invoke tools or process sensitive data;
- versioning, deprecation, provenance, and conflict handling across sources;
- ownership and trust requirements for external community sources; and
- a canonical example of a production-quality workflow skill and domain skill.

Maintainers are invited to amend this guide with those policies. Until then,
contributors should describe any necessary assumptions and evaluation evidence
in their pull requests.

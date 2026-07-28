# Create a reliable OpenScientist skill

An OpenScientist skill is a compact operating procedure for the discovery
agent. A useful skill improves a repeated scientific decision: it tells the
agent when the procedure applies, what evidence it needs, what to do, when to
stop, and what to report.

This guide separates three different kinds of statement:

- **Runtime requirement** — enforced by the current OpenScientist code.
- **Repository convention** — needed for predictable behavior in this project.
- **Authoring recommendation** — a quality practice supported by the corpus
  review or an external skill-authoring standard.

The evidence behind these statements, including the complete corpus inventory
and known limitations, is recorded in the
[Skill Authoring Knowledge Base](SKILL_AUTHORING_KNOWLEDGE_BASE.md).

## Use the interactive Skill Creator

Authenticated users can open **Skills → Create skill** (`/skills/create`) to
work through the same standard:

1. define the task contract;
2. generate or refine an editable draft with the configured model;
3. run deterministic syntax, portability, and safety checks;
4. review the scientific claims and any model questions;
5. explicitly accept the exact current draft; and
6. download `SKILL.md`.

The page never installs or publishes the draft. Editing invalidates acceptance,
and blocking syntax or secret checks prevent export. Repository review or an
administrator-managed source remains a separate publication decision.

Generation uses the configured provider's direct text-completion API and does
not expose agent tools. Direct Claude-compatible API configurations and
OpenAI-compatible API-key/proxy configurations are supported. Anthropic
Claude-Code OAuth-only and OpenAI Codex OAuth-only authentication are
intentionally not copied into the authoring container; configure the
corresponding API key (and `OPENSCIENTIST_MODEL` for direct OpenAI), or edit and
validate the starter draft manually. Provider-specific errors are shown without
discarding the current draft.

## Start with a task contract

Before drafting Markdown, write down:

1. **Purpose** — the decision or result this skill improves.
2. **Triggers** — two or three concrete requests or data situations where it
   should be used.
3. **Non-triggers** — nearby cases where it should not be used.
4. **Inputs and prerequisites** — required data, metadata, tools, and
   assumptions.
5. **Output contract** — the evidence, decision, or report the agent must
   produce.
6. **Failure behavior** — what to preserve and report when data or tools are
   missing.
7. **Human checkpoints** — actions that are destructive, costly, sensitive, or
   irreversible.

This task contract is more valuable than a long background essay. It creates
observable criteria for both reviewers and tests.

## Choose the category

OpenScientist currently gives special discovery semantics to two categories:

- `workflow`: methodology that applies across investigations. The discovery
  prompt tells the agent to read every enabled workflow skill.
- `domain`: expertise relevant to a particular scientific data type or
  question.

Use another category only if the corresponding selection behavior is also
implemented. All enabled skills are currently materialized for every job, so
keep each skill narrow and avoid conflicting global rules.

## Create `SKILL.md`

Only files named `SKILL.md` are ingested. Put each skill in a lowercase,
hyphenated directory:

```text
skills/
├── workflow/
│   └── <skill-slug>/
│       └── SKILL.md
└── domain/
    └── <skill-slug>/
        └── SKILL.md
```

Use this self-contained starting point:

```markdown
---
name: Example Quality Control
description: Evaluate example assay quality and record justified exclusions. Use before statistical analysis of example assay tables.
category: domain
slug: example-quality-control
tags:
  - quality-control
  - example-assay
---

# Example Quality Control

## Use when

- The input is an example assay table with ...

## Do not use when

- The measurements have already been ...

## Preconditions

- Require ...
- If ... is missing, stop and report ...

## Workflow

1. Inspect ... and record ...
2. Calculate ... using ...
3. Compare ... while accounting for ...
4. Ask the user to confirm before ...
5. Record the result and the evidence that supports it.

## Interpretation

- Treat ... as an observation, not proof of ...
- Do not infer ... unless ...

## Report

- Report inputs, versions, parameters, exclusions, uncertainty, limitations,
  and unresolved questions.
```

### Frontmatter contract

| Field | Current behavior | Authoring guidance |
| --- | --- | --- |
| `name` | Required, non-empty | Use a concise human-readable name. |
| `description` | Optional to the parser | State both capability and concrete trigger situations. Keep it within 1,024 characters for Codex portability. |
| `category` | Derived from the parent directory if omitted | Write `workflow` or `domain` explicitly. |
| `slug` | Derived and sanitized if omitted | Write a stable lowercase-hyphenated value explicitly. |
| `tags` | Optional list or scalar | Use precise retrieval terms, not broad labels. |

Keep `<category>--<slug>` at most 64 characters. The Codex adapter truncates
longer identities. Do not use slashes, `..`, drive prefixes, or other path
characters in explicit category or slug values.

## Write the procedure

### Optimize the description for selection

The description is the routing surface. Say what the skill does and when it
should activate:

> Evaluate replicate-aware differential expression and preserve sample-level
> evidence. Use for bulk or single-cell RNA-seq comparisons with biological
> replicates.

Avoid descriptions that only repeat the title. Include distinctive data types,
tasks, and trigger phrases. The
[Agent Skills guidance on descriptions][agent-skills-descriptions] likewise
recommends evaluating descriptions against queries that should and should not
trigger the skill.

### Use progressive disclosure

Keep the main procedure concise. Put high-value instructions before explanatory
background. Although the broader
[Agent Skills specification][agent-skills-spec] supports `references/`,
`scripts/`, and `assets/`, current OpenScientist ingestion transports only
`SKILL.md`. Until bundle support exists, an OpenScientist skill must remain
self-contained.

### Match precision to risk

- Use principles where several methods can be scientifically valid.
- Use ordered steps where sequence matters.
- Specify exact formulas, thresholds, and tool calls where deviation can
  invalidate the result.
- Label fixed project policy separately from literature-supported scientific
  claims.

A good instruction constrains fragile choices without removing appropriate
scientific judgment.

### Preserve epistemic status

Require the agent to distinguish:

- measured observations;
- statistical associations;
- mechanistic hypotheses;
- heuristics or proxies; and
- causal conclusions.

Non-significance is not proof that a hypothesis is false. A metabolite ratio is
not direct flux without an appropriate tracer or kinetic design. Encode these
boundaries directly when they matter to the skill.

### Make the work reproducible

Tell the agent to record:

- input identifiers, versions, units, and biological replicate structure;
- filters, transformations, parameters, and software or database versions;
- intermediate quality-control evidence;
- negative, inconclusive, and conflicting results;
- uncertainty, limitations, and alternative explanations; and
- citations for consequential thresholds or methodological claims.

Prefer primary literature and authoritative technical documentation. Never
invent citations. Say when claims require a fresh literature search.

### Fail safely

Require an explicit response to missing prerequisites, tool failures,
unsupported formats, and out-of-scope requests. Do not embed credentials,
private data, contributor-specific absolute paths, or instructions that weaken
the sandbox. Require human confirmation before consequential external actions.

## Evaluate behavior, not prose

Create a small evaluation set before submitting the skill:

| Case | What it checks |
| --- | --- |
| Two normal trigger cases | The skill is selected and completes its output contract. |
| Two near-miss cases | The skill is not selected unnecessarily. |
| One incomplete-input case | Missing prerequisites produce a useful stop or clarification. |
| One adversarial or ambiguous case | Guardrails hold and uncertainty remains visible. |
| One regression case | The scientific error most likely to recur stays fixed. |

Review two layers separately:

1. **Selection:** Did the agent choose the skill at the right time?
2. **Execution:** Did it follow the procedure and produce the required evidence?

The repository currently has parser and ingestion tests, but no behavioral skill
evaluation harness. Record the cases and observed results in the pull request
until that harness exists.

## Validate locally

Parse the file:

```bash
uv run python -c "from pathlib import Path; from openscientist.skill_ingestion import SkillParser; p = Path('skills/domain/example-quality-control/SKILL.md'); print(SkillParser().parse_file(p))"
```

Run ingestion tests:

```bash
uv run pytest tests/test_skill_ingestion.py
```

Then run the behavioral cases with the providers the skill is expected to
support. Parser success only proves that the file can be ingested.

## Review checklist

- [ ] The task contract names triggers, non-triggers, inputs, outputs, and
      failure behavior.
- [ ] `SKILL.md` has valid frontmatter and safe explicit identifiers.
- [ ] The description says what the skill does and when it applies.
- [ ] The category matches current discovery behavior.
- [ ] The procedure is ordered, testable, and self-contained.
- [ ] Consequential scientific claims and thresholds have sources.
- [ ] Observations, associations, hypotheses, proxies, and causal claims remain
      distinct.
- [ ] Provenance, uncertainty, negative results, and limitations are preserved.
- [ ] Human confirmation protects consequential actions.
- [ ] No secrets, sensitive data, unsafe paths, or unavailable bundled
      resources are present.
- [ ] Selection and execution cases were tested, including a near miss and a
      failure case.

## Further standards

- [Agent Skills specification][agent-skills-spec]
- [Agent Skills authoring best practices][agent-skills-practices]
- [Agent Skills description optimization][agent-skills-descriptions]
- [OpenAI Academy: Skills][openai-skills]
- [Anthropic: Equipping agents for the real world with Agent Skills][anthropic-skills]

[agent-skills-spec]: https://agentskills.io/specification
[agent-skills-practices]: https://agentskills.io/skill-creation/best-practices
[agent-skills-descriptions]: https://agentskills.io/skill-creation/optimizing-descriptions
[openai-skills]: https://openai.com/academy/skills/
[anthropic-skills]: https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills

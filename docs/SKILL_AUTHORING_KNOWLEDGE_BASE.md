# Skill Authoring Knowledge Base

This document is the evidence trace behind
[`CREATING_SKILLS.md`](CREATING_SKILLS.md) and the interactive Skill Creator.
It records what the current runtime enforces, what the repository corpus
demonstrates, where the corpus conflicts, and which recommendations are
external best practices rather than current product behavior.

## Method and evidence labels

The review covered every file under `skills/` on 2026-07-28:

- 10 `SKILL.md` files;
- 4 structural-biology Markdown support files;
- 2 reference documents; and
- 9 shell scripts.

Each conclusion below is labeled:

- **Observed** — directly present in code or a corpus file.
- **Assessment** — an engineering or scientific judgment derived from observed
  evidence.
- **Recommendation** — a proposed standard, not an existing runtime guarantee.

Line links are repository-relative traces. They may move as the referenced
files evolve; the surrounding symbol or section name is the durable locator.

## Runtime trace

| ID | Evidence | Finding |
| --- | --- | --- |
| R1 | [`SkillParser.FRONTMATTER_PATTERN`](../src/openscientist/skill_ingestion.py#L117), [`parse_content`](../src/openscientist/skill_ingestion.py#L138) | **Observed:** frontmatter must start the file, parse as a YAML mapping, and contain a truthy `name`. Description and body may be empty. |
| R2 | [Local discovery](../src/openscientist/skill_ingestion.py#L647), [GitHub discovery](../src/openscientist/skill_ingestion.py#L422) | **Observed:** only files named `SKILL.md` are ingested. |
| R3 | [Discovery prompt](../src/openscientist/prompts/common.py#L63), [skill query](../src/openscientist/prompts/common.py#L622) | **Observed:** all enabled skills are made available; all workflow skills are mandatory and domain skills are selected by relevance. |
| R4 | [Codex materialization](../src/openscientist/agent/skills.py#L74) | **Observed:** Codex reconstructs frontmatter, truncating `category--slug` to 64 characters and description to 1,024 characters. |
| R5 | [Skills settings](../src/openscientist/settings.py#L832), [skill query](../src/openscientist/prompts/common.py#L622) | **Observed:** `MAX_AGENT_SKILLS` exists, but the query does not apply it. |
| R6 | [Default source configuration](../src/openscientist/web_app.py#L362), [scheduler query](../src/openscientist/skill_scheduler.py#L150) | **Observed:** the default GitHub source tracks `main`, and enabled sources are synchronized automatically. |
| R7 | [`parse_content`](../src/openscientist/skill_ingestion.py#L138), [Codex path construction](../src/openscientist/agent/skills.py#L90) | **Assessment:** explicit category and slug values need validation before path materialization. The parser currently accepts path characters verbatim. |
| R8 | [Skills API](../src/openscientist/api/endpoints/skills.py), [skills page](../src/openscientist/webapp_components/pages/skills_list.py) | **Observed:** skills can be read and sources administered, but there is no end-user create/update API. The authoring UI therefore exports a draft instead of silently publishing it. |
| R9 | [Codex skill rendering](../src/openscientist/agent/skills.py), [`data-science` skill](../skills/domain/data-science/SKILL.md) | **Observed:** materialization writes the skill body unchanged. Fenced Python is delivered as agent-visible procedural content; the renderer does not parse or execute it. |
| R10 | [`execute_code` registration](../src/openscientist_tools/code_exec.py), [MCP server](../src/openscientist_tools/server.py) | **Observed:** `execute_code` is a separately registered MCP callable with an explicit interface. A code block in a skill does not define or invoke that tool. |

### Current effective format

The minimum parser-valid file is:

```markdown
---
name: Example
---
```

That is a syntax floor, not a quality standard. For predictable portable
behavior, the authoring tools require or recommend:

```yaml
name: Human-readable name
description: Capability plus concrete trigger conditions.
category: workflow-or-domain
slug: lowercase-hyphenated-identifier
tags:
  - precise-retrieval-term
```

## Corpus inventory

### Files delivered as skills

| Trace | Strength worth retaining | Review concern |
| --- | --- | --- |
| [`workflow/hypothesis-generation`](../skills/workflow/hypothesis-generation/SKILL.md) | Testability, competing explanations, explicit handoff. | Its 0–5 weighted scoring model does not match prioritization's multiplicative model. |
| [`workflow/prioritization`](../skills/workflow/prioritization/SKILL.md) | Impact/feasibility/novelty decision structure. | Scores range to 125 and examples use thresholds incompatible with hypothesis generation. |
| [`workflow/result-interpretation`](../skills/workflow/result-interpretation/SKILL.md) | Effect sizes, assumptions, negative-result learning, multiple-testing warning. | It equates `p` above threshold with a rejected hypothesis, which overstates evidence. |
| [`workflow/stopping-criteria`](../skills/workflow/stopping-criteria/SKILL.md) | Explicit saturation, budget, and diminishing-return checks. | Its priority thresholds inherit the inconsistent scoring contract. |
| [`domain/data-science`](../skills/domain/data-science/SKILL.md) | Broad procedural coverage, reproducibility prompts, and directly usable Python templates. | Some statistical defaults and diagnostic logic need specialist review before being mandatory. |
| [`domain/genomics`](../skills/domain/genomics/SKILL.md) | Strong biological-replicate and single-cell pseudoreplication guidance. | Early count-data examples suggest ordinary per-gene tests despite later DESeq2/edgeR guidance. |
| [`domain/metabolomics`](../skills/domain/metabolomics/SKILL.md) | Pathway context and ratio exploration. | Steady-state abundance ratios are repeatedly described as flux and bottlenecks without sufficient qualification. |
| [`domain/phenix-tools-reference`](../skills/domain/phenix-tools-reference/SKILL.md) | Exact supported tool-call contract. | Long command catalog increases context cost; validate commands against installed versions. |
| [`domain/berkeley-data-lakehouse/kbase-query`](../skills/domain/berkeley-data-lakehouse/kbase-query/SKILL.md) | Trigger-rich description, endpoint decision tree, practical failure handling. | Links to sibling references and scripts that current ingestion does not deliver. |
| [`domain/berkeley-data-lakehouse/jgi-lakehouse`](../skills/domain/berkeley-data-lakehouse/jgi-lakehouse/SKILL.md) | Concrete schemas, query patterns, and provenance context. | Contains a contributor-specific `/Users/cjm/...` path and an unavailable sibling reference. |

### Files present but not delivered

| Files | Runtime status |
| --- | --- |
| [`structural_biology/alphafold-confidence.md`](../skills/domain/structural_biology/alphafold-confidence.md), [`comparing-structures.md`](../skills/domain/structural_biology/comparing-structures.md), [`interpreting-discrepancies.md`](../skills/domain/structural_biology/interpreting-discrepancies.md), [`validation-metrics.md`](../skills/domain/structural_biology/validation-metrics.md) | **Observed:** skipped because they are not named `SKILL.md`. Some also recommend custom `execute_code` paths that conflict with the global Phenix-first rule. |
| [`kbase-query/references/api_reference.md`](../skills/domain/berkeley-data-lakehouse/kbase-query/references/api_reference.md), [`jgi-lakehouse/references/databases.md`](../skills/domain/berkeley-data-lakehouse/jgi-lakehouse/references/databases.md) | **Observed:** not ingested; links from their parent skills cannot be followed in a materialized job. |
| [`kbase-query/scripts/`](../skills/domain/berkeley-data-lakehouse/kbase-query/scripts) (9 shell scripts) | **Observed:** not ingested or materialized. The skill must not rely on these scripts until bundle support is implemented. |

## Cross-corpus findings

### What the strongest skills have in common

**Observed:** the KBase and JGI skills have descriptions containing recognizable
triggers, concrete tool/data contracts, ordered routing decisions, examples,
and failure advice. The genomics skill's pseudobulk section explicitly protects
the biological replicate as the unit of inference. The workflow skills make
phase transitions and output artifacts visible.

**Assessment:** those patterns are valuable because an agent can decide when to
load the procedure, execute it without hidden author context, and expose enough
evidence for a scientist to audit the result.

**Recommendation:** every new skill should have a task contract, one primary
decision procedure, an output contract, epistemic labels, provenance rules, and
safe failure behavior.

### Conflicts that should become regression cases

| ID | Evidence | Risk and recommended regression |
| --- | --- | --- |
| C1 | [Hypothesis score](../skills/workflow/hypothesis-generation/SKILL.md#L129), [priority score](../skills/workflow/prioritization/SKILL.md#L92) | **Observed:** incompatible scales. Define one scoring contract and test that every workflow uses it. |
| C2 | [Non-significant result](../skills/workflow/result-interpretation/SKILL.md#L66) | **Assessment:** “hypothesis rejected” is too strong. Test for language such as “not supported at the chosen sensitivity” plus confidence interval and power/precision context. |
| C3 | [Genomics normalization](../skills/domain/genomics/SKILL.md#L22), [pseudobulk guidance](../skills/domain/genomics/SKILL.md#L150) | **Observed:** strong later guidance coexists with weaker early defaults. Test that count-data comparisons preserve replicate structure and use an appropriate count model. |
| C4 | [Metabolomics flux claims](../skills/domain/metabolomics/SKILL.md#L37) | **Assessment:** abundance ratios alone do not identify pathway flux. Test that the agent labels them as exploratory proxies and requests isotope-tracing or kinetic evidence for flux claims. |
| C5 | [JGI local path](../skills/domain/berkeley-data-lakehouse/jgi-lakehouse/SKILL.md#L106) | **Observed:** non-portable machine path. Reject contributor-specific absolute paths. |
| C6 | [Structural helper](../skills/domain/structural_biology/comparing-structures.md#L97), [global Phenix rule](../src/openscientist/prompts/common.py#L383) | **Observed:** tool-policy conflict, though the helper file is currently skipped. Test that delivered skill instructions do not contradict platform tool policy. |

## Derived authoring standard

A production-quality skill should satisfy all of these layers:

1. **Selection:** distinctive description, positive triggers, and non-triggers.
2. **Contract:** prerequisites, supported inputs, output, and completion
   criteria.
3. **Procedure:** one clear sequence or decision tree with justified degrees of
   freedom. Directly usable Python recipes are valid when the agent is told how
   to adapt them and explicitly invoke `execute_code`.
4. **Evidence:** sources for consequential claims; identifiers, versions,
   parameters, units, and intermediate checks.
5. **Epistemics:** observations, associations, hypotheses, proxies, and causal
   claims remain distinct.
6. **Safety:** failure behavior and human confirmation for consequential
   actions.
7. **Portability:** safe identifiers, no secrets or machine paths, and no
   reliance on resources the runtime does not deliver.
8. **Evaluation:** positive and negative selection cases plus normal, edge,
   failure, and regression execution cases.

## Product decisions for the Skill Creator

| Decision | Evidence and rationale |
| --- | --- |
| Guided task-contract form | Converts the strongest corpus patterns into explicit human-owned inputs. |
| LLM generation and refinement | Useful for synthesis, but model output is always an editable proposal. |
| Deterministic validation | Separates enforceable syntax and portability checks from model judgment. |
| Explicit Accept step | Keeps authors responsible for scientific and operational claims. |
| Export-only `SKILL.md` | There is no end-user write API, provenance model, or trust-review workflow for safe direct publishing. |
| Isolated, no-tools ephemeral model turn | Uses provider text-completion APIs rather than an agent runtime, substitutes unusable settings sentinels for job/database credentials, omits execution and Codex OAuth credentials, and carries only the active provider settings required for the API call. |
| No sibling resources in generated drafts | Matches R2 and current materialization behavior. |
| Inline executable recipes allowed | Matches R9: Python can live directly in `SKILL.md`, while execution remains an explicit `execute_code` tool call by the job agent. |

## External knowledge traces

These sources inform recommendations, not claims about current OpenScientist
behavior:

- [Agent Skills specification](https://agentskills.io/specification) — portable
  `SKILL.md` structure and progressive disclosure.
- [Agent Skills authoring best practices](https://agentskills.io/skill-creation/best-practices)
  — concise instructions, degrees of freedom, workflows, feedback loops, and
  examples.
- [Optimizing skill descriptions](https://agentskills.io/skill-creation/optimizing-descriptions)
  — trigger/near-miss evaluation for reliable selection.
- [OpenAI Academy: Skills](https://openai.com/academy/skills/) — reusable
  workflows, instructions, examples, and resources.
- [Anthropic: Equipping agents for the real world with Agent Skills](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills)
  — progressive disclosure as a context-management pattern.

## Open engineering questions

- How should sources be pinned, reviewed, signed, and promoted before mandatory
  instructions are automatically synchronized?
- Should explicit category and slug validation be enforced at ingestion, and
  how should existing incompatible rows be migrated?
- When will references, scripts, and assets be stored and materialized with
  integrity checks?
- How should conflicts among mandatory workflow skills be detected?
- What evaluation format and provider matrix should gate skill changes?
- How should provenance, ownership, versioning, deprecation, and rollback be
  represented?

Until those questions are resolved, authoring remains export-first and
publication remains an explicit repository or administrator workflow.

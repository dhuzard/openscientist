"""Oh My Pi (omp) prompt fragments.

Like the Codex fragments: omp surfaces skills natively (from ``.omp/skills/``),
so these drop the ``.claude/`` paths and the ``search_skills`` tool and use
omp's built-in ``read``/``write`` tools.

No tool-calling guidance is needed here: ``OmpAgent`` runs omp with
``tools.xdev`` disabled, so the MCP tools are top-level callable tools under the
same plain names the shared prompt already uses.
"""

from openscientist.prompts.common import BackendFragments

OMP_FRAGMENTS = BackendFragments(
    skills_location=(
        "the skills omp lists in this prompt (omp surfaces each available "
        "skill and the path to its `SKILL.md` natively)"
    ),
    builtin_read_tool="the built-in `read` tool",
    builtin_read_tool_short="the built-in `read` tool",
    search_skills_doc="",
    skills_discovery_note="",
    # omp exposes MCP tools top-level (OmpAgent disables tools.xdev) but keeps
    # them namespaced by server, so the prompts must name them that way.
    mcp_tool_prefix="mcp__openscientist_tools_",
)

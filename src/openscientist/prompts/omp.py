"""Oh My Pi (omp) prompt fragments.

Like the Codex fragments: omp surfaces skills natively (from ``.omp/skills/``),
so these drop the ``.claude/`` paths and the ``search_skills`` tool and use
omp's built-in ``read``/``write`` tools.
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
)

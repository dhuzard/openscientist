"""Standalone `execute_code` tool.

Dispatches Python/Rust/SPARQL snippets into one-shot Docker
executor containers via the shared `ContainerManager`. Mirrors
the in-process tool's data-file caching, KS status updates, and
log_analysis writes.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import time
from pathlib import Path
from typing import Any

from mcp.server.fastmcp.exceptions import ToolError

from openscientist.code_executor import format_execution_result
from openscientist.exec_broker_client import BrokerError, execute_code_via_broker
from openscientist.file_loader import get_file_info, load_data_file
from openscientist.job_container.utils import to_host_path
from openscientist.knowledge_state import KnowledgeState
from openscientist.settings import get_settings
from openscientist_tools.server import mcp
from openscientist_tools.state import STATE

logger = logging.getLogger(__name__)

_DATA_CACHE: dict[str, object] = {}
_DATA_LOADED: dict[str, bool] = {}
_DATA_ERROR: dict[str, str | None] = {}

_DVC_SKILL_KEY = "domain--digital-ventilated-cage-analysis"
_DATA_SCIENCE_SKILL_KEY = "domain--data-science"
_EXPLORATORY_SCOPE_RE = re.compile(
    r"\b(exploratory|validation|diagnostic|quality control|qc)\b",
    re.IGNORECASE,
)
_STATISTICS_OR_PLOT_RE = re.compile(
    r"\b("
    r"ttest|t-test|wilcoxon|mannwhitney|anova|kruskal|pearson|spearman|"
    r"correlation|effect[\s_-]*size|cohen|confidence[\s_-]*interval|"
    r"regression|mixedlm|ols|cosinor|periodogram|scipy\.stats|statsmodels|"
    r"matplotlib|seaborn|plot|corr"
    r")\b",
    re.IGNORECASE,
)
_BIOLOGICAL_TIME_RE = re.compile(
    r"\b("
    r"circadian|cosinor|acrophase|zeitgeber|zt\d*|dark[\s_-]*(?:onset|phase)|"
    r"light[\s_-]*(?:onset|phase|schedule)|lights?[\s_-]*(?:on|off)|"
    r"phase[\s_-]*(?:angle|shift|mean)"
    r")\b",
    re.IGNORECASE,
)
_DVC_DATASET_ID_RE = re.compile(r"\bdvc-[0-9a-fA-F-]{36}\b")


def _assigned_skill_keys(job_dir: Path) -> set[str]:
    try:
        payload = json.loads(
            (job_dir / ".openscientist_skill_manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return set()
    if not isinstance(payload, list):
        return set()
    return {
        str(item["key"])
        for item in payload
        if isinstance(item, dict) and isinstance(item.get("key"), str)
    }


def _verified_biological_time_context_issue(
    job_dir: Path,
    *,
    searchable: str,
) -> str | None:
    """Return why exact-dataset biological-time provenance is insufficient."""
    dataset_root = job_dir / "dvc_datasets"
    available = {
        path.name
        for path in dataset_root.iterdir()
        if path.is_dir() and _DVC_DATASET_ID_RE.fullmatch(path.name)
    }
    referenced = set(_DVC_DATASET_ID_RE.findall(searchable))
    unknown = referenced - available
    if unknown:
        return "the code references an unknown DVC dataset"
    if not referenced:
        if len(available) != 1:
            return (
                "the exact DVC dataset is ambiguous; reference one dataset ID "
                "explicitly"
            )
        referenced = available

    analyses_dir = job_dir / "dvc_analyses"
    if not analyses_dir.is_dir():
        return "no governed analysis provenance is available"
    verified: set[str] = set()
    for path in analyses_dir.glob("*/provenance.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        dataset_id = payload.get("dataset_id")
        prerequisites = payload.get("scientific_prerequisites", {})
        if not isinstance(dataset_id, str) or not isinstance(prerequisites, dict):
            continue
        required = (
            prerequisites.get("environment.light_schedule"),
            prerequisites.get("environment.timezone"),
        )
        if all(
            isinstance(item, dict)
            and item.get("status") in {"recorded", "computed"}
            and isinstance(item.get("source"), str)
            and bool(item["source"].strip())
            and isinstance(item.get("value_sha256"), str)
            and bool(re.fullmatch(r"[0-9a-f]{64}", item["value_sha256"]))
            for item in required
        ):
            verified.add(dataset_id)
    missing = referenced - verified
    if missing:
        return (
            "source-backed light schedule and local timezone provenance is missing "
            f"for: {', '.join(sorted(missing))}"
        )
    return None


def _dvc_code_guardrail(
    job_dir: Path,
    *,
    code: str,
    description: str,
) -> tuple[str | None, str | None]:
    """Fail closed for ad hoc code in a governed DVC job.

    Returns ``(blocker, scope_notice)``. Jobs that are not governed DVC jobs
    remain unaffected.
    """
    skills = _assigned_skill_keys(job_dir)
    if _DVC_SKILL_KEY not in skills or not (job_dir / "dvc_datasets").is_dir():
        return None, None

    if not _EXPLORATORY_SCOPE_RE.search(description):
        return (
            "❌ DVC GOVERNANCE BLOCK: `execute_code` is outside governed UDWA "
            "execution. Label its description explicitly as validation diagnostics "
            "or exploratory work; it cannot produce governed evidence.",
            None,
        )

    searchable = f"{description}\n{code}"
    if (
        _STATISTICS_OR_PLOT_RE.search(searchable)
        and _DATA_SCIENCE_SKILL_KEY not in skills
    ):
        return (
            "❌ DVC METHODOLOGY BLOCK: statistical testing, assumptions, effect "
            "sizes, correlations, modelling, and plots outside governed UDWA require "
            "the assigned `data-science` skill.",
            None,
        )

    biological_time_issue = (
        _verified_biological_time_context_issue(job_dir, searchable=searchable)
        if _BIOLOGICAL_TIME_RE.search(searchable)
        else None
    )
    if biological_time_issue:
        return (
            "❌ DVC SCIENTIFIC BLOCK: biological circadian modelling, phase/dark-"
            "onset interpretation, and light/dark-aligned plots require a verified, "
            "source-backed local light schedule and timezone for the exact dataset "
            f"({biological_time_issue}). Placeholder assumptions are not allowed.",
            None,
        )

    return (
        None,
        "⚠️ DVC GOVERNANCE SCOPE: this `execute_code` result is explicitly "
        "ungoverned validation/exploratory output. It must not be presented as "
        "governed scientific evidence.",
    )


def _ensure_data_loaded() -> str | None:
    """Load STATE.data_file into the module-level cache. Returns error or None."""
    key = str(STATE.job_dir)
    if _DATA_LOADED.get(key):
        return _DATA_ERROR.get(key)

    _DATA_LOADED[key] = True

    if STATE.data_file is None:
        _DATA_ERROR[key] = None
        _DATA_CACHE[key] = None
        return None

    try:
        file_size_mb = STATE.data_file.stat().st_size / (1024 * 1024)
        print(
            f"⏳ Loading data: {STATE.data_file.name} ({file_size_mb:.1f} MB)",
            file=sys.stderr,
        )
        start = time.time()
        data = load_data_file(STATE.data_file)
        elapsed = time.time() - start

        if data is not None:
            print(
                f"✅ Loaded {data.shape[0]}x{data.shape[1]} in {elapsed:.1f}s",
                file=sys.stderr,
            )
        _DATA_CACHE[key] = data
        _DATA_ERROR[key] = None
        return None

    except Exception as e:
        err = f"Unable to load data file '{STATE.data_file.name}': {e}"
        print(f"❌ {err}", file=sys.stderr)
        _DATA_ERROR[key] = err
        _DATA_CACHE[key] = None
        return err


@mcp.tool()
def execute_code(code: str, language: str = "python", description: str = "") -> str:
    """Execute code to analyze data.

    Supported languages:
    - "python" (default): Use for data analysis, statistical testing, and
      visualization. Your uploaded data files are ALREADY available in this
      tool's namespace. Do not open them by guessing filesystem paths, and do
      not reuse paths you saw in the shell (such as /agent/jobs/.../data): those
      paths do not exist in this executor. Access the data through:
        * `data`: a pandas DataFrame pre-loaded from the primary data file,
          if it's a tabular format (csv/tsv/parquet/xlsx/json). For
          non-tabular primary files (h5ad, structures, sequences, images),
          `data` is None -- load the file yourself, see below.
        * `data_files`: a list of dicts, one per uploaded file, each with a
          `path` key that already points to the file inside this executor
          (under /data). Read tabular files with
          `pd.read_csv(data_files[i]["path"])`. Load non-tabular files
          directly with the matching library, e.g.
          `scanpy.read_h5ad(data_files[i]["path"])` for `.h5ad`,
          `biopython`/`Bio.SeqIO` for sequence files, `PIL.Image.open(...)`
          for images.
      Also available: pandas, polars, numpy, scipy, matplotlib, seaborn, plotly,
      statsmodels, pingouin, sklearn, umap-learn, leidenalg, networkx, biopython,
      scanpy, pydeseq2, and more. Plots are automatically saved to the job's
      plots directory. Choose Python unless a specific reason (performance,
      structured knowledge lookup) justifies another language.
    - "rust": Use when Python is too slow — e.g., tight inner loops over >1M rows,
      custom numerical algorithms, or performance-critical computation. Compiled and
      run with cargo. Pre-seeded crates available without imports or downloads:
      rayon (parallel iteration), ndarray + ndarray-stats (N-dimensional arrays and
      statistics), statrs (statistical distributions), rand (random numbers),
      serde + serde_json (serialization), csv (CSV parsing), anyhow (error handling),
      itertools (iterator combinators), num-traits (Float, Zero, One, etc.).
      No data or plot integration; write results to stdout.
    - "sparql": Use to query structured knowledge bases for biological, chemical, or
      scientific facts (e.g., gene functions, protein interactions, drug targets,
      taxonomic relationships). The query must include a comment specifying the
      endpoint URL, e.g.:
          # ENDPOINT: https://query.wikidata.org/sparql
      Other common endpoints: https://sparql.uniprot.org/sparql (proteins),
      https://bio2rdf.org/sparql (life sciences). Results are returned as a
      formatted table. No data or plot integration.

    Args:
        code: Code or query to execute
        language: Language to use ("python", "rust", or "sparql"). Default: "python"
        description: Optional description of what you're investigating

    Returns:
        Formatted execution result with output, plots (Python only), and any errors
    """
    if language not in ("python", "rust", "sparql"):
        return f"❌ ERROR: Unsupported language '{language}'. Supported: 'python', 'rust', 'sparql'"

    governance_blocker, governance_scope = _dvc_code_guardrail(
        STATE.job_dir,
        code=code,
        description=description,
    )
    if governance_blocker:
        return governance_blocker

    load_error = _ensure_data_loaded()
    if load_error and language not in ("rust", "sparql"):
        return f"❌ ERROR: Cannot execute code - data file failed to load.\n\n{load_error}"

    ks = KnowledgeState.load_from_database_sync(STATE.job_id)

    lang_label = {"python": "Python", "rust": "Rust", "sparql": "SPARQL"}.get(language, language)
    status_msg = f"Running {lang_label} script" if language != "sparql" else "Running SPARQL query"
    if description:
        suffix = description[:50] + "..." if len(description) > 50 else description
        status_msg = (
            f"Running {lang_label} {'query' if language == 'sparql' else 'script'}: {suffix}"
        )
    ks.set_agent_status(status_msg)
    ks.save_to_database_sync(STATE.job_id)

    provenance_dir = STATE.job_dir / "provenance"
    provenance_dir.mkdir(parents=True, exist_ok=True)

    cs = get_settings().container

    def _to_host(path: Path) -> str:
        # Resolve and map to the host path the broker mounts.
        return str(to_host_path(path.resolve(), cs))

    job_id = STATE.job_dir.name
    host_output_dir = _to_host(provenance_dir)

    result: dict[str, Any]
    execution_started = time.time()
    try:
        if language == "python":
            data_files: list[dict[str, Any]] = []
            for df_path in STATE.data_files:
                if not df_path.exists():
                    raise FileNotFoundError(f"Data file not found: {df_path}")
                info = get_file_info(df_path)
                info["path"] = _to_host(Path(info["path"]))
                data_files.append(info)

            primary_data_path = _to_host(STATE.data_files[0]) if STATE.data_files else None

            result = execute_code_via_broker(
                code=code,
                language="python",
                job_id=job_id,
                output_dir=host_output_dir,
                data_path=primary_data_path,
                data_files=data_files,
                description=description,
                iteration=int(ks.data["iteration"]),
                timeout=60,
            )
        else:
            result = execute_code_via_broker(
                code=code,
                language=language,
                job_id=job_id,
                output_dir=host_output_dir,
                description=description,
                iteration=int(ks.data["iteration"]),
                timeout=300 if language == "rust" else 60,
            )
    except BrokerError as exc:
        message = f"Code execution service unavailable: {exc}"
        execution_time = time.time() - execution_started
        # Persist the failure independently of the agent transcript. This makes
        # the warning visible in the investigation timeline immediately, even
        # when the surrounding model turn later times out.
        ks.log_analysis(
            action="execute_code",
            code=code,
            description=description,
            output=message,
            success=False,
            execution_time=execution_time,
            plots=[],
            governance_scope=governance_scope,
        )
        ks.set_agent_status("Code execution failed — see Agentic Info")
        ks.save_to_database_sync(STATE.job_id)
        logger.error("execute_code broker failure for job %s: %s", STATE.job_id, exc)
        # Returning an error-looking string is still a successful MCP call.
        # Raising ToolError sets the protocol-level isError flag so Codex and
        # the UI can reliably classify and alarm on the failure.
        raise ToolError(message) from exc

    ks.log_analysis(
        action="execute_code",
        code=code,
        description=description,
        output=result.get("output", ""),
        success=result["success"],
        execution_time=result["execution_time"],
        plots=result.get("plots", []),
        governance_scope=governance_scope,
    )
    ks.save_to_database_sync(STATE.job_id)

    formatted = format_execution_result(result)
    if governance_scope:
        return f"{governance_scope}\n\n{formatted}"
    return formatted

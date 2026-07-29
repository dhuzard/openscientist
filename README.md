# OpenScientist: Scientific Hypothesis Agent for Novel Discovery

An autonomous AI scientist that generates and tests hypotheses from scientific data.

**Live instance: [openscientist.io](https://openscientist.io)**

## Overview

OpenScientist is a domain-agnostic autonomous discovery agent that:

- Accepts data files and a research question
- Runs for N iterations autonomously
- Generates hypotheses, tests them, searches literature
- Produces a final report with findings and mechanistic insights

## Features

### Core Capabilities

- **Autonomous Discovery**: Runs iterative hypothesis-testing loop using an agentic coding assistant
- **Domain-Agnostic**: Works with genomics, transcriptomics, proteomics, metabolomics, and other scientific data
- **Literature-Grounded**: Searches PubMed for mechanistic insights
- **Multiple Agent Backends**: Runs investigations with Claude Code or OpenAI Codex
- **Multi-Provider Support**: Connects to Anthropic, CBORG, Vertex AI, Bedrock, Azure AI Foundry, OpenAI, Azure OpenAI, or Ollama
- **Cost Tracking**: Project-level budget monitoring with provider-specific cost APIs
- **Sandboxed Execution**: Runs model-written Python, Rust, and SPARQL in resource-limited executor containers

### Skills System

- **Workflow Skills**: Hypothesis generation, result interpretation, prioritization, stopping criteria
- **Domain Skills**: Metabolomics, genomics/transcriptomics, structural biology, data science/statistics

### Architecture

- **MCP Tools**: Provides tools via Model Context Protocol
  - `execute_code`: Run Python, Rust, or SPARQL analysis
  - `search_pubmed`: Search literature
  - `update_knowledge_state`: Record findings
  - `dvc_test_connection`, `dvc_list_metrics`, `dvc_search_cages`,
    `dvc_import_dataset`: Acquire Tecniplast DVC data through pinned UDWA
  - `dvc_assess_pre_analysis`, `dvc_run_analysis`,
    `dvc_assess_post_analysis`: Run the governed DVC assessment and analysis flow
  - `run_phenix_tool`, `compare_structures`, `parse_alphafold_confidence` (optional, requires Phenix)
- **Knowledge State**: PostgreSQL-backed tracking for findings, hypotheses, literature, analysis logs, and iteration summaries
- **Job Manager**: Multi-job queueing with pause/resume, live iteration limits, and early reports
- **Web and REST Interfaces**: NiceGUI UI plus an authenticated FastAPI API

Each job runs in a dedicated agent container. The agent calls the standalone
`openscientist-tools` MCP server, which routes model-written analysis through an
execution broker into short-lived executor containers. Executor containers have
resource limits and no network in air-gapped mode; the agent container can also
run behind a default-deny egress firewall.

### Governed Tecniplast DVC workflow

The integration foundation supports this ordered flow:

```text
DVC API
→ UDWA-backed MCP acquisition
→ pre-analysis FAIR/PREPARE/ARRIVE assessment
→ authenticated human approval
→ governed UDWA analysis
→ immutable provenance
→ post-analysis FAIR/ARRIVE/MNMS assessment
```

OpenScientist resolves DVC credentials server-side; agents use logical
connection identifiers and never receive API keys. Scientific operations are
allowlisted, prerequisite-checked, bound to authenticated approvals when
required, and persisted with input hashes and versioned provenance.

This is an implemented integration foundation, not yet a proven live POC. A
usable deployment still requires a reachable FAIR-VCG service, DVC credentials,
container-network verification, agent orchestration instructions, and one real
end-to-end run against Tecniplast DVC and FAIR-VCG. Approval currently uses an
authenticated REST endpoint; a graphical approval experience and dedicated
evidence-linked final-report workflow remain backlog items.

See [DVC POC and backlog](docs/DVC_POC.md),
[integration boundaries](docs/DVC_INTEGRATION_ARCHITECTURE.md), and
[FAIR/PREPARE integration](docs/FAIR_PREPARE_INTEGRATION.md).

### Structural Biology Support (Optional)

OpenScientist supports **Phenix integration** for protein structure analysis:

- Structure comparison and superposition
- Validation metrics (clash score, backbone geometry)
- AlphaFold confidence analysis

See the Phenix section in [.env.example](.env.example) for installation and
configuration notes.

## Quick Start

### Prerequisites

- Python 3.12+
- Docker (for containerized deployment)
- `uv` package manager
- Credentials for one of the supported model providers (Ollama can run locally
  without an API key)

### Installation

```bash
# Clone the repository
git clone <repository-url>
cd openscientist

# Create .env file (copy from example and configure)
cp .env.example .env
# Edit .env with your provider credentials

# Build and start
make build
make start
```

### Access the UI

Open your browser to `http://localhost:8080`

## Usage

1. Upload your data files (optional - supports CSV, TSV, Excel, Parquet, JSON, PDB, mmCIF, FASTA, images, and many other file types)
2. Enter your research question
3. Set maximum iterations (e.g., 10)
4. Click "Start Discovery"
5. Monitor progress and view results

## Project Structure

```
openscientist/
├── src/
│   ├── openscientist/             # Web app, API, orchestration, and persistence
│   │   ├── agent/                 # Claude Code and Codex agent backends
│   │   ├── api/                   # Authenticated REST endpoints
│   │   ├── database/              # PostgreSQL models, RLS, and migrations
│   │   ├── job_container/         # Per-job container lifecycle and egress policy
│   │   ├── orchestrator/          # Iterative discovery and report generation
│   │   ├── providers/             # LLM provider and cost integrations
│   │   ├── report/                # Markdown, HTML, PDF, and figure rendering
│   │   └── transcript/            # Backend-neutral transcript schema
│   ├── openscientist_tools/       # Standalone scientific MCP server
│   └── openscientist_executor/    # Isolated code-execution entry point
├── skills/                        # Built-in workflow and domain skills
├── tests/                         # Unit and integration tests
├── Dockerfile.agent               # Per-job agent image
├── Dockerfile.executor            # Analysis executor image
└── docker-compose.yml             # Web app and PostgreSQL services
```

## Configuration

### Model Providers

The selected provider determines which agent backend runs:

| Agent backend | Provider IDs |
|---------------|--------------|
| **Claude Code** | `anthropic`, `cborg`, `vertex`, `bedrock`, `foundry` |
| **Codex** | `openai`, `azure-openai`, `ollama` |

Choose a provider and copy its credential settings from
[.env.example](.env.example):

```bash
OPENSCIENTIST_PROVIDER=anthropic
# Add the credentials for the selected provider.
```

`OPENSCIENTIST_MODEL` optionally overrides the provider's default model. Cost
tracking and authentication capabilities vary by provider; `.env.example`
documents the corresponding settings.

### Budget Controls

Set application-level budget limits (optional):

```bash
# Maximum total spend across all jobs
MAX_PROJECT_SPEND_TOTAL_USD=1000

# Maximum spend in last 24 hours
MAX_PROJECT_SPEND_24H_USD=50
```

Budget limits are checked before job creation. The web UI displays:

- Total project spend
- Recent spend (last 24h)
- Budget remaining (if provider supports it)

For OpenAI, budget checks automatically use estimated costs recorded after each
OpenScientist agent turn because ordinary API keys cannot read organization
billing data. Running jobs therefore update the local counter between turns.
These estimates do not include OpenAI usage outside this app.

### Other Settings

```bash
# Dev mode - enables mock OAuth login for development
OPENSCIENTIST_DEV_MODE=true
```

### DVC and FAIR/PREPARE settings

The governed DVC flow resolves credentials in the agent/tool-server
environment:

```bash
DVC_BASE_URL=https://<dvc-api-host>
DVC_API_KEY=<dvc-api-key>
FAIR_PREPARE_URL=http://fair-vcg-mentor:8000
```

Named DVC connections use
`DVC_CONNECTION_<NORMALIZED_CONNECTION_ID>_API_KEY` and optionally
`DVC_CONNECTION_<NORMALIZED_CONNECTION_ID>_BASE_URL`. Never place credentials
in MCP arguments, manifests, analysis parameters, or provenance.

UDWA is pinned in `requirements/udwa-poc.txt` and currently comes from a private
repository. Building `Dockerfile.agent` therefore requires a BuildKit
`github_token` secret with read access to that repository; the secret is used
only during installation and is not retained in the image.

### Job Manager Settings

- `OPENSCIENTIST_MAX_CONCURRENT_JOBS`: Maximum concurrent jobs (default: `1`)
- `OPENSCIENTIST_JOBS_DIR`: Directory for job artifacts (default: `jobs/`)

### Legacy Bootstrap (Filesystem -> DB)

If you have pre-database jobs on disk, run:

```bash
docker compose exec openscientist python -m openscientist.job_manager bootstrap --jobs-dir /app/jobs --dry-run
docker compose exec openscientist python -m openscientist.job_manager bootstrap --jobs-dir /app/jobs
```

Jobs with unresolved ownership are migrated as orphaned (`owner_id=NULL`) and
can be assigned later from the admin UI.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, testing, and deployment.

## Documentation

- [Design Document](docs/DESIGN.md)
- [Deployment Guide](docs/DEPLOYMENT.md)
- [Job Run Controls](docs/JOB_RUN_CONTROLS.md)
- [DVC POC and Development Backlog](docs/DVC_POC.md)
- [DVC Integration Architecture](docs/DVC_INTEGRATION_ARCHITECTURE.md)
- [FAIR/PREPARE Integration](docs/FAIR_PREPARE_INTEGRATION.md)
- [Security Review](docs/SECURITY_REVIEW.md)
- [Environment Configuration](.env.example)

## Author

Justin Reese <justinreese@lbl.gov>

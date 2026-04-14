# Datatalk

Datatalk is a natural language interface to U.S. campaign finance data, built at Stanford / Big Local News. Citizens, journalists, and researchers can ask questions in plain English and get accurate, sourced answers backed by FEC and OpenSecrets data.

**Status:** V2 in active development. See [docs/PRD-v2.md](docs/PRD-v2.md) and [docs/Design-v2.md](docs/Design-v2.md) for project plans.

## Quick Start

### Prerequisites

- Docker and Docker Compose
- Python 3.12+ and [uv]
- An LLM — either a commercial API key (OpenAI/Anthropic) **or** a local model via [Ollama]
- PostgreSQL (local or via Docker)

[uv]: https://docs.astral.sh/uv/
[Ollama]: https://ollama.com/

### 1. Clone and configure

```bash
git clone <repo-url>
cd datatalk
cp .env.example .env
```

Edit `.env` and add your API key(s) — or skip this step and use a local model instead (see below).

#### Option A: Commercial LLM API key

Add `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` to your `.env` file.

#### Option B: Local model (no API key needed)

Run a local model with [Ollama](https://ollama.com/):

```bash
# Install Ollama (macOS)
brew install ollama

# Start the server and pick a model
scripts/ollama.sh start qwen3:32b

# Or start and choose interactively (requires fzf)
scripts/ollama.sh start
```

The script supports `start`, `stop`, `restart`, and `status`. Logs go to `.tmp/ollama/`.

Then configure `.env` to point at it:

```bash
OPENAI_API_KEY=not-needed
OPENAI_API_BASE=http://localhost:11434/v1
DATATALK_ENGINE=qwen3:32b
```

**Model recommendations:**

| Model | RAM needed | SQL quality | Speed (M1 Max) | Notes |
|-------|-----------|-------------|-----------------|-------|
| `qwen3:235b-a22b` | ~16 GB | Very good | ~10s | MoE — 235B total, 22B active. Best quality-per-GB option. |
| `qwen3:72b` | ~42 GB | Very good | ~35s | Dense 72B. Quality ceiling for 64 GB machines, but slow. |
| `qwen3:32b` | ~20 GB | Good | ~14s | Recommended for dev with 64 GB RAM. Produces correct multi-table joins. |
| `qwen3.5:9b` | ~6 GB | Good | ~3s | Newer variant, good quality for its size |
| `qwen3:8b` | ~5 GB | Acceptable | ~2s | Minimum viable for development |

This uses Ollama's OpenAI-compatible API. The MCP server's SQL translation will work with any model that litellm supports. Use the benchmark suite to measure quality differences between models.

**Important: disable thinking mode for Qwen 3.** Qwen 3 defaults to chain-of-thought reasoning, which consumes tokens before generating the actual SQL. When calling via Ollama's native API, pass `"think": false`. When calling via the OpenAI-compatible endpoint (`/v1/chat/completions`), the thinking tokens appear in a `reasoning` field and the `content` field may be empty if `max_tokens` is too low. The MCP server will need to handle this — either by using the native Ollama API with thinking disabled, or by setting high token limits on the OpenAI-compatible endpoint.

### 2. Start the infrastructure

```bash
docker compose up postgres
```

This starts PostgreSQL on port 5432.

### 3. Load FEC data

Download and load federal campaign finance data into your local Postgres:

```bash
uv sync

# Download and load the 2023-2024 election cycle
python scripts/import_fec.py --cycle 2024
```

This downloads bulk data files from [data.fec.gov](https://data.fec.gov) (~several GB for individual contributions) and loads five tables: candidates, committees, individual contributions, committee-to-candidate contributions, and candidate-committee linkage.

Options:
- `--cycle 2024` — election cycle to load (default: 2024)
- `--download-only` — download files without loading into Postgres
- `--load-only` — load already-downloaded files
- `--data-dir .tmp/fec_data/` — where to store downloaded files

To also load OpenSecrets data (if you have access):

```bash
python scripts/import_opensecrets.py --data-dir path/to/opensecrets/csvs/
```

### 4. Run the MCP server

```bash
# stdio transport (for Claude Desktop, mcp-cli)
uv run python -m datatalk.mcp_server

# SSE transport (for LibreChat or HTTP clients)
uv run python -m datatalk.mcp_server --transport sse --port 8080
```

The MCP server exposes a `query_campaign_finance` tool that accepts natural language questions and returns structured results with data, SQL, confidence levels, and caveats.

### 5. (Optional) Start the full stack with LibreChat

To bring up the complete chat UI:

```bash
docker compose up
```

This starts PostgreSQL, FerretDB, MeiliSearch, the MCP server, and LibreChat. Visit http://localhost:3080 for the chat UI.

See [DEVELOPING.md](DEVELOPING.md) for the full set of development modes.

## Project Structure

```
datatalk/
├── docs/                        # Project documentation
│   ├── PRD-v2.md                # Product requirements
│   ├── Design-v2.md             # Technical design
│   └── evaluation-methodology.md # Eval system spec
│
├── backend/                     # All custom Python code
│   ├── datatalk/
│   │   ├── mcp_server/          # MCP server (campaign finance tools)
│   │   └── pipeline/            # Data import pipelines
│   │       ├── scrapers/        # FEC and OpenSecrets downloaders
│   │       └── loaders/         # PostgreSQL schema and loading
│   └── tests/
│
├── frontend/                    # LibreChat configuration (not source)
│   ├── librechat.yaml           # MCP server config, model settings
│   └── system-prompt.md         # Campaign finance system prompt
│
├── scripts/                     # CLI tools
│   ├── import_fec.py            # Download and load FEC data
│   ├── import_opensecrets.py    # Load OpenSecrets data
│   └── run_benchmark.py         # Run evaluation benchmarks
│
├── tests/                       # Evaluation benchmarks
│   └── benchmarks/
│       └── questions.yaml       # Curated test questions
│
├── agent/                       # V1 NL-to-SQL agent (legacy)
├── ingestion/                   # V1 CSV ingestion (legacy)
│
├── docker-compose.yml           # Full local stack
├── Procfile                     # honcho: local Python services
├── DEVELOPING.md                # Developer setup guide
└── .env.example                 # Environment variable template
```

## Running Tests

```bash
uv sync --dev
uv run pytest
```

## Running Benchmarks

The benchmark suite tests the system against curated campaign finance questions:

```bash
# Dry run — validate questions without calling the agent
python scripts/run_benchmark.py --dry-run

# Run a specific category
python scripts/run_benchmark.py --filter known_failures

# Run all benchmarks
python scripts/run_benchmark.py
```

Results are written to `tests/benchmarks/results/` as timestamped JSON files.

## Architecture

Datatalk V2 is three systems:

- **Database** — PostgreSQL stores campaign finance data (FEC, OpenSecrets) and LibreChat's document store (via FerretDB)
- **Backend** (Python) — MCP server, data import pipelines, evaluation system
- **Frontend** (Node.js) — LibreChat for the chat UI, configured but not forked

The MCP server is the core — both LibreChat and external tools (Claude Desktop, custom LLM setups) consume it. See [docs/Design-v2.md](docs/Design-v2.md) for the full architecture.

## Links

- [Product Requirements (PRD)](docs/PRD-v2.md)
- [Technical Design](docs/Design-v2.md)
- [Evaluation Methodology](docs/evaluation-methodology.md)
- [Developer Setup](DEVELOPING.md)
- [Live site (V1)](https://www.datatalk.genie.stanford.edu/)

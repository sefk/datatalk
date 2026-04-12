# Datatalk V2 — Design Document

**Project:** Datatalk — Natural Language Campaign Finance Explorer  
**Owner:** Big Local News / Stanford  
**Author:** Sef Kloninger <sefklon@gmail.com>
**Status:** Draft  
**See also:** [PRD-V2.md](PRD-V2.md)

---

## 1. System Architecture

Datatalk V2 is composed of five main subsystems. The MCP server is the central abstraction — both the web UI and external integrations use it.

```
                              Code LLM              Resoning LLM                            
                                  ▲                       ▲         
                                  │                       │         
                                  │                ┌──────┴────────┐
                       ┌──────────┴──────────┐     │               │
                       │                     │     │  Web UI       │
                       │     MCP Server      ├────▶│  LibreChat    │
┌────────────────┐     │ (Campaign Finance)  │     │               │
│                │     │                     │     └───────────────┘
│   PostgreSQL   ├────▶│  - Domain Knowledge │                      
│   Data Store   │     │  - NL→SQL (SUQL)    │     ┌───────────────┐
│                │     │  - Prompt Library   │     │               │
└────────────────┘     │                     ├────►│ External LLM  │
      ▲                │                     │     │ Other Tools   │
      │                └────────┬────────────┘     │               │
      │                         │  ▲               └───────────────┘
      │                         ▼  │                                
┌─────┴──────────┐     ┌───────────┴─────────┐                      
│  Data Import   │     │                     │                      
│  Pipelines     │     │  Evaluation System  │                      
│  (FEC, OS)     │     │                     │                      
└────────────────┘     └─────────────────────┘                      

```

In this diagram, a "Code LLM" is expected to be a commercially available, less capable LLM e.g. Qwen 3 or Gemma 3 / Flash 3. Reasoning LLM is a higher-cost, more capable model like Sonnet 4.5 or Opus 4.5

### Design Principles

- **MCP-first.** The MCP server is the canonical interface to the data. The web UI is a consumer of it, not a bypass around it. This ensures external integrations get the same quality as the website.
- **Cloud-portable.** No hard dependencies on a specific cloud provider. Use containers and standard services (Postgres, Redis) that run anywhere.
- **Contributor-friendly.** Clear separation of concerns, standard tooling, good documentation. A new developer should be able to run the full system locally within an hour.
- **LLM-agnostic.** The system must work with multiple LLM backends. Model-specific tuning is acceptable but not model lock-in.

---

## 2. Component Design

### 2.1 Data Import Pipelines

**Purpose:** Automated, recurring ingestion of campaign finance data from upstream sources into the relational data store.

**Current state:** V1 has a CSV-based ingestion pipeline (`ingestion/`) that detects column types and creates Postgres schemas. This handles bulk loading but has no support for incremental updates or scheduling.

**V2 design:**

```
┌────────────┐     ┌────────────┐     ┌──────────┐     ┌──────────┐
│  Scrapers  │────▶│  Staging   │────▶│  Review  │────▶│Production│
│  (per src) │     │  Tables    │     │  Queue   │     │  Tables  │
└────────────┘     └────────────┘     └──────────┘     └──────────┘
   FEC API            Detect             Operator          Merge
   OpenSecrets        deltas             inspect           into
   (future: DIME)     from last          & certify         live data
                      import
```

**Key design decisions:**

- **One scraper per source.** Each data source (FEC, OpenSecrets) gets its own scraper module with source-specific logic for API access, pagination, and rate limiting. Scrapers are independent and can run on different schedules.

- **Staging-then-promote pattern.** New data lands in staging tables first. The system computes a diff against the current production data and queues changes for operator review. Only after certification do changes merge into production tables. This prevents bad data from reaching users.

- **State tracking.** Each scraper maintains a high-water mark (last filing date, last API cursor, etc.) to support incremental imports. State is stored in the database alongside the data.

- **Operator notification.** When new data arrives in staging, the system notifies operators (email or webhook). The review interface shows what changed: new records, modified records, and summary statistics.

- **Replayability.** Import runs are logged with timestamps, source URLs, and record counts. A failed or bad import can be rolled back by replaying from the previous good state.

**Technology:**

- Python scripts, scheduled via cron or a lightweight task runner (e.g., Django management commands with cron, or Celery for more sophistication if needed)
- Maintaing core prompts using [DSPy] instead of simple files full of text prompts. The main benefits of DSPy that we hope to use are
    - avoiding the fragile prompt problem, where prompts don't survive model changes well, and
    - better revision workflow during eval
- Requests/http for API calls
- Pandas for data transformation (continuing V1 pattern)
- V1's type detection and schema inference logic (`ingestion/ingestion.py`) is retained and extended

[DSPy]: https://dspy.ai

**Open questions:**

- What are the FEC and OpenSecrets API rate limits and update schedules?
- Should we use the FEC bulk data downloads or the API for incremental updates?

### 2.2 Relational Data Store

**Purpose:** Durable, queryable storage for all campaign finance data.

**Choice: PostgreSQL**

PostgreSQL is the right choice for this workload:
- Complex relational schema with many joins (candidates ↔ committees ↔ contributions ↔ expenditures)
- Read-heavy workload with occasional batch writes (data imports)
- SUQL compatibility (V1 already uses Postgres with SUQL)
- Strong ecosystem for backups, replication, monitoring
- Available as a managed service on all major clouds (RDS, Cloud SQL, Azure Database)
- The datasets are not particularly large (<100GB) not requiring a complicated sharding or other distribution approaches.

The V1 system already uses Postgres with two database roles (`creator_role` for DDL/imports, `select_user` for read-only query execution). This pattern continues in V2.

**Schema considerations:**
- Retain V1's auto-generated schemas from CSV ingestion as a starting point
- Add explicit staging tables (prefixed `staging_`) for the import pipeline
- Add metadata tables: `import_runs`, `import_state`, `data_certifications`
- Add evaluation tables: `eval_questions`, `eval_responses`, `eval_ratings` (see Section 2.5)
- Consider materialized views for common query patterns (top donors per race, candidate totals by cycle) to reduce LLM-generated query complexity and improve response time

**Indexing strategy:**
- Index foreign keys and common filter columns (election cycle, state, office, candidate ID)
- Full-text search indexes on candidate/committee names for entity resolution
- Monitor slow queries from the NL→SQL pipeline and add indexes as needed

### 2.3 MCP Server — Campaign Finance

**Purpose:** The core intelligence layer. Accepts natural language questions about campaign finance and returns structured, explainable answers. Serves both the web UI and external integrations.

**Interface design:**

The MCP server exposes high-context tools that accept natural language and return rich structured results. Callers do not need to know SQL or our schema.

```python
# Core tools exposed via MCP

query_campaign_finance(question: str) -> QueryResult
    """General-purpose campaign finance query. Handles any natural language
    question about campaign finance data."""

get_candidate_summary(name: str, cycle: str = "current") -> CandidateSummary
    """Comprehensive funding profile for a candidate: total raised,
    top donors, spending, cash on hand, and relevant context."""

get_race_overview(office: str, state: str, cycle: str = "current") -> RaceOverview
    """Funding summary for a race: all candidates, total money in the race,
    top PACs, and comparative analysis."""

compare_candidates(names: list[str], metric: str = "total_raised") -> Comparison
    """Side-by-side comparison of candidates on a funding metric."""
```

**Response structure:**

Every MCP response includes:

```python
@dataclass
class QueryResult:
    answer: str              # Natural language answer
    data: list[dict]         # Supporting data rows
    sql_query: str           # Generated SQL (for transparency/debugging)
    confidence: str          # "high", "medium", "low"
    data_sources: list[str]  # Which datasets contributed
    data_freshness: str      # Timestamp of most recent data used
    caveats: list[str]       # Limitations, out-of-scope warnings
    suggested_followups: list[str]  # Recommended next questions
```

**Internal pipeline:**

```
    User Question
         │
         ▼
┌─────────────────────┐
│  Domain Knowledge   │  ← Curated prompt library: political context,
│  Augmentation       │    jargon definitions, known data gaps
└────────┬────────────┘
         ▼
┌─────────────────────┐
│  NL → SQL           │  ← Cheap, fast model (Gemma 3 / Flash 3)
│  Translation        │    Uses SUQL for structured generation
│  (may be multiple   │    Entity linking via existing Redis cache
│   rounds)           │    
└────────┬────────────┘
         ▼
┌─────────────────────┐
│  SQL Execution      │  ← Read-only Postgres connection
│  & Result Fetch     │    Auto-limiting for large result sets
└────────┬────────────┘
         ▼
┌─────────────────────┐
│  Answer Generation  │  ← Same cheap model
│  & Contextualization│    Applies domain knowledge to interpret results
│                     │    Adds caveats, confidence assessment
└────────┬────────────┘
         ▼
Structured QueryResult
```

**Domain knowledge prompt library:**

This is the critical new component in V2. A curated, structured collection of prompts and reference data that encodes expert knowledge:

- **Political landscape:** Current officeholders, announced candidates, upcoming elections, recent departures. Maintained by domain experts.
- **Campaign finance concepts:** Jargon definitions (war chest, bundling, dark money, etc.), filing types, contribution limits by office and cycle.
- **Data coverage documentation:** What our data includes and excludes, time ranges, known gaps. Prevents the system from confidently answering questions outside our data.
- **Common patterns:** Templates for how to interpret certain query results (e.g., "this candidate shows high receipts but also high debt — this often indicates...").

**Design constraints for the prompt library:**
- **Stable across data updates.** Political landscape entries are versioned and dated so they can be updated independently of data imports.
- **LLM-portable.** No model-specific prompt engineering. Use clear, factual language that works across models. Test with multiple backends.
- **Expert-maintainable.** Domain experts should be able to add and update entries without writing code. Consider a simple structured format (YAML or Markdown with frontmatter) stored in version control.

**SUQL integration:**

V1 uses SUQL (Structured and Unstructured Query Language) from Stanford's Oval lab for SQL generation with user-defined functions like `summary()` and `answer()`. V2 continues with SUQL as the NL→SQL layer, with the understanding that this choice will be re-evaluated as part of ongoing research.

**LLM backend:**

The MCP server's internal LLM usage (SQL translation, answer generation) targets **cheap, non-reasoning models:**
- Primary: Gemma 3 (locally hosted) or Gemini Flash 3 (API)
- The system must support swapping models via configuration, not code changes
- Cost per query is tracked and reported (continuing V1's litellm integration)

External callers who connect their own reasoning model to our MCP server handle their own orchestration costs.

### 2.4 Application Layer — Web UI

**Purpose:** The primary user-facing interface. A conversational chat application for exploring campaign finance data.

**Choice: LibreChat**

LibreChat is an open-source chat UI that supports multiple LLM backends, conversation management, and plugin/tool integrations. It is a strong fit because:
- Proven in production at scale (NYT and others)
- Supports MCP tool use natively — our MCP server plugs in directly
- Handles conversation management, message streaming, and UI polish
- Active open-source community
- Reduces custom frontend development significantly

**Integration architecture:**

```
┌─────────────────────────────────────────┐   ┌───────────────────────────┐
│  LibreChat                              │   │  Reasoning LLM            │
│  ┌───────────────┐  ┌───────────────┐   │   │  (Claude, GPT-4o, etc.)   │
│  │ Chat UI       │  │ Conversation  │   │   │  Configured or BYOL       │
│  │ (React)       │  │ Management    │   │   └───────────────────────────┘
│  └───────────────┘  └───────────────┘   │         ▲
│                                         │         │ API calls
│  ┌───────────────┐                      ├─────────┘
│  │ Datatalk MCP  │                      │
│  │ Server Plugin │                      │
│  └───────────────┘                      │
└─────────────────────────────────────────┘
```

The reasoning LLM (the expensive one) lives in the LibreChat layer, orchestrating tool calls to our MCP server. This is where the two-tier LLM strategy manifests:
- **LibreChat's LLM:** Reasoning model that understands user intent, chains multiple queries, synthesizes. This is the expensive call. For the default hosted experience, we pick one (e.g., Claude, GPT-4o). BYOL users configure their own.
- **MCP server's LLM:** Cheap model that translates structured tool calls into SQL. Called by the MCP server internally.

**Customizations on top of LibreChat:**
- Landing page with example queries, project description, data source information
- Custom branding for Stanford / Big Local News
- Evaluation results summary (link to methodology, aggregate stats)
- Default system prompt configured for campaign finance exploration
- Rate limiting for unauthenticated users

**Authentication (P2):**
- Logged-out: Full query functionality, usage throttling, no conversation persistence
- Logged-in: Saved conversations, potentially higher limits
- LibreChat has built-in auth support (local accounts, OAuth)

### 2.5 Evaluation System

**Purpose:** Measure and improve answer quality systematically. Produce trust artifacts for the public site.

**This is a research-informed component.** The design below is a starting framework. We will study best practices — particularly the Policing Project's evaluation methodology — and refine before implementation.

**Architecture:**

The evaluation system is a separate web application. It reads from the same database as the main system but writes to evaluation-specific tables.

```
┌─────────────────────────────────────┐
│  Evaluation Web App                 │
│  (Django admin-style interface)     │
│                                     │
│  ┌─────────┐  ┌──────────────────┐  │
│  │Question │  │  Rating &        │  │
│  │Browser  │  │  Annotation UI   │  │
│  └────┬────┘  └────────┬─────────┘  │
│       │                │            │
│  ┌────▼────────────────▼─────────┐  │
│  │  Evaluation Database Tables   │  │
│  │  eval_questions               │  │
│  │  eval_runs                    │  │
│  │  eval_ratings                 │  │
│  │  eval_annotations             │  │
│  └───────────────────────────────┘  │
│                                     │
│  ┌───────────────────────────────┐  │
│  │  Reporting Dashboard          │  │
│  │  Aggregate metrics over time  │  │
│  │  Regression detection         │  │
│  └───────────────────────────────┘  │
└─────────────────────────────────────┘
```

**Evaluation workflow (draft — subject to research):**

1. **Question curation.** Maintain a benchmark set of questions spanning different categories (candidate-specific, race-level, historical, jargon-heavy, out-of-scope). Domain experts contribute questions. Some are "gold standard" with known-correct answers.

2. **Automated runs.** When data or models change, the system re-runs the benchmark suite and records answers. This is the regression detection mechanism.

3. **Human evaluation.** Evaluators (volunteers, paid journalists, domain experts) review question-answer pairs and rate them on dimensions such as:
   - Factual accuracy
   - Completeness
   - Appropriate context / caveats
   - Source attribution quality
   - Helpfulness

4. **Annotation.** Evaluators can annotate with corrections ("the answer should mention that...") that feed back into domain knowledge improvements.

5. **Reporting.** Aggregate metrics are computed and tracked over time. Public-facing summaries are generated for the website.

**Concurrency:** The system must support ~10 simultaneous evaluators without data loss. Standard database transactions and optimistic locking are sufficient at this scale — no need for real-time collaboration features.

**Access control:** Evaluation app is behind authentication. Not publicly accessible. Separate from the main site's auth.

---

## 3. Technology Stack

| Layer | Choice | Rationale |
|-------|--------|-----------|
| **Language** | Python 3.12+ | Team expertise; rich ML/LLM ecosystem; V1 continuity |
| **Web framework** | Django | Team familiarity; built-in admin, auth, ORM; good fit for evaluation app and data import management |
| **Chat UI** | LibreChat | Proven MCP support; conversation management; auth built-in |
| **Database** | PostgreSQL | Relational workload; SUQL compatibility; managed service on all clouds |
| **Cache** | Redis | Entity linking cache (V1 continuity); session cache |
| **NL→SQL** | SUQL | V1 continuity; Stanford Oval integration; under evaluation |
| **LLM (SQL layer)** | Gemma 3 / Gemini Flash 3 | Low cost; non-reasoning; swappable via config |
| **LLM (reasoning)** | Configurable (Claude, GPT-4o, etc.) | Runs in LibreChat; user can bring their own |
| **LLM abstraction** | litellm | Multi-provider support; cost tracking (V1 continuity) |
| **MCP framework** | Python MCP SDK | Standard protocol implementation |
| **Containers** | Docker + Docker Compose | Cloud-portable; reproducible dev environment |
| **Package manager** | uv | Fast, modern Python tooling (V1 continuity) |

### Python version note

V1 targets Python 3.14 (set in `.python-version`). For V2 we should target the latest stable release at development time (likely 3.12 or 3.13) rather than a pre-release version, to maximize library compatibility.

---

## 4. Infrastructure & Deployment

### 4.1 Environments

| Environment | Purpose | Infrastructure |
|-------------|---------|---------------|
| **Local dev** | Individual developer machines | Docker Compose: Postgres, Redis, LibreChat, MCP server, eval app. Single `docker compose up` to start everything. |
| **Staging** | Pre-production testing. New data, new models, new code validated here before release. | Cloud-hosted, mirrors production topology but smaller instances. |
| **Production** | Public-facing site at datatalk.genie.stanford.edu | Cloud-hosted with monitoring, backups, and scale-up capability. |

### 4.2 Cloud Portability

The project's cloud provider may change as funding evolves. To avoid lock-in:

- **Containerize everything.** All services run in Docker containers orchestrated by Docker Compose (dev) or a lightweight orchestrator (staging/production).
- **Use managed Postgres and Redis** but through standard connection strings, not provider-specific SDKs.
- **Store secrets in environment variables**, not cloud-specific secret managers (or use a thin abstraction).
- **Avoid cloud-specific services** for core functionality (no Lambda, no Cloud Functions, no proprietary queues). Standard cron + containers for scheduling.
- **Infrastructure as code.** Use Terraform or similar to define infrastructure, making it reproducible across providers.

### 4.3 Deployment Architecture (Production)

```
┌─────────────────────────────────────────────────┐
│  Reverse Proxy / Load Balancer                  │
│  (nginx or cloud LB)                            │
│  - TLS termination                              │
│  - Rate limiting (unauthenticated users)        │
│  - Static asset serving                         │
└──────────┬──────────────┬───────────────────────┘
           │              │
    ┌──────▼──────┐ ┌─────▼──────┐
    │  LibreChat  │ │  Eval App  │
    │  (chat UI)  │ │  (Django)  │
    │  Port 3080  │ │  Port 8000 │
    └──────┬──────┘ └─────┬──────┘
           │              │
    ┌──────▼──────────────▼──────┐
    │  MCP Server                │
    │  (Campaign Finance)        │
    │  Port 8080                 │
    └──────┬─────────────────────┘
           │
    ┌──────▼──────┐  ┌──────────┐
    │  PostgreSQL │  │  Redis   │
    │  (managed)  │  │ (managed)│
    └─────────────┘  └──────────┘
```

### 4.4 Reliability & Operations

| Concern | Approach |
|---------|----------|
| **Backups** | Automated daily Postgres backups with point-in-time recovery. Retain 30 days. |
| **Monitoring** | Application metrics (response times, error rates, LLM costs) via Prometheus + Grafana or equivalent. Health check endpoints on all services. |
| **Logging** | Structured JSON logging to stdout from all containers. Aggregated via cloud logging service or ELK. |
| **Alerting** | Page on: service down, error rate spike, database connection failures. Notify on: LLM cost anomalies, data import failures. |
| **Scale-up** | Horizontal scaling of LibreChat and MCP server containers behind load balancer. Postgres read replicas if read load grows. |
| **Abuse protection** | Rate limiting at the reverse proxy layer. Per-IP throttling for unauthenticated users. Request size limits. LLM cost circuit breakers (halt queries if daily spend exceeds threshold). |
| **Uptime target** | 99.5% — acceptable for a research/public-interest tool. Maintenance windows are fine with notice. |

### 4.5 Cost Management

Two cost categories require active monitoring:

**LLM costs:**
- Cheap model (SQL translation): cost per query tracked via litellm. Target: keep this negligible by using small/local models.
- Reasoning model (orchestration): more expensive. For the hosted experience, budget is TBD. Circuit breaker halts service if daily spend exceeds threshold.
- BYOL users bear their own reasoning model costs.

**Hosting costs:**
- Managed Postgres and Redis are the primary fixed costs
- Compute scales with traffic — container orchestration allows right-sizing
- Monthly cost report generated and reviewed

---

## 5. Development Workflow

### 5.1 Local Development

```bash
# Clone and setup
git clone <repo>
cd datatalk
cp .env.example .env  # Configure API keys

# Start all services
docker compose up

# Run just the MCP server for development
uv run python -m datatalk.mcp_server

# Run evaluation app
uv run python manage.py runserver
```

Goal: a new developer runs the full stack in under an hour, including sample data.

### 5.2 Project Structure (Proposed)

```
datatalk/
├── docker-compose.yml        # Full local stack
├── pyproject.toml
├── README.md
│
├── docs/
│   ├── PRD-v2.md
│   └── Design-v2.md
│
├── datatalk/                 # Main Python package
│   ├── mcp_server/           # MCP server implementation
│   │   ├── server.py         # MCP tool definitions
│   │   ├── tools/            # Tool implementations
│   │   └── prompts/          # Domain knowledge prompt library
│   │
│   ├── pipeline/             # Data import pipelines
│   │   ├── scrapers/         # Per-source scrapers (fec.py, opensecrets.py)
│   │   ├── staging.py        # Staging table management
│   │   └── certification.py  # Review/promote workflow
│   │
│   ├── nlsql/                # NL→SQL engine
│   │   ├── translator.py     # SUQL-based translation
│   │   ├── entity_linking.py # Entity resolution
│   │   └── executor.py       # SQL execution and result formatting
│   │
│   └── evaluation/           # Evaluation Django app
│       ├── models.py         # Eval data models
│       ├── views.py          # Evaluator-facing views
│       └── reporting.py      # Metrics and public trust artifacts
│
├── librechat/                # LibreChat configuration and customization
│   ├── librechat.yaml        # LibreChat config
│   └── custom/               # Branding, landing page, system prompts
│
├── tests/
│   ├── test_mcp_tools.py
│   ├── test_pipeline.py
│   ├── test_nlsql.py
│   └── benchmarks/           # Evaluation benchmark question sets
│
├── scripts/                  # Operational scripts
│   ├── import_fec.py
│   ├── import_opensecrets.py
│   └── run_benchmark.py
│
└── infra/                    # Infrastructure as code
    ├── docker/               # Dockerfiles per service
    ├── terraform/            # Cloud infrastructure (when ready)
    └── nginx/                # Reverse proxy config
```

### 5.3 Release Process

1. **Feature development** on feature branches
2. **CI checks:** tests, linting, type checking (run on every push)
3. **PR review and merge** to main
4. **Deploy to staging** automatically on merge to main
5. **Run benchmark suite** against staging — check for regressions
6. **Promote to production** manually after staging validation

### 5.4 Testing Strategy

| Level | What | How |
|-------|------|-----|
| **Unit tests** | NL→SQL translation, entity linking, data import logic | pytest, mocked LLM responses for deterministic testing |
| **Integration tests** | MCP tool end-to-end (question → structured result) | pytest with test database, real LLM calls (or recorded responses) |
| **Benchmark suite** | Full system evaluation against curated question set | Automated runs on staging; results fed into evaluation dashboard |
| **Manual evaluation** | Human judgment on answer quality | Evaluation web app with trained evaluators |

---

## 6. Migration from V1

V1 is a working system. The migration approach:

1. **Retain and refactor** the ingestion pipeline. The type detection and schema inference code is solid. Wrap it in the new staging/certification workflow.
2. **Retain** the SUQL-based NL→SQL engine and entity linking. Refactor into the new package structure under `datatalk/nlsql/`.
3. **Replace** the Flask frontend with LibreChat.
4. **New:** MCP server wrapping the NL→SQL engine.
5. **New:** Evaluation system.
6. **New:** Domain knowledge prompt library.
7. **New:** Docker-based development and deployment.

The V1 agent pipeline (`agent/kraken/`) — with its LangGraph state machine, controller prompts, and reporter — is the foundation for the MCP server's internal pipeline. The refactoring wraps this in MCP tool interfaces and adds the domain knowledge layer.

---

## 7. Open Design Questions

| # | Question | Impact | When to Resolve |
|---|----------|--------|----------------|
| 1 | SUQL vs. alternatives for NL→SQL | Core pipeline architecture | Early — before major refactoring |
| 2 | LibreChat MCP integration specifics | Application layer design | During prototyping |
| 3 | Evaluation methodology and dimensions | Evaluation system design | Research phase — before eval app build |
| 4 | Locally-hosted vs. API-only for cheap LLM | Infrastructure and cost | During model evaluation |
| 5 | Domain knowledge format (YAML, Markdown, database) | Prompt library design | During domain expert onboarding |
| 6 | FEC/OpenSecrets API specifics and rate limits | Pipeline design | During pipeline development |
| 7 | Cloud provider selection | Infrastructure | When funding is determined |
| 8 | Django vs. lighter framework for eval app | Evaluation system | Early — framework choice cascades |

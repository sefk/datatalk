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

- **Staging-then-promote pattern.** New data lands in staging tables first. The system computes a diff against the current production data. Incremental updates that pass automated checks (row counts within expected range, no schema changes, no statistical anomalies) are auto-certified and promoted without operator intervention. First imports, schema changes, and anomalous data are queued for manual operator review. This balances data safety with freshness — requiring manual certification for every import would create the same staleness problem we had in V1.

- **State tracking.** Each scraper maintains a high-water mark (last filing date, last API cursor, etc.) to support incremental imports. State is stored in the database alongside the data.

- **Operator notification.** When new data arrives in staging, the system notifies operators (email or webhook). The review interface shows what changed: new records, modified records, and summary statistics.

- **Replayability.** Import runs are logged with timestamps, source URLs, and record counts. A failed or bad import can be rolled back by replaying from the previous good state.

**Technology:**

- Python scripts, scheduled via cron or a lightweight task runner (e.g., Django management commands with cron, or Celery for more sophistication if needed)
- Consider [DSPy] for managing core prompts instead of simple text files. The potential benefits are avoiding the fragile prompt problem (prompts that don't survive model changes) and better revision workflow during eval. However, DSPy has a steep learning curve and significant API churn — it adds cognitive overhead for new contributors. We should defer adopting DSPy until we've actually hit prompt fragility in practice. The benchmark suite will tell us when we have a problem. Start with simple prompt files and migrate to DSPy later if needed.
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

**Stateless design:**

The MCP server is stateless — each tool call is self-contained with no session or conversation context. For multi-turn conversations (e.g., a user asks "Who are the top donors to Senate races?" then follows up with "What about California?"), the caller is responsible for reformulating follow-ups into self-contained questions. In practice, the reasoning LLM in LibreChat handles this naturally. This is a deliberate departure from V1, which threaded conversation history and entity linking results across turns inside the agent. The stateless approach is simpler, more portable across callers, and avoids coupling the MCP server to any particular conversation management system.

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
    answer_hint: str         # Brief natural language summary of the result
    data: list[dict]         # Supporting data rows
    sql_query: str           # Generated SQL (for transparency/debugging)
    confidence: str          # "high", "medium", "low"
    data_sources: list[str]  # Which datasets contributed
    data_freshness: str      # Timestamp of most recent data used
    caveats: list[str]       # Limitations, out-of-scope warnings
    suggested_followups: list[str]  # Recommended next questions
```

The `answer_hint` field is a lightweight summary generated by the MCP server's cheap model. It is not intended to be the final user-facing answer. Callers with their own reasoning LLM (LibreChat, BYOL users) will typically produce a more thorough answer by synthesizing the structured `data`, `caveats`, and `confidence` fields — potentially combining results from multiple tool calls or cross-referencing with other sources. The hint is useful for direct MCP callers that don't have a reasoning model, or as a fallback.

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

**Risk: cheap model cost and quality.** The V1 pipeline makes 5-15 LLM calls per query (schema exploration, entity linking, SQL generation, verification, reporting). Even with a cheap model, this adds up at scale. More critically, small models like Gemma 3 are unproven for complex SQL generation involving multi-table joins across campaign finance schemas — V1 uses GPT-4o for this. Moving to a much less capable model is a significant quality risk. Before committing to the two-tier cost model, we need to: (1) instrument the V1 pipeline to measure actual calls-per-query and cost-per-query, (2) benchmark candidate cheap models against our actual SQL workload, and (3) evaluate whether the multi-step schema exploration pattern is still necessary once we have a stable, well-documented schema — a simpler approach with the schema in-context might work with fewer calls.

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
3. **Replace chainlite** with direct litellm calls and Jinja2 prompt templates. chainlite is a Stanford-internal library (`stanford-oval/chainlite`, pinned to a specific git commit) that is deeply embedded in V1 — the `@chain` decorator and `llm_generation_chain` are used throughout the agent code. It is undocumented, niche, and a barrier to new contributors. litellm already provides multi-model support and cost tracking; Jinja2 handles prompt templating.
4. **Replace** the Flask frontend with LibreChat.
5. **New:** MCP server wrapping the NL→SQL engine.
6. **New:** Evaluation system.
7. **New:** Domain knowledge prompt library.
8. **New:** Docker-based development and deployment.

The V1 agent pipeline (`agent/kraken/`) — with its LangGraph state machine, controller prompts, and reporter — is the foundation for the MCP server's internal pipeline. The refactoring wraps this in MCP tool interfaces and adds the domain knowledge layer.

---

## 7. Development Plan

*This section was drafted by Claude Code (architect agent) based on a review of the V1 codebase and V2 design. Treat as a starting point for planning, not a committed schedule.*

### Scope honesty

Despite Section 6 framing this as "migration," V2 is effectively a rewrite. The agent code needs full repackaging (from `agent/kraken/` to `datatalk/nlsql/`). Core dependencies are being replaced (chainlite → litellm + Jinja2, chainlit → LibreChat, Flask → Node.js). The frontend is a different language entirely. Only the ingestion pipeline's type detection logic and some entity linking code can be truly lifted from V1. The rest is new work. Planning should reflect this reality.

### Phasing

Given the scope and a summer 2026 target, work should be phased to deliver value incrementally and resolve key unknowns early.

**Phase 0: Evaluate and prototype (weeks 1-4)**
Resolve the open questions that everything else depends on before building.
- Instrument V1 pipeline: measure calls-per-query, cost-per-query, identify where time is spent
- Benchmark candidate cheap models (Gemma 3, Flash 3, others) against actual SQL workload
- Audit SUQL: what does it provide vs. our custom agent code? Decision: adopt or drop
- Audit LangGraph: is the multi-step state machine necessary, or can a simpler loop work?
- Prototype LibreChat + MCP integration: can we customize the landing page without forking? Do large tool responses (data tables) work? What's the upgrade story?
- Prototype domain knowledge injection with concrete examples from the V1 failure cases (Schiff/gubernatorial, MTG/war chest)

**Phase 1: MCP server wrapping V1 agent (weeks 4-8)**
Get the core working end-to-end before replacing internals.
- Wrap the existing V1 agent pipeline in MCP tool interfaces
- Replace chainlite with litellm + Jinja2
- Implement the stateless design (caller reformulates follow-ups)
- Implement `answer_hint` response structure
- Stand up test database with current FEC/OpenSecrets data
- Build initial benchmark suite from known V1 failure cases

**Phase 2: Web UI and domain knowledge (weeks 6-12)**
The user-facing experience and the key quality improvement. Overlaps with Phase 1.
- Deploy LibreChat with MCP server integration
- Custom landing page, branding, example queries, system prompt
- Build initial domain knowledge prompt library (campaign finance concepts, data coverage docs)
- Hire domain expert(s); begin populating political landscape knowledge
- Rate limiting for unauthenticated users

**Phase 3: Data pipelines and evaluation (weeks 10-16)**
Freshness and quality assurance.
- Build FEC scraper with staging-then-promote pattern
- Build OpenSecrets scraper (contingent on data licensing — see risks)
- Operator notification and review interface
- Evaluation web app (Django or alternative per open question #9)
- Recruit initial evaluators
- First benchmark run with evaluation workflow

**Phase 4: Hardening and launch (weeks 14-20)**
Production readiness.
- Docker-based deployment for staging and production
- Monitoring, logging, alerting
- Backup and recovery procedures
- Security review (MCP query guardrails, abuse protection)
- Publish methodology and evaluation results on the site
- Load testing and cost projections

### Key risks to the timeline

- **OpenSecrets data licensing** is unresolved. If they don't grant permission, the data story weakens and we need more time to make raw FEC data useful.
- **Cheap model quality** may not be sufficient. If benchmarking shows small models can't handle our SQL complexity, the cost model changes and we may need to stay on expensive models longer.
- **LibreChat customization** may prove too rigid, requiring more frontend work than planned.
- **Solo developer** scaling to a team mid-project introduces onboarding overhead.

---

## 8. Open Design Questions

### High Priority — blocks architecture or other work

### 8.1 Cheap Model Benchmark
**Priority:** High

**Description:** The V1 pipeline makes 5-15 LLM calls per query. Even with a cheap model, this adds up. Small models like Gemma 3 are unproven for complex SQL generation with multi-table joins. We need empirical data before committing to the two-tier cost model.

**Impact:** Core cost model and answer quality. If cheap models can't handle the SQL complexity, the whole architecture changes.

**When and How to Resolve:** Phase 0. Instrument V1 to measure calls-per-query and cost-per-query. Run the benchmark suite with candidate cheap models and compare quality against GPT-4o.

### 8.2 SUQL vs. Alternatives for NL→SQL
**Priority:** High

**Description:** SUQL is currently disabled in V1 (`suql_enabled = False`). SQL generation is done by the LangGraph agent with direct LLM prompting. Adopting SUQL for V2 would be adding a new dependency, not continuing an existing one. We need to audit what SUQL actually provides vs. what our custom agent code provides, then make a clear call: go deeper or drop it.

**Impact:** Core pipeline architecture.

**When and How to Resolve:** Phase 0. Audit V1 code, talk to the SUQL team at Stanford Oval, and compare approaches on the benchmark suite.

### 8.3 LibreChat MCP Integration
**Priority:** High

**Description:** Can we customize the landing page without forking LibreChat? Does MCP tool use work with large responses (full data tables)? What's the upgrade story when LibreChat releases new versions? LibreChat is a full service with its own MongoDB, Node.js runtime, and release cadence — we're coupling our deployment to theirs.

**Impact:** Application layer design, operational burden. If LibreChat proves too rigid, the entire app layer plan changes.

**When and How to Resolve:** Phase 0. Build a spike with LibreChat + our MCP server and answer these questions before committing.

### 8.4 Is LangGraph Still Needed?
**Priority:** High

**Description:** V1 uses LangGraph for a multi-step agent state machine that dynamically explores the schema. With a stable, well-documented schema, this exploration may be unnecessary. A simpler loop or direct prompt-with-schema approach could reduce calls-per-query and drop a heavyweight dependency (LangGraph + LangChain, already version-pinned and outdated in V1).

**Impact:** Dependency surface, complexity, cost.

**When and How to Resolve:** Phase 0, alongside the cheap model benchmark (#8.1). If a simpler approach works with fewer calls, LangGraph adds complexity for no benefit.

### 8.5 Domain Knowledge Injection Mechanics
**Priority:** High

**Description:** How concretely does domain knowledge get into prompts? Is the full library included in every call (context window cost) or retrieved selectively (needs a retrieval mechanism like RAG or keyword matching)? How large can the library grow before hitting context limits? V1's `domain_specific_instructions` mechanism (a CSV file with table-triggered rules) is simple but brittle — what's the concrete upgrade path?

**Impact:** Answer quality, LLM cost, scalability of domain knowledge. This is the core V2 quality improvement.

**When and How to Resolve:** Phase 2 prototyping. Start with the concrete V1 failure cases (Schiff/gubernatorial, MTG/war chest) and prototype domain knowledge injection that actually fixes them. Let that drive the format and retrieval design.

### Medium Priority — important, can resolve in parallel

### 8.6 Evaluation Methodology and Dimensions
**Priority:** Medium

**Description:** What are best practices for evaluating NL query systems? How does the Policing Project structure their evaluation? What dimensions should we rate on (accuracy, completeness, context, sourcing, helpfulness)? How do we recruit, train, and retain evaluators?

**Impact:** Evaluation system design. The eval system is P1 but useless without a sound methodology and actual evaluators.

**When and How to Resolve:** Research phase — before building the eval app. Review Policing Project and other references, draft a methodology, pilot with a small set of questions.

### 8.7 MCP Server Security Model
**Priority:** Medium

**Description:** The MCP server accepts natural language and generates SQL. Even with a read-only database connection, there are risks: prompt injection leading to SQL injection, expensive queries (full table scans, cartesian joins) as denial-of-service, and potential data exfiltration if the schema contains anything sensitive. Do we need query timeouts, row limits, query plan analysis, or a SQL allowlist?

**Impact:** Security, reliability.

**When and How to Resolve:** Design guardrails during the MCP server build (Phase 1), security review during hardening (Phase 4). Must be resolved before production launch.

### 8.8 LLM Pipeline Observability
**Priority:** Medium

**Description:** The design covers app-level metrics (Prometheus/Grafana) but not per-query traces through the LLM pipeline: which queries fail, why SQL generation went wrong, how many retries per query, where latency concentrates in the multi-step pipeline. This data is critical for both operational debugging and feeding the evaluation system.

**Impact:** Quality improvement, debugging, evaluation.

**When and How to Resolve:** During MCP server build (Phase 1). Build structured logging into the pipeline from the start — every query should produce a trace with timing, model calls, SQL generated, and results returned.

### 8.9 Django vs. Lighter Framework for Eval App
**Priority:** Medium

**Description:** Django brings admin, auth, and ORM out of the box but introduces a second web framework alongside LibreChat's Node.js stack. A lighter alternative (FastAPI + htmx) would reduce surface area but requires building more from scratch. Also consider: could the eval UI be a protected section within LibreChat (using its existing auth) rather than a separate application?

**Impact:** Evaluation system design, operational burden.

**When and How to Resolve:** Early — framework choice cascades into many subsequent decisions.

### Low Priority — can defer without blocking

### 8.10 Locally-Hosted vs. API-Only for Cheap LLM
**Priority:** Low

**Description:** Running a local model (Gemma 3) requires GPU instances, which vary dramatically across cloud providers in availability, pricing, and configuration. This is the single biggest cloud portability risk. An API-based model (Gemini Flash) is far more portable.

**Impact:** Infrastructure, cost, cloud portability.

**When and How to Resolve:** During model evaluation. Default to API-based model; only design infrastructure around local serving if the cost difference is compelling and tested.

### 8.11 Domain Knowledge Format
**Priority:** Low

**Description:** How should curated domain knowledge be structured? YAML, Markdown with frontmatter, database records? Domain experts need to maintain it without writing code.

**Impact:** Prompt library design, expert workflow.

**When and How to Resolve:** During domain expert onboarding. Start simple (Markdown files in version control), evolve based on what experts actually need.

### 8.12 FEC/OpenSecrets API Specifics
**Priority:** Low

**Description:** What are the FEC and OpenSecrets API rate limits and update schedules? Should we use FEC bulk data downloads or the API for incremental updates?

**Impact:** Pipeline design, data freshness targets.

**When and How to Resolve:** During pipeline development. Research the APIs, talk to other projects that scrape these sources.

### 8.13 Cloud Provider Selection
**Priority:** Low

**Description:** Currently on Azure credits but funding may change. Need to avoid lock-in while still making a hosting decision.

**Impact:** Infrastructure, cost.

**When and How to Resolve:** When funding is determined. The containerized architecture keeps options open in the meantime.

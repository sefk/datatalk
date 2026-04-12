# Datatalk v2 — Product Requirements Document

**Project:** Datatalk — Natural Language Campaign Finance Explorer  
**Owner:** Big Local News / Stanford  
**Author:** Sef Kloninger <sefklon@gmail.com>
**Status:** Draft  
**Target Launch:** Summer 2026  
**URL:** https://www.datatalk.genie.stanford.edu/

---

## 1. Overview

Datatalk is a public web application that enables citizens, journalists, and researchers to explore U.S. campaign finance data using natural language. Users ask questions in plain English — "Who are the top donors to California Senate races?" — and receive accurate, sourced, explainable answers backed by authoritative datasets.

Version 1 demonstrated the core NL-to-SQL concept. Version 2 focuses on making the system **trustworthy**, **current**, and **usable** — moving from a technology demonstration to a reliable public resource.

## 2. Target Users

| User | Needs | Example |
|------|-------|---------|
| **Journalists** | Quick, accurate answers to specific campaign finance questions during reporting. Trust that answers are faithful to source data. | "How much has the top PAC supporting the Texas governor's race raised this cycle?" |
| **Citizens** | Accessible exploration of election funding without technical skills. Confidence in the system's neutrality and accuracy. | "Who is funding my congressional representative?" |
| **Researchers** | Programmatic access to structured campaign finance data with provenance. Ability to use their own analytical tools. | Connecting Datatalk's MCP server to their own LLM or analysis pipeline. |
| **Evaluators** | Ability to assess and improve answer quality. Domain experts, journalists, or volunteers who review system outputs. | Rating answer accuracy, flagging incorrect domain assumptions, contributing gold-standard Q&A pairs. |

## 3. Problem Statement

Campaign finance data is public but difficult to use. The FEC and OpenSecrets provide raw data, but exploring it requires technical skills (SQL, data wrangling) and domain knowledge (election cycles, filing types, committee structures, political jargon). Datatalk bridges this gap.

### What v1 got right
- Core NL-to-SQL pipeline works
- Handles follow-up questions in a conversational flow
- Entity linking resolves candidate and committee names

### What v1 got wrong

**Insufficient domain knowledge.** The system lacks understanding of the political landscape beyond what's in the database schema:

> **Q:** "Who is the top fund-raiser for the upcoming California gubernatorial election?"  
> **A:** "Adam Schiff" — He's the current junior senator, not a gubernatorial candidate. The system doesn't know what races exist, who is running, or that this race isn't in our data.

> **Q:** "How large is Marjorie Taylor Greene's war chest?"  
> **A:** Correctly interprets the jargon, but doesn't note that she's no longer in Congress or running for office — context that any knowledgeable human would provide.

**No evaluation system.** There is no systematic way to measure answer quality, catch regressions, or incorporate expert feedback.

**Stale data.** Data was loaded once. There is no pipeline for ongoing updates as new filings and disclosures arrive.

**Too much implementation leakage.** The current UI exposes SQL queries and technical details that confuse non-technical users and distract from the answers themselves.

## 4. Goals and Priorities

### P1 — Must Have for Launch

| # | Goal | Success Criteria |
|---|------|-----------------|
| 1 | **Better answers** | Domain experts rate >90% of answers to benchmark questions as accurate and contextually appropriate. System provides relevant caveats (e.g., "this candidate is not running in this race") when applicable. |
| 2 | **Fresher data** | Automated pipelines ingest new data from FEC and OpenSecrets. Site operators are notified when new data arrives and can inspect/certify before it goes live. |
| 3 | **Evaluation system** | A dedicated evaluation interface where reviewers can assess answer quality. Aggregate evaluation metrics are published on the site to build user trust. Process and methodology are documented publicly. |
| 4 | **Focus on answers** | Default UI shows natural language answers with source attribution. SQL and technical details are hidden unless explicitly requested. |

### P2 — Important but Can Follow Launch

| # | Goal | Success Criteria |
|---|------|-----------------|
| 5 | **MCP integration** | A public MCP server allows external tools and LLMs to query campaign finance data programmatically. Enables "bring your own LLM" workflows. |
| 6 | **User accounts** | Logged-in users can save conversation history and may receive higher usage limits. |

### Non-Goals for v2

- Mobile-native experience (responsive web is sufficient)
- Real-time data (near-daily freshness is the target, not streaming)
- Covering non-U.S. elections
- DIME dataset integration (aspirational; may be added if licensing is secured)

## 5. Data Requirements

### 5.1 Data Sources

| Source | Description | Status |
|--------|-------------|--------|
| **[FEC]** | Official federal campaign finance filings: contributions, expenditures, candidates, committees | Continuing from v1. Publicly available for any use. |
| **[OpenSecrets]** | Aggregated and enriched campaign finance data, PAC tracking, lobbying | Continuing from v1. Unclear what discussions or approvals we've had with them to date. |
| **[DIME] (Adam Bonica)** | Database on Ideology, Money in Politics, and Elections — research dataset with ideological scores | Haven't approached Prof. Bonica yet. Even though license is permisive (attribution only), would still want his OK first. |

[FEC]: https://data.fec.gov/
[OpenSecrets]: https://opensecrets.org
[DIME]: https://data.stanford.edu/dime

### 5.2 Data Freshness

The system must support automated, recurring data imports — not one-time bulk loads. Key requirements:

- **Stateful pipelines** that detect when new data is available from upstream sources
- **Operator notification** when new data arrives, with ability to inspect before publishing
- **Non-destructive updates** — new data supplements rather than replaces existing data, preserving query consistency
- **Audit trail** — record of what data was imported, when, and from where

The update cadence for each source is a research topic. The system should be designed to support daily checks even if actual updates are less frequent.

### 5.3 Domain Knowledge

Beyond the raw data, the system needs curated domain knowledge to provide contextually appropriate answers. This includes:

- **Campaign finance concepts:** Filing periods, contribution limits, PAC types, dark money, bundling, war chests, and other jargon
- **Data limitations:** What our data covers and doesn't cover, which races and time periods are represented

We won't be able to maintain information about the **current political landscape** in our system. This changes too often and we don't have staff to respond quickly. Given that, we should include in our own system prompts and MCP results give hints when things should be cross-checked with external sources. Websearch on Wikipedia will likely be sufficient.

This knowledge will be maintained by hired domain experts and stored as structured prompt material that is stable across data updates and applicable across different LLM backends.

## 6. Functional Requirements

### 6.1 Natural Language Query Interface

The primary user interaction is a conversational chat interface.

- Users type questions in natural language
- The system returns answers in natural language with supporting data (tables, figures as appropriate)
- Answers include source attribution (which dataset, which filing)
- Answers include relevant caveats and context drawn from domain knowledge
- The system suggests follow-up questions to guide exploration
- Multi-turn conversations maintain context
- Technical details (SQL, query plans) are hidden by default
- An optional "verbose mode" may expose implementation details for power users or debugging

### 6.2 Evaluation Interface

A separate, non-public interface for quality assessment.

- **Access control:** Only authorized evaluators and project engineers; never accessible to the general public
- **Concurrent use:** Support up to ~10 simultaneous evaluators without data loss
- **Evaluation workflow:** Research topic — design should accommodate evolving methodology. Reference: the Policing Project's evaluation systems as a model for thoroughness.
- **Core capabilities:**
  - Present question-answer pairs for review
  - Capture quality ratings across relevant dimensions (accuracy, completeness, context, etc.)
  - Allow evaluators to provide corrections and explanations
  - Track evaluation coverage — which questions and answer types have been assessed
- **Metrics and reporting:**
  - Aggregate quality metrics over time
  - Breakdown by question type, data source, or topic area
  - Regression detection when data or models change
- **Public trust artifacts:**
  - Description of evaluation methodology published on the site
  - Aggregate statistics (e.g., "94% accuracy on benchmark suite, reviewed by N domain experts") suitable for public claims

### 6.3 MCP Server (P2)

A Model Context Protocol server exposing campaign finance data to external tools.

- **High-context tools** that accept natural language queries and return structured results:
  - `query_campaign_finance(question: str)` — General-purpose query
  - `get_candidate_summary(name: str, cycle: str)` — Candidate funding profile
  - `get_race_overview(office: str, state: str, cycle: str)` — Race-level funding summary
  - `compare_candidates(names: list[str], metric: str)` — Side-by-side comparison
- **Structured responses** including:
  - The answer in natural language
  - Supporting data tables
  - Confidence level
  - Data sources and freshness timestamps
  - Caveats or limitations
  - Suggested follow-up queries
- The MCP server uses the same backend LLM pipeline as the web interface — queries are translated to SQL internally, not exposed to the caller
- Enables "bring your own LLM" — users can connect their preferred reasoning model to our MCP server

### 6.4 Public Website

- Hosted at a Stanford domain (currently datatalk.genie.stanford.edu)
- **Logged-out mode (P1):** Fully functional query interface with usage throttling to prevent abuse
- **Logged-in mode (P2):** Persistent conversation history, potentially higher usage limits
- Example queries prominently displayed to guide new users
- "About" section describing data sources, methodology, evaluation results, and the team
- Responsive design suitable for desktop and tablet use

## 7. Trust and Transparency

Trust is a core product value, not a nice-to-have. Users are making judgments about elections based on our answers.

- **Source attribution:** Every answer traces back to specific data sources
- **Honest uncertainty:** The system says "I don't know" or "our data doesn't cover this" rather than guessing
- **Published methodology:** How the system works, what data it uses, how it's evaluated
- **Published evaluation results:** Aggregate quality metrics from the evaluation system
- **Domain expert involvement:** Answers incorporate knowledge from hired domain experts; their involvement is part of the public trust story
- **Open source:** The codebase remains public (current repo)

## 8. Success Metrics

| Metric | Target | How Measured |
|--------|--------|-------------|
| Answer accuracy | >90% on benchmark suite | Evaluation system ratings |
| Appropriate caveats | System flags data limitations in >95% of out-of-scope questions | Evaluation system |
| Data freshness | New FEC filings reflected within 1 week of publication | Pipeline monitoring |
| Uptime | 99.5% | Infrastructure monitoring |
| Evaluator throughput | 50+ question-answer pairs evaluated per week | Evaluation system metrics |
| Response latency | <15 seconds for typical queries | Application monitoring |
| LLM cost per query | Tracked and reported; target TBD based on budget | Cost monitoring |

## 9. Competitive Landscape

Understanding the competitive landscape is essential for positioning Datatalk and making informed prioritization decisions. Users exploring campaign finance data today have several options, each with different strengths and limitations.

### 9.1 Direct Competitors: Conversational Interfaces to Campaign Finance Data

There are currently **no established products** that offer a natural language conversational interface specifically for U.S. campaign finance data. This represents a genuine gap in the market. Several university research projects have explored NL-to-SQL for government data (including Stanford OVAL's prior work), but none are maintained as public products with domain knowledge and ongoing data updates.

Datatalk has a first-mover opportunity in the conversational campaign finance space. The risk is not being beaten by a direct competitor — it is that users find "good enough" alternatives before we reach sufficient quality.

### 9.2 Existing Campaign Finance Platforms

These are the tools journalists and researchers use today. They are Datatalk's primary competition for user attention, even though they are not conversational.

| Product | Strengths | Weaknesses | Datatalk Differentiation |
|---------|-----------|------------|--------------------------|
| **[OpenSecrets]** | Best-in-class enriched data: donor lookups, industry categorization, lobbying, PAC tracking. Trusted brand among journalists. Excellent pre-built reports. | Not conversational. Requires users to know what they're looking for. Custom queries require bulk data + SQL skills. | Datatalk answers freeform questions that would require multiple OpenSecrets page navigations. If we use their data, we provide a better interface to their enrichment. |
| **[FEC.gov][FEC]** | Authoritative primary source. Real-time filings. Complete federal data. | Notoriously difficult to use. Search is keyword-based and schema-aware. Requires understanding of filing types, committee structures, and FEC jargon. | Datatalk translates plain English into correct FEC data queries, handling jargon and entity resolution automatically. |
| **[FollowTheMoney]** | State-level campaign finance data — an area where FEC has no coverage. Good cross-state comparisons. | Federal data is not its focus. Interface is dated. Not conversational. | Complementary, not competitive, at the federal level. Could be a future data source for state coverage. |
| **[ProPublica Campaign Finance API][ProPublica]** | Well-structured API for developers. Clean data model. | API-only — requires programming skills. ProPublica has deprioritized some civic data tools in recent years. | Datatalk serves non-technical users that ProPublica's API cannot reach. |
| **Newsroom-internal tools** | Major outlets (NYT, WaPo, AP) have internal campaign finance databases and tools built by their data teams. | Not public. Each newsroom builds its own. Smaller newsrooms have nothing. | Datatalk democratizes access. The journalist at a local paper gets the same analytical capability as the NYT data team. |

[OpenSecrets]: https://www.opensecrets.org
[FEC]: https://www.fec.gov
[FollowTheMoney]: https://www.followthemoney.org
[ProPublica]: https://projects.propublica.org/api-docs/campaign-finance/

### 9.3 General-Purpose AI Tools

This is arguably Datatalk's most important competitive threat. A journalist in 2026 can ask ChatGPT or Perplexity the same question they would ask Datatalk.

| Product | Strengths | Weaknesses | Datatalk Differentiation |
|---------|-----------|------------|--------------------------|
| **ChatGPT** (with web search) | Extremely capable reasoning. Can search the web and synthesize. Most journalists already have access. | No guaranteed data provenance. May hallucinate dollar amounts. Cannot query structured FEC data. "It said so" is not a citation. | Datatalk queries authoritative structured data with source attribution. A journalist can trace an answer to a specific filing. |
| **Perplexity AI** | Fast, cited web search. Shows sources. Good for fact-checking. | Citations are to web pages, not primary data. Cannot run SQL against FEC data. May cite secondary sources that are themselves wrong. | Datatalk queries primary data; Perplexity searches secondary sources. |
| **Claude / Gemini with tool use** | Strong reasoning. If connected to Datatalk's MCP server, could be the best of both worlds. | Without our MCP server, has no structured access to campaign finance data. Same provenance problems as ChatGPT. | This is the BYOL opportunity. Claude + Datatalk MCP = a power-user workflow where our data is the authoritative backend. |

**The "good enough" risk:** General-purpose AI tools provide *approximate* answers to many campaign finance questions. For casual users, this may be sufficient. Datatalk's competitive moat is **precision, provenance, and domain expertise** — the answer is not just plausible, it is correct, sourced, and contextualized. This moat only holds if our answer quality is demonstrably superior and our trust artifacts (source attribution, evaluation scores, methodology) make the difference visible.

### 9.4 Strategic Positioning

Datatalk is the only tool combining conversational natural language access with deep campaign finance domain expertise. Our key strategic implications:

1. **Our primary competition is general-purpose AI, not existing finance tools.** Journalists will compare us to ChatGPT, not to FEC.gov. We must be clearly better on accuracy, sourcing, and domain context — and make that superiority visible through trust artifacts and published evaluation results.

2. **OpenSecrets is a potential partner, not just a data source.** We provide a better interface to their data; they provide the enrichment that makes our answers useful. This relationship needs active management.

3. **MCP is a platform play.** By exposing our data and domain knowledge via MCP, we become infrastructure that other tools build on. A journalist using Claude can connect to our MCP server and get authoritative answers within their preferred tool. This extends our reach beyond our own website.

4. **The equity argument is our strongest positioning.** The NYT data team can build their own tools. A local reporter in a swing district cannot. Datatalk democratizes access to campaign finance analysis. This resonates with foundations, Stanford's mission, and press coverage.

5. **Stanford affiliation is a trust asset.** In a landscape where AI answers are distrusted, "built at Stanford, evaluated by domain experts, open source, with published methodology" is a meaningful trust signal.

## 10. Open Research Questions

These topics require investigation before or during implementation:

1. **Data update cadence:** How often do FEC and OpenSecrets publish new data? What's the right scraping frequency?
2. **Evaluation methodology:** What are best practices for evaluating NL query systems? How does the Policing Project structure their evaluation? What dimensions should we rate on?
3. **Domain knowledge representation:** How should curated domain knowledge be structured to be stable across data updates and portable across LLMs?
4. **SUQL fitness:** Is SUQL still the right abstraction for NL-to-SQL, or should we evaluate alternatives?
5. **LLM model selection:** Which models best balance cost and quality for the SQL translation layer vs. the reasoning/orchestration layer?
6. **DIME integration:** Is licensing feasible? How does the DIME schema relate to FEC/OpenSecrets data?

---

## Appendix: Claude AI Review (Product Manager Perspective)

*Generated 2026-04-12 by Claude Opus 4.6 in the prod-mgr agent role. Items here are suggestions for future PRD revisions, not yet incorporated into the main document.*

### User journeys need concrete workflows

The user table lists types and abstract needs but not end-to-end workflows. For journalists specifically: what happens between getting an answer and publishing? Do they need citation formatting, export, shareable links, print-friendly views? The journalist use case — "reporter on deadline verifies a claim about a candidate's top donors" — should be traced step by step. This gap drives UI feature decisions.

### Citizen persona may be aspirational

The PRD lists citizens as a target user but provides little specificity about what they do with answers. Citizens have very different trust requirements than journalists — they may not know what "FEC filing" means. If citizens are truly a target, the PRD should address assumed data literacy, whether answers need simplification, and whether there should be a guided exploration mode. If citizens are secondary to journalists and researchers, be honest about that.

### Trust mechanisms need UI specifics

Section 7 lists trust values but does not specify what the user sees. What does source attribution look like — a footnote, a clickable link to the FEC filing, a sidebar? What does "low confidence" look like when rendered? A confidence indicator without guidance ("low confidence — consider verifying at FEC.gov") is worse than no indicator.

### Success metrics are system-only

The metrics table has no user-facing measures: no usage metrics (queries per day, return users), no engagement metrics (multi-turn conversations, click-through to sources), no qualitative metrics (satisfaction, would-recommend), no adoption metrics (MCP integrations, newsrooms using the tool). The PRD should define what "successful launch" looks like in user terms.

### MCP priority conflicts with MCP-first architecture

The design doc makes MCP the architectural center, but the PRD lists it as P2. If MCP is truly central, deferring it means the web UI gets built without it and MCP is bolted on later. Either make a minimal MCP interface P1 (since the architecture depends on it) or acknowledge the web UI will initially bypass MCP.

### Content policy and political neutrality

This is a university-hosted tool about elections. The PRD should address: how the system avoids appearing politically biased, whether there are questions it should refuse ("who should I vote for?"), and how to handle bad-faith actors trying to produce misleading screenshots. Even a brief section is better than silence.

### Error states and edge cases

Not addressed: what the user sees when SQL generation fails, when the system is over capacity, when they ask about state races not in federal data, when they ask about very recent events not yet in the data, or when they ask political questions outside campaign finance.

### OpenSecrets licensing is a top-level risk

"Unclear what discussions or approvals we've had" is buried in a table footnote. If OpenSecrets does not grant permission, the v2 data story weakens considerably — FEC raw data alone is much harder to make useful.

### Data freshness during election season

One week is an eternity in October. The freshness target may need to vary by season. The system should always tell users when data was last updated.

### Evaluator recruiting is on the critical path

The evaluation system is P1, but evaluators are people, not technology. Who are they, how are they recruited, are they paid, how many do we need? This is a people problem that should be addressed in the PRD.

### Accessibility

The PRD targets citizens but says nothing about WCAG compliance, screen reader support, or language. Even if the answer is "English only, WCAG 2.1 AA," it should be stated.

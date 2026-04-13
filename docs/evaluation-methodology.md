# Datatalk Evaluation Methodology

**Project:** Datatalk -- Natural Language Campaign Finance Explorer
**Owner:** Big Local News / Stanford
**Status:** Draft
**See also:** [PRD-v2.md](PRD-v2.md), [Design-v2.md](Design-v2.md) (Section 2.5, Open Question 8.6)

---

## 1. Rating Dimensions

Each question-answer pair is evaluated on five dimensions. These were chosen to cover the quality attributes most relevant to a public-facing NL-to-SQL system for campaign finance data, drawing on the PRD's trust requirements (Section 7) and the evaluation dimensions proposed in the design doc (Section 2.5).

### 1.1 Factual Accuracy

**Definition:** The factual claims in the answer are correct and consistent with the underlying data. Numbers, names, dates, and relationships match what a correct SQL query against the source data would return.

| Score | Meaning | Example |
|-------|---------|---------|
| 3 | All facts correct | "ActBlue raised $217M in Q3 2025" matches the database records exactly. |
| 2 | Minor error that does not change the main conclusion | Answer says "$217M" when the precise figure is $216.8M; rounding is reasonable but not flagged as approximate. |
| 1 | Material error that changes the meaning or conclusion | Answer says "ActBlue raised $217M" when the correct figure is $117M, or attributes the amount to the wrong committee. |

**Evaluator guidance:** If the answer says "I don't know" or "our data doesn't cover this" and that is the correct response, score 3. An honest refusal is factually accurate. A fabricated answer to an unanswerable question scores 1.

### 1.2 Completeness

**Definition:** The answer addresses all parts of the user's question and includes the key information a knowledgeable human would provide. It does not omit important results or leave the question partially answered.

| Score | Meaning | Example |
|-------|---------|---------|
| 3 | Fully addresses the question | Q: "Who are the top 5 donors to Texas Senate races in 2024?" -- Answer lists all 5 with amounts and recipient committees. |
| 2 | Addresses the core question but omits secondary information | Answer lists 5 donors with amounts but does not break down by recipient committee when that would be useful context. |
| 1 | Fails to answer the question or is critically incomplete | Answer lists only 2 donors, or answers about House races instead of Senate races. |

**Evaluator guidance:** Completeness does not mean verbosity. A concise answer that fully addresses the question scores 3. An answer that buries the response in irrelevant detail but misses the core question scores 1.

### 1.3 Appropriate Caveats and Context

**Definition:** The answer provides necessary qualifications, limitations, and contextual information that a knowledgeable analyst would include. This is the "domain intelligence" dimension -- it measures whether the system demonstrates understanding of the political landscape beyond raw data.

| Score | Meaning | Example |
|-------|---------|---------|
| 3 | Includes all necessary caveats and relevant context | Q: "How much has the gubernatorial campaign raised?" -- Answer correctly notes that our data covers federal races only and this question may be about a state race not in our database. |
| 2 | Missing a caveat that would be useful but is not misleading without it | Answer about a candidate's fundraising does not mention that the candidate withdrew from the race, but the numbers are still correct. |
| 1 | Missing a critical caveat, leading the user to a wrong conclusion | Answer about a PAC's spending does not mention that the figures only cover independent expenditures and exclude direct contributions, making the total appear much smaller than reality. |

**Evaluator guidance:** This dimension is especially important for Datatalk because users may base reporting or civic decisions on answers. Key caveats include: data coverage limitations (federal-only, time period), candidate status changes, known data quality issues, and the difference between data absence and a true zero.

### 1.4 Source Attribution

**Definition:** The answer identifies which data source(s) were used and provides enough specificity for the user to verify the answer independently.

| Score | Meaning | Example |
|-------|---------|---------|
| 3 | Cites specific source with enough detail for verification | "Source: FEC individual contributions data, 2023-2024 election cycle, filing period through 2024-06-30." |
| 2 | Names the general source but lacks specificity | "Source: FEC data." |
| 1 | No source attribution, or attributes to wrong source | Answer provides numbers with no indication of where they came from. |

**Evaluator guidance:** Perfect attribution (score 3) means a journalist could trace the answer back to specific records. For the MVP, we expect most answers to cite at least the data source and election cycle.

### 1.5 Helpfulness

**Definition:** The answer is well-organized, easy to understand, and directly useful to the person who asked the question. This is the holistic "would a journalist find this useful?" dimension.

| Score | Meaning | Example |
|-------|---------|---------|
| 3 | Clear, well-structured, directly actionable | Answer uses a clean table for comparative data, highlights the key finding, and suggests a relevant follow-up question. |
| 2 | Understandable but could be better organized or more direct | Answer provides correct information but buries the key number in a paragraph of text, or uses jargon without explanation. |
| 1 | Confusing, poorly organized, or not useful | Answer dumps raw SQL output, uses unexplained column names, or provides information the user did not ask for while missing what they did. |

**Evaluator guidance:** This is the "overall impression" dimension. If the other four dimensions are all scored 3 but the answer is still somehow unhelpful (e.g., technically correct but presented in an unusable format), this is where that gets captured.

### Why These Five Dimensions

These dimensions map directly to the PRD's trust and quality requirements:

| PRD Requirement | Evaluation Dimension |
|----------------|---------------------|
| Source attribution (Section 7) | Source Attribution |
| Honest uncertainty (Section 7) | Appropriate Caveats and Context |
| >90% accuracy on benchmark (Section 8) | Factual Accuracy |
| Appropriate caveats in >95% of out-of-scope questions (Section 8) | Appropriate Caveats and Context |
| Domain expert involvement (Section 7) | Completeness, Helpfulness |

Five dimensions is a deliberate limit. Research on human evaluation rubrics (the LLM-Rubric framework, the CLEVER clinical evaluation study) shows that evaluator fatigue and inter-rater disagreement increase significantly beyond 5-7 dimensions. Each additional dimension adds roughly 30 seconds per evaluation and reduces rating consistency.

---

## 2. Rating Scale

### Scale: 3-point ordinal (1-2-3) per dimension

Each dimension uses a 3-point scale as defined above:
- **3** = Good (meets expectations)
- **2** = Acceptable (minor issues)
- **1** = Poor (material problems)

### Justification

**Why not binary (pass/fail)?** Binary scales lose the distinction between "minor issue" and "material failure." The PRD requires nuanced metrics ("90% accuracy") that benefit from a middle category. Binary would force evaluators to make hard calls on borderline cases, increasing disagreement.

**Why not 5-point Likert?** Research on inter-rater reliability consistently shows that narrower scales produce higher agreement. A 5-point scale introduces ambiguity between adjacent levels (what distinguishes a 3 from a 4?). The CLEVER clinical evaluation framework found that even with trained physician raters, inter-rater reliability on multi-point scales was only moderate. With our evaluator pool (mixed expertise levels, part-time), a 3-point scale is more realistic. The LLM-Rubric research confirms that LLM judges exhibit central tendency bias on broad scales, which also applies to human raters.

**Why not Best-Worst Scaling?** BWS produces higher statistical reliability but requires presenting multiple answers for comparison. Datatalk evaluates one system's answer at a time (not comparing models), so pairwise comparison adds complexity without benefit.

### Deriving the Headline Accuracy Metric

The PRD's ">90% accuracy" target maps to:

**Accuracy rate = percentage of benchmark questions where Factual Accuracy = 3**

This is a strict pass/fail derived from the ordinal scale: only a score of 3 (all facts correct) counts as accurate. Scores of 2 (minor error) and 1 (material error) both count as inaccurate for the headline metric. This is deliberately conservative -- it is better to understate our accuracy than to overstate it.

For the caveats metric (">95% on out-of-scope questions"), the same logic applies: only a score of 3 on Appropriate Caveats counts as passing.

### Composite Score

For internal tracking (not public reporting), we compute a per-question composite:

**Composite = mean of all 5 dimension scores**

Range: 1.0 to 3.0. This is useful for regression detection (tracking composite score over time) but is too opaque for public trust claims. Public claims always use the dimension-specific metrics.

---

## 3. Evaluator Workflow

### What the Evaluator Sees

The evaluation interface presents one question-answer pair at a time:

```
+------------------------------------------------------------------+
|  QUESTION                                                        |
|  "Who are the top 5 individual donors to Republican Senate       |
|   candidates in the 2024 cycle?"                                 |
|                                                                  |
|  DATATALK'S ANSWER                                               |
|  [Full natural language answer as the user would see it,         |
|   including any tables, source citations, and caveats]           |
|                                                                  |
|  REFERENCE INFORMATION (collapsed by default)                    |
|  > Gold-standard answer (if available)                           |
|  > SQL query generated                                           |
|  > Raw query results                                             |
|  > Data source and freshness timestamp                           |
+------------------------------------------------------------------+
|                                                                  |
|  RATINGS                                                         |
|                                                                  |
|  Factual Accuracy:        [1] [2] [3]                            |
|  Completeness:            [1] [2] [3]                            |
|  Caveats & Context:       [1] [2] [3]                            |
|  Source Attribution:       [1] [2] [3]                            |
|  Helpfulness:             [1] [2] [3]                             |
|                                                                  |
|  COMMENTS (optional, free text)                                  |
|  [                                                    ]          |
|                                                                  |
|  CORRECTION (optional -- "the answer should say...")             |
|  [                                                    ]          |
|                                                                  |
|  FLAG FOR DISCUSSION  [ ]                                        |
|                                                                  |
|  [Skip]                    [Submit & Next]                       |
+------------------------------------------------------------------+
|  Progress: 12 of 50 completed | Session time: 28 min             |
+------------------------------------------------------------------+
```

### Step-by-Step Workflow

1. **Read the question.** Understand what the user is asking. (~10 seconds)

2. **Read the answer.** Read Datatalk's full response, including any tables and citations. (~30 seconds)

3. **Check reference information if needed.** Expand the reference panel to see the gold-standard answer (if one exists), the SQL query, or raw results. This is optional -- experienced evaluators may not need it for straightforward questions. (~0-30 seconds)

4. **Rate each dimension.** Click one of [1] [2] [3] for each of the five dimensions. The 3-point scale with clear anchors makes this fast. (~30 seconds)

5. **Add comments or corrections (optional).** Only when something noteworthy needs explanation. Most evaluations will not need comments. (~0-30 seconds)

6. **Submit and advance.** Click "Submit & Next" to record the evaluation and load the next question. The interface auto-saves ratings on click, so no work is lost if the session is interrupted.

**Target time per question: 2-3 minutes.** Simple factual questions with correct answers take under 2 minutes. Complex questions requiring reference checking take up to 3 minutes. Questions requiring corrections take 3-5 minutes. The interface shows session time and pace to help evaluators self-manage.

### Assignment and Concurrency

- Questions are assigned to evaluators in round-robin fashion to distribute coverage evenly.
- Each question in the core benchmark set is evaluated by at least 2 independent evaluators (see Section 6 for MVP specifics).
- Evaluators do not see each other's ratings until both have submitted (blind evaluation).
- Disagreements of 2+ points on any dimension are flagged for adjudication by a project team member.
- The system supports up to 10 concurrent evaluators (per PRD Section 6.2) using standard database transactions with optimistic locking.

### Session Design

- Evaluators work in sessions of 20-30 questions (approximately 45-75 minutes).
- The interface randomizes question order within a session to prevent order effects.
- A "Skip" button allows evaluators to pass on questions outside their expertise.
- Session progress is visible so evaluators can pace themselves.

---

## 4. Question Set Design

### 4.1 Question Categories

The benchmark set covers these categories, chosen to span the range of queries Datatalk must handle:

| Category | Description | Example | Target % of Set |
|----------|-------------|---------|-----------------|
| **Candidate lookup** | Fundraising totals, donor lists, spending for specific candidates | "How much has [candidate] raised this cycle?" | 20% |
| **Race comparison** | Side-by-side funding comparison for a race or set of races | "Compare fundraising for the two leading Senate candidates in Pennsylvania." | 15% |
| **Donor/PAC analysis** | Questions about specific donors, PACs, or contribution patterns | "What are the top 10 PACs by total independent expenditures in 2024?" | 15% |
| **Trend/historical** | Questions spanning multiple cycles or time periods | "How has small-dollar fundraising changed over the last 3 presidential cycles?" | 10% |
| **Domain jargon** | Questions using campaign finance terminology | "What's the burn rate for [candidate]'s campaign?" | 10% |
| **Out-of-scope** | Questions our data cannot answer (state races, non-finance topics, future predictions) | "Who will win the California governor's race?" | 15% |
| **Ambiguous/complex** | Questions requiring disambiguation or multi-step reasoning | "Who is the biggest spender in the upcoming election?" (which office? which state?) | 10% |
| **Edge cases** | Empty results, very recent data, data quality issues | "How much has [obscure candidate who filed yesterday] raised?" | 5% |

### 4.2 Benchmark Set Size

**Target: 200 questions for the full benchmark, 50 for the MVP.**

Rationale for 200:
- With 2 evaluators per question and a 3-point scale across 5 dimensions, 200 questions provides sufficient statistical power to detect meaningful quality differences. For inter-rater reliability measurement (Cohen's kappa), research recommends a minimum of 30-50 items with 3-5 raters; 200 questions with 2 raters is well above this threshold.
- At 8 question categories, 200 questions gives 10-40 questions per category (proportional to the target percentages above), enough to report category-level metrics with reasonable confidence intervals.
- The BIRD NL-to-SQL benchmark uses a 500-question mini-dev set for quick evaluation and 1,534 for full dev. Our 200-question set is proportionate to our domain scope (single domain vs. BIRD's 37 domains).
- At 2-3 minutes per evaluation, 200 questions takes one evaluator roughly 7-10 hours -- feasible as a focused evaluation sprint over 2-3 days.

Rationale for 50 (MVP):
- 50 questions is the minimum for a statistically meaningful accuracy claim. With 50 binary outcomes (accurate/not), a 90% accuracy rate has a 95% confidence interval of approximately +/-8 percentage points (82%-98%). This is wide but sufficient for an initial trust artifact.
- 50 questions can be evaluated by 2 raters in a single day, enabling rapid iteration.

### 4.3 Question Curation Process

1. **Seed questions from the team.** Project engineers and the domain expert write 30-50 initial questions based on real user queries from v1 logs and their domain knowledge.

2. **Expert contribution.** Hired domain experts (see Section 5) contribute questions in their areas of expertise, especially edge cases and jargon-heavy queries that engineers might miss.

3. **Adversarial questions.** Deliberately include questions designed to trip up the system: ambiguous phrasing, questions mixing covered and uncovered data, questions with misleading assumptions ("How much dark money did [candidate] receive?" when dark money is not directly trackable in FEC data).

4. **Gold-standard answers.** For at least 50% of benchmark questions, write a gold-standard answer that represents what a correct, complete response looks like. These are written by domain experts and verified against the database. Gold-standard answers serve as reference material for evaluators and as ground truth for automated checks.

5. **Difficulty stratification.** Tag each question with a difficulty level (easy / medium / hard) based on the SQL complexity required and the amount of domain knowledge needed. Report accuracy metrics by difficulty level.

### 4.4 Benchmark Update Cadence

- **Quarterly review.** Every 3 months, review the benchmark set for staleness. Questions that reference specific candidates or races become stale as election cycles change.
- **Post-data-update regression.** After each major data import (new election cycle data, new data source), run the full automated benchmark. This is automated and does not require human re-evaluation unless regressions are detected.
- **Annual refresh.** Once per year (after each major election cycle), retire 20-30% of questions that are no longer relevant and replace with new questions reflecting the current political landscape.
- **Append, don't replace.** Retired questions are archived, not deleted. This allows longitudinal comparison of system quality over time.

---

## 5. Evaluator Recruiting and Training

### 5.1 Evaluator Profiles

We need three tiers of evaluators, each serving a different purpose:

| Tier | Who | Role | Count Needed | Paid? |
|------|-----|------|-------------|-------|
| **Core** | Hired domain expert(s) -- campaign finance analysts, political scientists, or experienced political journalists | Write gold-standard answers, adjudicate disagreements, validate the benchmark set, provide corrections that feed back into domain knowledge | 1-2 | Yes -- part of their domain expert role (budgeted in PRD) |
| **Regular** | Journalism students, political science graduate students, or civic tech volunteers with campaign finance knowledge | Perform bulk evaluation sessions, provide ratings and annotations | 3-5 | Yes -- paid per-session stipend ($25-50/hour or per-question micro-payment of $1-2/question) |
| **Spot** | Project engineers, Stanford researchers, or interested volunteers | Occasional evaluation to supplement coverage, dogfooding | 2-3 | No -- part of their project role |

**Total evaluator pool: 6-10 people.** At any given time, we need at least 3-4 active evaluators to maintain the target of 50+ evaluated question-answer pairs per week (PRD Section 8).

### 5.2 Recruiting Strategy

**Core evaluators:** Recruited through Big Local News / Stanford connections. The PRD already plans to hire domain experts; evaluation is part of their role. Look for people with both campaign finance expertise and experience explaining findings to non-experts (former investigative reporters, policy analysts).

**Regular evaluators:** Three recruiting channels, in priority order:

1. **Stanford journalism / political science programs.** Graduate students who study campaign finance or election reporting. Recruiting through course instructors or department job boards. This is the most likely source of evaluators who have both the domain knowledge and the motivation (resume building, course credit, exposure to applied AI research).

2. **Civic tech community.** Organizations like the Investigative Reporters and Editors (IRE), the National Institute for Computer-Assisted Reporting (NICAR), or local civic tech meetups. These communities have people who care about transparent campaign finance data and may volunteer or work for modest stipends.

3. **Annotation platforms.** If we cannot recruit enough domain-knowledgeable evaluators, we can use platforms like Surge AI or Scale AI for evaluators who follow our rubric. This is a fallback -- general-purpose annotators will produce lower-quality ratings on domain-specific dimensions (Caveats & Context, Completeness) compared to people who understand campaign finance.

### 5.3 Training Process

**Training takes approximately 2 hours per evaluator.** It consists of:

1. **Background reading (30 min, async).** Evaluators read:
   - This methodology document (the rating dimensions and scale definitions)
   - A 1-page summary of Datatalk's data sources and known limitations
   - 5 example evaluations with commentary explaining why each dimension received its score

2. **Calibration session (60 min, synchronous).** All new evaluators participate in a calibration session (video call or in person):
   - Walk through 10 question-answer pairs together as a group
   - Each evaluator rates independently, then the group discusses
   - A core evaluator explains the "correct" ratings and why
   - Focus on the cases where reasonable people might disagree
   - The goal is not unanimity but shared understanding of the scale anchors

3. **Solo practice (30 min, async).** Evaluator independently rates 10 question-answer pairs from the calibration set (different from the 10 discussed in the group session). Their ratings are compared to the core evaluator's ratings. If agreement is below 70% (exact match on 3-point scale), the evaluator gets additional coaching before joining the production pool.

4. **Ongoing calibration.** Every month, include 5 "calibration questions" (pre-rated by core evaluators) in each evaluator's session without flagging them. This provides continuous monitoring of inter-rater reliability without disrupting the workflow.

### 5.4 Timeline to Onboard

| Milestone | Time from Start |
|-----------|----------------|
| Post recruiting notices, identify candidates | Week 1 |
| Screen and select 4-6 regular evaluators | Week 2-3 |
| Develop training materials and example evaluations | Week 2-3 (parallel) |
| Conduct calibration session | Week 4 |
| Solo practice and coaching | Week 4-5 |
| First production evaluation session | Week 5-6 |
| MVP evaluation complete (50 questions x 2 raters) | Week 7-8 |

**Realistic total: 6-8 weeks from decision to first publishable results.** The critical path is evaluator recruiting and training, not technology. The evaluation interface can be a minimal Django admin-style form for the MVP (see Section 6).

### 5.5 Retention

Evaluator retention is the biggest operational risk. Mitigation strategies:

- **Keep sessions short.** 45-75 minutes maximum. Do not ask evaluators to grind through hundreds of questions in a marathon.
- **Pay fairly and promptly.** Benchmarked at $25-50/hour for regular evaluators, paid within 2 weeks of the session.
- **Show impact.** Share aggregate results with evaluators. Show them how their feedback improved the system. People who see their work making a difference are more likely to continue.
- **Maintain a bench.** Always have 2-3 more evaluators in the trained pool than are needed for any given sprint. Expect 30-40% attrition per quarter.

---

## 6. Minimum Viable Evaluation (MVE)

The MVE is the smallest evaluation that produces a credible, publishable trust artifact for the Datatalk website.

### 6.1 MVE Specification

| Parameter | MVE Target |
|-----------|-----------|
| Benchmark questions | 50 |
| Evaluators | 3 (1 core + 2 regular) |
| Ratings per question | 2 (each question rated by 2 evaluators) |
| Dimensions rated | All 5 |
| Gold-standard answers | At least 25 (50% of questions) |
| Inter-rater reliability | Cohen's kappa >= 0.60 (substantial agreement) on Factual Accuracy |
| Automated checks | SQL execution success, source citation presence (see Section 7) |

### 6.2 Publishable Trust Artifact

The MVE produces a statement suitable for the Datatalk "About" page:

> **Evaluation Results**
>
> Datatalk's answers were evaluated on a benchmark of 50 campaign finance questions spanning candidate lookups, race comparisons, donor analysis, domain jargon, and out-of-scope detection. Each question was independently reviewed by two trained evaluators with campaign finance expertise.
>
> - **Factual accuracy:** X% of answers were rated fully accurate by both evaluators.
> - **Appropriate caveats:** Y% of out-of-scope questions were correctly identified with appropriate caveats.
> - **Source attribution:** Z% of answers included specific, verifiable source citations.
>
> Evaluation methodology is [published here](link). The benchmark set is refreshed quarterly. Last evaluation: [date].

### 6.3 What the MVE Does Not Include

- Category-level breakdowns (not enough questions per category with only 50 total)
- Trend analysis over time (first evaluation, no historical comparison)
- Statistical confidence intervals (we report raw percentages; the sample size context is implicit in "50 questions")
- Automated regression detection (requires multiple evaluation runs)

### 6.4 MVE Technology Requirements

The MVE does not require a polished evaluation web application. It can run on:

- **Question storage:** A CSV or JSON file in the repository (`tests/benchmarks/benchmark_v1.json`)
- **Automated run:** A script (`scripts/run_benchmark.py`) that sends each benchmark question through the system and records the answer
- **Evaluation interface:** A simple Django admin form, Google Form, or even a shared spreadsheet with structured columns. The key requirement is that evaluators rate independently (no visibility into each other's ratings).
- **Analysis:** A Python notebook or script that computes accuracy percentages, inter-rater reliability, and generates the publishable summary.

The full evaluation web application (Design-v2.md Section 2.5) is built after the MVE proves the methodology works.

### 6.5 MVE Effort Estimate

| Task | Effort |
|------|--------|
| Write 50 benchmark questions with categories and difficulty tags | 2-3 days (domain expert + engineer) |
| Write 25 gold-standard answers | 3-4 days (domain expert) |
| Build `run_benchmark.py` script | 1-2 days (engineer) |
| Set up minimal evaluation interface | 1 day (engineer) |
| Recruit and train 2 regular evaluators | 3-4 weeks (see Section 5.4) |
| Run automated benchmark | 1 day (automated) |
| Conduct human evaluation (50 questions x 2 raters) | 2-3 days per evaluator |
| Analyze results and write publishable summary | 1 day |
| **Total calendar time** | **6-8 weeks** |
| **Total engineering effort** | **~1 week** |

The bottleneck is evaluator recruiting and training, not engineering.

---

## 7. Automated vs. Human Evaluation

Some quality dimensions can be partially or fully automated, reducing the human evaluation burden and enabling continuous regression detection. The principle: **automate what is objective, use humans for what requires judgment.**

### 7.1 Fully Automated Checks

These checks run on every benchmark execution with no human involvement. They produce binary pass/fail signals.

| Check | What It Measures | How It Works |
|-------|-----------------|-------------|
| **SQL execution success** | Did the generated SQL run without error? | Execute the SQL against the database; check for exceptions. A query that fails to execute cannot produce a correct answer. |
| **Non-empty result** | Did the query return data? | Check that the result set has >= 1 row (for questions expected to have results). Empty results for non-empty-expected questions indicate a query logic error. |
| **Source citation present** | Does the answer mention its data source? | Regex/string match for known source identifiers ("FEC", "OpenSecrets", "filing", "election cycle"). Binary: present or absent. |
| **Response latency** | Did the answer arrive within the target time? | Measure wall-clock time from question submission to answer completion. Flag responses exceeding 15 seconds (PRD Section 8). |
| **Answer refusal for out-of-scope** | Does the system correctly decline out-of-scope questions? | For questions tagged as out-of-scope in the benchmark, check whether the answer contains refusal/caveat phrases ("our data doesn't cover", "not in our database", "state-level races"). |
| **SQL safety** | Does the generated SQL stay within expected bounds? | Check for absence of DDL statements (CREATE, DROP, ALTER), absence of writes (INSERT, UPDATE, DELETE), and presence of LIMIT clauses on broad queries. |

### 7.2 Semi-Automated Checks (LLM-Assisted)

These checks use an LLM judge to approximate human ratings. They are useful for continuous monitoring between human evaluation sprints but do not replace human evaluation for official metrics.

| Check | What It Measures | How It Works |
|-------|-----------------|-------------|
| **Answer-vs-gold comparison** | Does the answer match the gold-standard answer semantically? | For questions with gold-standard answers, prompt an LLM: "Given this question and reference answer, does the system's answer convey the same facts? Rate: match / partial match / no match." This approximates the Factual Accuracy dimension. |
| **Caveat detection** | Does the answer include appropriate qualifications? | Prompt an LLM with the question, the answer, and a list of known data limitations: "Does this answer mention relevant limitations?" This approximates the Caveats & Context dimension. |
| **Readability and structure** | Is the answer well-organized? | Prompt an LLM: "Rate this answer's clarity and organization on a scale of 1-3." This approximates Helpfulness but with lower reliability than human judgment. |

**Important constraints on LLM-assisted evaluation:**
- LLM judge ratings are tracked separately from human ratings and are never mixed into official metrics.
- LLM judges are calibrated against human ratings from the most recent evaluation sprint. If LLM-human agreement falls below kappa 0.50, LLM-assisted checks are suspended until recalibrated.
- LLM judges use a different model than the one generating answers (to avoid self-evaluation bias). If Datatalk uses Claude for answer generation, use GPT-4 for evaluation, or vice versa.

### 7.3 Human-Only Evaluation

These dimensions require human judgment and cannot be meaningfully automated:

| Dimension | Why It Requires Humans |
|-----------|----------------------|
| **Factual Accuracy (official metric)** | Automated execution accuracy (did the SQL return correct results?) is necessary but not sufficient. The natural language answer might misrepresent correct SQL results, or the SQL might be technically correct but answer the wrong question. Only a human can assess whether the full chain from question to answer is faithful. |
| **Completeness** | Judging whether "all key information a knowledgeable human would provide" is included requires domain knowledge that cannot be reduced to a checklist. |
| **Appropriate Caveats (official metric)** | Knowing which caveats are necessary for a specific question requires understanding the political context. A missing caveat about a candidate's withdrawal from a race cannot be detected by pattern matching -- you need to know the candidate withdrew. |
| **Helpfulness** | Subjective judgment about whether the answer would be useful to a journalist on deadline. No automated proxy captures this reliably. |

### 7.4 Evaluation Pipeline

The automated and human evaluation steps combine into a pipeline:

```
Benchmark question set
        |
        v
[Automated Run] -- sends each question through Datatalk, records answers
        |
        v
[Automated Checks] -- SQL success, citation present, latency, safety
        |
        v
[LLM-Assisted Checks] -- answer-vs-gold, caveat detection (for monitoring)
        |
        v
[Human Evaluation Queue] -- questions + answers presented to evaluators
        |                    with automated check results as context
        v
[Analysis & Reporting] -- compute metrics, check inter-rater reliability,
                          generate publishable summary, detect regressions
```

**Automated checks run on every benchmark execution** (triggered by data updates, model changes, or code deploys).

**Human evaluation runs on a sprint cadence** -- at minimum quarterly, and additionally when automated checks detect regressions (e.g., SQL success rate drops below threshold, LLM-vs-gold match rate declines significantly).

---

## 8. References and Prior Art

This methodology draws on several sources:

### NL-to-SQL Benchmarks

- **[BIRD Benchmark](https://bird-bench.github.io/):** The most relevant NL-to-SQL benchmark for our purposes. BIRD's use of execution accuracy (EX) as the primary metric, its incorporation of domain-specific external knowledge, and its emphasis on real-world "dirty" data are directly applicable to Datatalk. Our automated SQL execution check (Section 7.1) follows BIRD's approach. BIRD's 12,751 question-SQL pairs across 37 domains provides scale context for our 200-question single-domain benchmark.

- **[Spider](https://yale-lily.github.io/spider):** The foundational NL-to-SQL benchmark. Spider's exact-match accuracy metric (comparing predicted SQL structure to gold SQL) is less relevant to Datatalk because we care about answer correctness, not SQL equivalence -- there are many correct SQL formulations for the same question. However, Spider's question difficulty categorization (easy / medium / hard / extra hard based on SQL complexity) informed our difficulty stratification approach.

- **Analysis of Text-to-SQL Benchmarks (EDBT 2025):** Highlights limitations of existing benchmarks: over-representation of simple SELECT-FROM-WHERE queries, limited structural variety, and non-industrial database schemas. Our benchmark addresses this by including questions that require multi-step reasoning, temporal analysis, and domain knowledge beyond what is in the schema.

### Human Evaluation Frameworks

- **[CLEVER Framework](https://pmc.ncbi.nlm.nih.gov/articles/PMC12677871/):** Clinical LLM evaluation using 3 expert raters per item, pairwise comparison, and rubric-based scoring across factuality, relevance, and conciseness. Key lesson: inter-rater reliability was only moderate even with trained physician raters, reinforcing our decision to use a narrow 3-point scale and require calibration training. Their 100-item evaluation set and use of external contractors informed our MVE sizing.

- **[LLM-Rubric (ACL 2024)](https://aclanthology.org/2024.acl-long.745/):** Multidimensional calibrated evaluation using 9 quality dimensions on a 1-4 scale to predict overall user satisfaction. Key insight: narrow scales with clear anchors outperform broader scales for consistency. Their finding that each dimension should be evaluated independently (to prevent halo effects) informed our separate per-dimension rating design.

- **[PEARL Framework](https://www.mdpi.com/2078-2489/16/11/926):** Multi-metric evaluation integrating Technical, Argumentative, and Explanation rubrics. Their dimensions (accuracy, clarity, completeness, terminology) align closely with ours, validating our dimension selection.

### The Policing Project

- **[AI Governance Framework (2025)](https://www.policingproject.org/governing-ai-articles/2025/12/17/vs01kunxeynef91ie0plwd61dwcgfh):** The Policing Project's framework for AI governance in law enforcement emphasizes mandatory assessment before deployment, meaningful human oversight, transparency through disclosure, and dual internal/external auditing. While their domain (policing) is different from ours (campaign finance), the principles transfer: evaluation must be rigorous and public, human oversight is essential, and transparency about methodology builds trust. Their emphasis on evaluating AI systems against specific, documented criteria before deployment (not after) reinforces our approach of making evaluation a P1 launch requirement.

### Stanford Foundation Model Transparency Index

- **[FMTI](https://crfm.stanford.edu/fmti/):** Stanford's 100-indicator transparency index for foundation models provides a model for structured, public evaluation. Their approach of scoring across a rubric with published methodology and making results publicly available is the template for our trust artifacts. The key parallel: transparency about how evaluation is conducted is as important as the evaluation results themselves.

### Inter-Rater Reliability Standards

- Studies consistently recommend 30-50 items minimum with 3-5 raters for reliable kappa estimates. Our MVE (50 questions, 2-3 raters) is at the lower bound; the full benchmark (200 questions, 2+ raters) provides robust measurement. Target kappa of 0.60+ (substantial agreement) is appropriate for subjective quality ratings with a narrow scale.

---

## Appendix A: Evaluation Database Schema

These tables support the evaluation system (referenced in Design-v2.md Section 2.5).

```sql
-- Benchmark questions
CREATE TABLE eval_questions (
    id              SERIAL PRIMARY KEY,
    question_text   TEXT NOT NULL,
    category        VARCHAR(50) NOT NULL,  -- e.g., 'candidate_lookup', 'out_of_scope'
    difficulty      VARCHAR(20) NOT NULL,  -- 'easy', 'medium', 'hard'
    gold_answer     TEXT,                  -- gold-standard answer (nullable)
    gold_sql        TEXT,                  -- gold-standard SQL (nullable)
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMP DEFAULT NOW(),
    retired_at      TIMESTAMP
);

-- Automated benchmark runs
CREATE TABLE eval_runs (
    id              SERIAL PRIMARY KEY,
    run_date        TIMESTAMP DEFAULT NOW(),
    trigger         VARCHAR(50) NOT NULL,  -- 'data_update', 'model_change', 'scheduled', 'manual'
    model_version   VARCHAR(100),
    data_version    VARCHAR(100),          -- e.g., 'fec_2024q3'
    notes           TEXT
);

-- System-generated answers for each run
CREATE TABLE eval_responses (
    id              SERIAL PRIMARY KEY,
    run_id          INTEGER REFERENCES eval_runs(id),
    question_id     INTEGER REFERENCES eval_questions(id),
    answer_text     TEXT NOT NULL,
    sql_generated   TEXT,
    sql_success     BOOLEAN,
    result_row_count INTEGER,
    response_time_ms INTEGER,
    citation_present BOOLEAN,
    created_at      TIMESTAMP DEFAULT NOW(),
    UNIQUE(run_id, question_id)
);

-- Human ratings
CREATE TABLE eval_ratings (
    id              SERIAL PRIMARY KEY,
    response_id     INTEGER REFERENCES eval_responses(id),
    evaluator_id    INTEGER NOT NULL,      -- references auth user
    accuracy        SMALLINT CHECK (accuracy BETWEEN 1 AND 3),
    completeness    SMALLINT CHECK (completeness BETWEEN 1 AND 3),
    caveats         SMALLINT CHECK (caveats BETWEEN 1 AND 3),
    source_attr     SMALLINT CHECK (source_attr BETWEEN 1 AND 3),
    helpfulness     SMALLINT CHECK (helpfulness BETWEEN 1 AND 3),
    comment         TEXT,
    correction      TEXT,
    flagged         BOOLEAN DEFAULT FALSE,
    is_calibration  BOOLEAN DEFAULT FALSE, -- hidden calibration questions
    created_at      TIMESTAMP DEFAULT NOW(),
    UNIQUE(response_id, evaluator_id)
);

-- LLM-assisted evaluation results (tracked separately)
CREATE TABLE eval_llm_ratings (
    id              SERIAL PRIMARY KEY,
    response_id     INTEGER REFERENCES eval_responses(id),
    judge_model     VARCHAR(100) NOT NULL,
    gold_match      VARCHAR(20),           -- 'match', 'partial', 'no_match'
    caveat_detected BOOLEAN,
    readability     SMALLINT CHECK (readability BETWEEN 1 AND 3),
    raw_output      JSONB,                 -- full LLM judge response
    created_at      TIMESTAMP DEFAULT NOW()
);
```

## Appendix B: Example Calibration Questions

These examples are used during evaluator training (Section 5.3) to establish shared understanding of the rating scale.

### Example 1: High-scoring answer

**Question:** "How much did ActBlue raise in the 2024 election cycle?"

**Answer:** "According to FEC data through Q3 2024, ActBlue (Committee ID C00401224) reported total receipts of $1.47 billion for the 2023-2024 election cycle. This makes it the largest fundraising committee by total receipts in this cycle. Note: This figure includes all receipts reported to the FEC through the most recent filing period in our database (September 30, 2024). More recent filings may not yet be reflected."

**Ratings:** Accuracy: 3, Completeness: 3, Caveats: 3, Source Attribution: 3, Helpfulness: 3

**Why:** Numbers match the database. Provides the committee ID for verification. Notes the data freshness limitation. Clearly cites FEC data with the specific time period. Well-structured and directly answers the question.

### Example 2: Mixed-quality answer

**Question:** "Who are the top donors to Republican candidates in Texas?"

**Answer:** "The top individual donors to Republican candidates in Texas for the current cycle are: 1. John Smith - $250,000, 2. Jane Doe - $180,000, 3. Bob Johnson - $150,000. These donations were reported to the FEC."

**Ratings:** Accuracy: 2, Completeness: 2, Caveats: 1, Source Attribution: 2, Helpfulness: 2

**Why:** Accuracy is 2 because the donor names and amounts are correct but the question is ambiguous (which offices? which cycle?) and the answer does not acknowledge the ambiguity. Completeness is 2 because only 3 donors are listed when the user likely wants more. Caveats is 1 because the answer does not clarify which offices are included (federal only), which cycle, or that these are only individual contributions (not PAC money). Source Attribution is 2 because "reported to the FEC" is vague. Helpfulness is 2 because the answer is usable but missing important context.

### Example 3: Low-scoring answer

**Question:** "How much dark money was spent on the 2024 presidential race?"

**Answer:** "Dark money spending on the 2024 presidential race totaled approximately $890 million based on FEC records."

**Ratings:** Accuracy: 1, Completeness: 1, Caveats: 1, Source Attribution: 1, Helpfulness: 1

**Why:** Accuracy is 1 because dark money, by definition, is not reported to the FEC -- the number is fabricated or based on a fundamental misunderstanding. Completeness is 1 because the answer fails to address the inherent contradiction in the question. Caveats is 1 because the answer should explain that dark money is not tracked in FEC data and suggest alternative sources (OpenSecrets tracks some dark money estimates). Source Attribution is 1 because attributing dark money figures to "FEC records" is incorrect. Helpfulness is 1 because the answer is actively misleading.

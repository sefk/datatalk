# Evaluation Plan: Evaluator Sourcing

**See also:** [evaluation-methodology.md] (rating dimensions, workflow, question design)

[evaluation-methodology.md]: evaluation-methodology.md

---

## Context

Datatalk's evaluation requires human raters to score AI-generated answers to campaign finance questions across five dimensions (factual accuracy, completeness, caveats, source attribution, helpfulness). Not all dimensions require the same level of expertise:

- **Low domain knowledge:** Source attribution (is a source cited?), helpfulness (is the answer clear?), factual accuracy (when gold-standard answers are provided for comparison)
- **High domain knowledge:** Appropriate caveats (knowing what context is missing), completeness (knowing what a knowledgeable human would include)

This distinction shapes how we source evaluators.

## Primary Channels

### 1. Hire specific people

Post short evaluation gigs targeting people with relevant backgrounds. This is our highest-quality option for the domain-knowledge dimensions.

**Where to recruit:**

- **Political science grad students.** Stanford and other universities. They have the domain knowledge, and evaluation work is a useful resume line and exposure to applied AI research. Recruit through department job boards, course instructors, or direct outreach to research groups studying elections and money in politics.

- **Journalism students covering politics.** Journalism schools, especially programs with investigative or data journalism tracks. Students at Stanford, Columbia, Northwestern, Missouri, etc.

- **IRE / NICAR members.** Investigative Reporters and Editors and the National Institute for Computer-Assisted Reporting are professional communities of journalists who already work with campaign finance data. Post on their job boards or mailing lists. These are the most domain-knowledgeable evaluators we'll find outside of hiring a full-time analyst.

- **Civic tech volunteers.** Organizations like the Sunlight Foundation, MapLight alumni, or local civic tech meetups. People who care about transparent campaign finance data and may work for modest stipends or volunteer.

**Pay:** $25-50/hour, or a flat per-session rate (~$150-250 for a 5-6 hour evaluation session covering 50 questions). Pay promptly -- within 2 weeks of the session.

**Expected yield:** 3-5 qualified evaluators from this channel. The main bottleneck is finding people with both availability and domain knowledge.

### 2. Prolific

[Prolific] is a research-grade crowdsourcing platform with better quality controls than Mechanical Turk. The key advantage: **pre-screening filters**.

[Prolific]: https://www.prolific.com/

**How to use it:**

- Filter for US-based participants with education in political science, journalism, law, or public policy
- Add a custom screener with 5 campaign finance knowledge questions (e.g., "What federal agency collects campaign contribution data?", "What is a Super PAC?")
- Require a minimum approval rate of 95% on the platform
- Pay at Prolific's minimum rate or above (~$12-15/hour; $15-20/hour recommended for quality)

**Which dimensions to assign:**

Prolific evaluators are well-suited for source attribution, helpfulness, and factual accuracy (when gold-standard answers are provided). For caveats and completeness, only assign to evaluators who score highly on the custom screener.

**Study design:**

- Run as a Prolific study with the evaluation interface (Google Form or custom tool)
- Include 2-3 gold/calibration questions per batch to monitor quality
- Reject and re-run submissions from participants who fail calibration checks
- Target 2 ratings per question per dimension

**Pay:** ~$15-20/hour effective rate. For 50 questions at ~3 minutes each, that's ~2.5 hours = ~$40-50 per participant. With 2 raters per question, total cost is ~$80-100 for non-expert dimensions.

**Expected yield:** Large pool available on short notice (days, not weeks). Quality depends heavily on screener design.

## Hybrid approach

Combine both channels for best coverage:

| Dimension | Primary raters | Source |
|---|---|---|
| Factual Accuracy | Trained non-experts (with gold answers) + expert spot-check | Prolific + Derek |
| Completeness | Domain experts | Hired students / IRE members |
| Caveats & Context | Domain experts | Hired students / IRE members + Derek |
| Source Attribution | Trained non-experts | Prolific |
| Helpfulness | Trained non-experts | Prolific |

This keeps expert costs low (~$300-500 for hired evaluators on the hard dimensions) while getting broad coverage on the easier dimensions via Prolific (~$100-200).

## Qualification process

All evaluators, regardless of source, must pass qualification before rating production questions.

### Training (~30 minutes, async)

1. A 1-page summary of Datatalk's data sources and known limitations
2. The rating rubric with scale definitions (from [evaluation-methodology.md])
3. Five worked examples with commentary explaining why each dimension received its score (see Appendix B of the methodology doc)

### Qualification test (10 questions, must score 8/10)

Test questions verify both domain knowledge and rubric understanding:

- "The system answers a question about a gubernatorial race using FEC data without noting that FEC only covers federal races. What is the appropriate caveats score?" (Answer: 1)
- "The answer says a candidate raised $5M. The gold-standard says $4.8M. The rounding is reasonable but not flagged as approximate. What is the factual accuracy score?" (Answer: 2)
- "What does FEC stand for, and what type of elections does it cover?"
- "A user asks about 'dark money.' Can this be answered from FEC data? Why or why not?"

### Calibration check

Rate 5 pre-scored question-answer pairs. Must agree with expert ratings on at least 4 of 5 for each assigned dimension. Disagreements of 2+ points on any dimension disqualify.

## Alternatives considered

### Mechanical Turk

Amazon Mechanical Turk has the largest worker pool and lowest per-task cost (~$0.50-2.00 per HIT). However:

- **No pre-screening for domain knowledge.** MTurk's qualification system is basic compared to Prolific's. You can test workers, but the pool of workers who know campaign finance is tiny.
- **High spam/low-effort rate.** Would need gold questions embedded in every batch to detect bad raters. Quality control overhead erodes the cost advantage.
- **No minimum pay enforcement.** The race-to-the-bottom pricing attracts workers who optimize for speed over quality.

MTurk could work for the simplest dimensions (source attribution, helpfulness) but Prolific is better on every axis except raw cost.

### Train and qualify general public volunteers

Recruit people with no campaign finance background and teach them enough to evaluate. The qualification process (training module + test + calibration) would filter for people who learn quickly.

**Pros:**
- Largest possible pool
- No recruiting cost
- Scales well if the training materials are good

**Cons:**
- People who pass a 30-minute training module still lack the *intuition* that comes from working with campaign finance data. They can check answers against a rubric, but they can't recognize when an answer is technically correct but misleading in context.
- The caveats dimension is especially hard to crowdsource -- you need to know what's *not* in the answer, which requires knowing the domain well enough to notice gaps.
- Training material development is a fixed cost (~1-2 days of expert time to write and validate).

This approach is viable as a fallback if the primary channels don't yield enough evaluators, or for future evaluation sprints at larger scale. Not recommended for the initial 50-question MVP where getting the methodology right matters more than volume.

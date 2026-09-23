# Citation precision — manual audit sample (prepared for human judgment)

**This document reports what was found. It does not mark any citation "correct" — every verdict
below is a suggestion for you to confirm or overrule, not a self-approval.** Every example here
was captured live, against the current committed code (`FINSIGHT_LLM_PROVIDER=ollama`,
`llama3.2:3b`, `temperature=0, seed=0`), by monkey-patching `attribute_claims` to record the
model's raw answer text *before* attribution alongside the final text *after* it — so "was this
citation added by the automatic mechanism, or did the model write it itself" is a fact, not an
inference. Every source passage is quoted **in full**, not the 280-character display quote a
reader sees, because `ERROR_ANALYSIS.md` §3g's own audit found that trusting the trimmed quote
produces a false "unsupported" verdict on real evidence one chunk deep. Regenerate this evidence
with the read-only pattern in `scripts/verify_gold_v2_draft.py`'s docstring (set
`FINSIGHT_OLLAMA__BASE_URL=http://localhost:11434` if running natively rather than in Docker).

## Automatically attached citations (added by `attribute_claims`, model wrote no bracket)

### 1. JPMorgan interest-rate risk — 3 sentences, all auto-attached

**Question:** "How does JPMorgan describe its exposure to interest rate risk?"

**Model's raw answer (zero brackets):** *"JPMorgan describes its exposure to interest rate risk
as arising from various factors, including differences in timing among the maturity or repricing
of assets, liabilities, and off-balance sheet instruments, differences in the amounts of assets,
liabilities, and off-balance sheet instruments that are maturing or repricing at the same time,
and differences in the amounts by which short-term and long-term market interest rates change. The
firm manages interest rate exposure related to its assets and liabilities on a consolidated,
firmwide basis, and business units transfer their interest rate risk to Treasury and CIO through
funds transfer pricing. The firm also uses earnings-at-risk to evaluate its structural interest
rate risk, which estimates the firm's interest rate exposure for a given interest rate scenario."*

| Claim sentence | Attached to | Full source passage (verbatim) | Suggested verdict |
|---|---|---|---|
| "...arising from various factors, including differences in timing among the maturity or repricing of assets, liabilities, and off-balance sheet instruments..." | S1 | "Key Risk Drivers and Risk Management Process\n\nStructural interest rate risk can arise due to a variety of factors, including:\n\n•Differences in timing among the maturity or repricing of assets, liabilities and off-balance sheet instruments\n\n•Differences in the amounts of assets, liabilities and off-balance sheet instruments that are maturing or repricing at the same time\n\n•Differences in the amounts by which short-term and long-term market interest rates change..." | **Supports** — near-verbatim, same list, same order |
| "The firm manages interest rate exposure related to its assets and liabilities on a consolidated, firmwide basis, and business units transfer their interest rate risk to Treasury and CIO through funds transfer pricing." | S1 | "...The Firm manages interest rate exposure related to its assets and liabilities on a consolidated, Firmwide basis. Business units transfer their interest rate risk to Treasury and CIO through funds transfer pricing, which takes into account the elements of interest rate exposure that can be risk-managed in financial markets..." | **Supports** — verbatim except capitalization |
| "The firm also uses earnings-at-risk to evaluate its structural interest rate risk, which estimates the firm's interest rate exposure for a given interest rate scenario." | S6 | "...Earnings-at-Risk\n\nOne way that the Firm evaluates its structural interest rate risk is through earnings-at-risk. Earnings-at-risk estimates the Firm's interest rate exposure for a given interest rate scenario..." | **Supports** — same claim, reordered clause |

### 2. Procter & Gamble cost pressures — 1 sentence auto-attached (the borderline case)

**Question:** "What are Procter & Gamble's biggest cost pressures?"

**Model's raw answer (bullet list has zero brackets; only the trailing sentence does after
attribution):** *"Procter & Gamble's biggest cost pressures include:\n\n* Disruptions in global
supply chain\n* Loss or disruption of key manufacturing and supply arrangements\n* Inability to
procure sufficient raw or input materials\n* Significant changes in trade policy\n* Natural
disasters, increasing severity or frequency of extreme weather events due to climate change\n*
Cybersecurity incidents\n* Labor disputes or controversies\n\nThese cost pressures can have an
adverse impact on the company's business, financial condition, results of operations, or cash
flows."*

| Claim sentence | Attached to | Full source passage (verbatim) | Suggested verdict |
|---|---|---|---|
| "These cost pressures can have an adverse impact on the company's business, financial condition, results of operations, or cash flows." | S2 | "BUSINESS OPERATIONS RISKS\n\n...The loss or disruption of such manufacturing and supply arrangements, including for issues such as labor disputes or controversies, loss or impairment of key manufacturing sites, discontinuity or disruptions in our internal information and data systems or those of our suppliers, cybersecurity incidents, inability to procure sufficient raw or input materials..., significant changes in trade policy, natural disasters,... have at times interrupted and could, in the future, interrupt product supply and, if not effectively managed and remedied, could have an adverse impact on our business, financial condition, results of operations or cash flows." | **Limited support.** S2 supports the narrow adverse-impact sentence and lists operational disruptions. It does not rank these as the company's **biggest cost pressures**. The uncited bullets need claim-level support. The draft question has been reworded to ask about operational and supply-chain risks; this historical run remains tied to the old wording. |

### 3. J&J talc litigation — 4 sentences, all auto-attached (a different live run than §3g's original audit)

**Question:** "How is Johnson & Johnson dealing with talc litigation?"

**Model's raw answer (zero brackets):** *"Johnson & Johnson is dealing with talc litigation by
implementing a corporate restructuring in 2021, which led to the creation of three new entities:
LTL Management LLC, Royalty A&M LLC, and Johnson & Johnson Consumer Inc. The company's subsidiary
Red River Talc, LLC filed a voluntary petition for Chapter 11 bankruptcy in September 2024, seeking
relief for the company's talc claims. The total present value of the reserve to resolve the talc
claims is approximately $11.6 billion, with approximately ten percent recorded as a current
liability. The company believes it has strong legal grounds to contest the other talc verdicts it
has appealed, but has settled cases in certain circumstances. The company's more significant legal
proceedings are described in Note 19 Legal proceedings under Notes to the Consolidated Financial
Statements included in Item 8 of this Report."*

| Claim sentence | Attached to | Full source passage (verbatim, abbreviated where noted) | Suggested verdict |
|---|---|---|---|
| "...Red River Talc, LLC filed a voluntary petition for Chapter 11 bankruptcy in September 2024, seeking relief for the company's talc claims." | S3 | "In September 2024, the Company's subsidiary Red River Talc, LLC filed a voluntary petition, seeking relief under Chapter 11 of the Bankruptcy Code, in furtherance of the Company's consensual 'prepackaged' Proposed Plan." | **Supports** — near-verbatim |
| "The total present value of the reserve to resolve the talc claims is approximately $11.6 billion, with approximately ten percent recorded as a current liability." | S3 (same passage, later sentence) | "As of December 29, 2024, the total present value of the reserve to resolve the talc claims is approximately $11.6 billion, of which approximately ten percent is recorded as a current liability." | **Supports** — exact figures match |
| "The company believes it has strong legal grounds to contest the other talc verdicts it has appealed, but has settled cases in certain circumstances." | S1 | "...The Company continues to believe that it has strong legal grounds to contest the other talc verdicts that it has appealed. Notwithstanding the Company's confidence in the safety of its talc products, in certain circumstances the Company has settled cases." | **Supports** — near-verbatim (this is the same sentence audited in `ERROR_ANALYSIS.md` §3g; here confirmed auto-attached rather than model-written, with the full source visible directly rather than reconstructed after a methodology correction) |
| "The company's more significant legal proceedings are described in Note 19 Legal proceedings under Notes to the Consolidated Financial Statements included in Item 8 of this Report." | S7 | "...The Company's more significant legal proceedings are described in Note 19 Legal proceedings under Notes to the Consolidated Financial Statements included in Item 8 of this Report..." | **Supports** — exact verbatim sentence |

## Model-written citations (the bracket was already in the model's own raw output)

### 4. Tesla government investigations/lawsuits — one blanket citation group covering 4 bullets

**Question:** "Does Tesla mention any government investigations or lawsuits?"

**Model wrote the bracket group itself** — `[S1, S2, S3, S4, S5, S6]` appears verbatim in the raw,
pre-attribution text (confirmed: this exact 6-ID group, spacing aside, is byte-identical across
every run of this question going back through this project's history, including runs from before
`attribute_claims` existed at all — a second, independent line of evidence for "model-written," not
just this single instrumented run).

**Model's answer**, four bulleted matters, followed by the citation group:
1. A shareholder derivative suit re: board oversight of the 2018 SEC settlement, stayed pending consolidation in Delaware Chancery.
2. *Diaz v. Tesla* — $136.9M verdict, race discrimination, Fremont Factory 2015–2016, appeal pending.
3. A California DFEH Notice of Cause Finding paralleling the Diaz allegations.
4. Two derivative actions in the U.S. District Court, W.D. Texas, re: fiduciary duty/unjust enrichment/securities-law claims tied to alleged race/gender discrimination and sexual harassment.

| Source | What it actually contains | Supports which bullet? |
|---|---|---|
| S1 | "Certain Investigations and Other Matters" — NHTSA/SEC/DOJ regulatory requests, a 2023 data-breach class action | **None of the 4 bullets specifically** — topically adjacent (litigation/investigations in general), not about any bulleted matter |
| S2 | Same "Certain Investigations" boilerplate, different data-breach detail, plus an unrelated letters-of-credit note | **None of the 4 bullets specifically** |
| S3 | Same "Certain Investigations" boilerplate again (near-duplicate of S1) | **None of the 4 bullets specifically** |
| S4 | "On October 21, 2022, a lawsuit was filed in the Delaware Court of Chancery by a purported shareholder of Tesla alleging... breached fiduciary duties... 2018 settlement with the SEC... stayed pending resolution of a motion to consolidate..." | **Bullet 1** — exact match |
| S5 | *Diaz v. Tesla* verdict details ($136.9M, Fremont Factory 2015–2016, "will pursue next steps, including an appeal") **and** the DFEH Notice of Cause Finding | **Bullets 2 and 3** — both, exact match |
| S6 | "...two Tesla stockholders filed separate derivative actions in the U.S. District Court for the Western District of Texas... breach of fiduciary duty, unjust enrichment, and violation of the federal securities laws in connection with alleged race and gender discrimination and sexual harassment." | **Bullet 4** — exact match |

**Claim-level verdict:** S4 supports bullet 1, S5 supports bullets 2–3, and S6 supports bullet 4.
S1/S2/S3 support none of the four claims, so those three citations fail claim-level precision even
though their URLs resolve and they discuss litigation generally. The historical answer should
not be presented as six supporting citations. A future answer should put S4/S5/S6 beside the
respective bullets; this review does not tune the model against this already-used question.

## Summary for your review

* **8 auto-attached citation instances audited** (JPMorgan ×3, P&G ×1, J&J ×4), spanning 2
  different live runs on 3 different companies: **7 of 8 are strong claim-specific matches**;
  P&G's one is supported only as a narrow adverse-impact statement, not as an answer to the
  historical "biggest cost pressures" wording.
* **6 model-written citation instances audited** (Tesla, one blanket group): **3 of 6 (S4/S5/S6)
  support specific bullets; 3 of 6 (S1/S2/S3) do not support any bullet**.
* **This is a small, hand-audited sample** (2 questions instrumented this session for
  auto-attachment provenance, 1 for model-written), not a statistically powered study — it is
  consistent with, and adds fresh evidence to, `ERROR_ANALYSIS.md` §3g's earlier finding that the
  tightened mechanism's remaining auto-attachments tend to be genuine, with the P&G case a useful
  concrete instance of the "generic boilerplate" risk that fix's own commentary already anticipated
  as a residual limitation.
* These are evidence-based editorial decisions for the reviewed examples, not a population-level
  precision estimate or human certification of the full dataset.

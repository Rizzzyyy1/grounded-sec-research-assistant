# FinSight — Dataset integrity: what has and hasn't been touched

This page exists so no evaluation number is ever presented as measuring performance on data the
system was never shown. Read it before quoting any accuracy or citation-hygiene figure as
evidence of generalization, and before adding a new fix that might be tuned against a split this
page says is no longer clean.

## The three files, and their actual status today

| File | Rows | Status | Why |
|---|---|---|---|
| `data/eval/gold_v1.jsonl`, split `dev` | ~103 | **Tuning set. Never a holdout.** | By design (`docs/EVALUATION.md` §2): "dev" means used for tuning. Every fix in this project that needed a numeric/router signal was checked against dev first. |
| `data/eval/gold_v1.jsonl`, split `test` | ~44 | **Frozen scoring split; no longer a blind holdout.** | Individual retrieval failures were inspected for `ERROR_ANALYSIS.md` §1 and informed subsequent citation work. Numeric and abstention grading are mechanical, but automatic grading does not prevent development from adapting to observed failures or repeated scores. Treat current results as measurements on an exposed split, not independent evidence of generalization. |
| `data/eval/gold_v2_draft.jsonl` | 38 | **Fully in-sample. Not a holdout in any sense.** | Run as a whole dozens of times across this project's sessions (`reports/runs/*-agent-ollama-natural*`, `*-agent-ollama-omitted-citation-subset*`) and used to *find and fix* specific bugs: the advice-guardrail hole (`ERROR_ANALYSIS.md` §3b, all 4 naturally-phrased advice questions), the ratio-phrasing router gap (`nat-ratio-009`/`nat-ratio-010`, §3e), the citation-attribution mechanism itself (`nat-txt-027` through `038`, §3f/3g - individual rows read line-by-line against their actual retrieved passages to calibrate thresholds), and the ISO-27001 warning-suppression bug (`nat-txt-035`, §3g). At least 20 of the 38 rows are cited by exact ID somewhere in `ERROR_ANALYSIS.md`; the remaining rows were read as part of the same full-file passes even where not individually quoted. **No question in this file has ever been an untouched holdout, and none should be described as one.** |

## What this means for numbers already in the README

* **Accuracy and citation-hygiene numbers reported for `gold_v2_draft`** (the "natural probe" rows
  in `README.md`/`reports/RESULTS.md`) measure performance **after** fixes that were themselves
  developed by reading this exact file. They are honest evidence that the fixes work on the
  questions they were built from - they are not evidence the fixes generalize to different
  wording. This has been disclosed before (`docs/EVALUATION.md` §2.2, "the post-fix re-run is
  in-sample"), but is restated here as the single place to check before trusting any number from
  this file.
* **`gold_v1` `test`-split numbers** remain useful for regression comparisons on the frozen
  questions. Inspected failures and subsequent development limit independent generalization
  claims for the pipeline as a whole, including numeric and abstention results. A mechanical
  grader makes scoring repeatable; it does not make the evaluated examples unseen.
* **Label correctness and evaluation independence are separate.** Reviewing the labels can
  improve correctness but cannot reverse prior development exposure. The natural-probe
  post-fix results are explicitly in-sample; claims that no scored split was used for tuning
  would contradict that history.

## Why a new holdout is needed, and the rule for keeping it clean

Given the above, no existing file can produce a first "how does this generalize" number. A new
set was built for exactly that purpose - see `docs/HOLDOUT_V1_DESIGN.md` and
`data/eval/holdout_v1_draft.jsonl`. The rule going forward, stated once here so it doesn't need
restating on every future fix: **once a human has read a specific question's failure to decide
what to build, that question is no longer a holdout, even if its aggregate score is still
"frozen."** Label the file's status honestly at that point rather than continuing to call it
untouched.

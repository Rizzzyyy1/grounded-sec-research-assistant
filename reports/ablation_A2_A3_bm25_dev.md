BM25-only, dev split (n=29), contextual header on, section-level scoring.

| Preset | recall@8 [95% CI] | MRR | nDCG@8 | p50 ms | p95 ms | Δ vs chunk400_overlap15 (default) [95% CI] |
|---|---|---|---|---|---|---|
| chunk200_overlap15 | 0.879 [0.759, 0.983] | 0.690 | 0.379 | 1 | 1 | -0.017 [-0.138, 0.103] |
| chunk400_overlap15 (default) | 0.897 [0.793, 0.983] | 0.646 | 0.302 | 1 | 1 | - |
| chunk800_overlap15 | 0.828 [0.690, 0.966] | 0.527 | 0.251 | 0 | 0 | -0.069 [-0.190, 0.017] |
| chunk400_overlap0 | 0.914 [0.810, 1.000] | 0.674 | 0.315 | 1 | 1 | 0.017 [-0.086, 0.121] |
| chunk400_overlap30 | 0.776 [0.621, 0.914] | 0.597 | 0.285 | 1 | 1 | -0.121 [-0.241, -0.017] * |

`*` = paired-bootstrap 95% CI of the difference excludes zero (n=29 questions).

Index size:

* `chunk200_overlap15`: 38,120 chunks
* `chunk400_overlap15 (default)`: 23,221 chunks
* `chunk800_overlap15`: 17,421 chunks
* `chunk400_overlap0`: 22,686 chunks
* `chunk400_overlap30`: 23,856 chunks

The dense half (embeddings) was **not** measured: re-embedding costs ~35 minutes per configuration.

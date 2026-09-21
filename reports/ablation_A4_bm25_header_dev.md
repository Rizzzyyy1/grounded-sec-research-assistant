| Preset | recall@8 [95% CI] | MRR | nDCG@8 | p50 ms | p95 ms | Δ vs bm25_no_header [95% CI] |
|---|---|---|---|---|---|---|
| bm25_no_header | 0.586 [0.414, 0.759] | 0.352 | 0.160 | 0 | 1 | - |
| bm25_with_header | 0.897 [0.793, 0.983] | 0.646 | 0.302 | 0 | 1 | 0.310 [0.155, 0.483] * |

`*` = paired-bootstrap 95% CI of the difference excludes zero (n=29 questions).

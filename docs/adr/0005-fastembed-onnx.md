# ADR-0005: FastEmbed (ONNX) for embeddings and reranking
* **Status:** Accepted · **Date:** 2026-09-21

## Context
Anyone should be able to reproduce results on a laptop CPU without a GPU or a multi-GB PyTorch
install, and CI must stay fast.

## Decision
Use **FastEmbed** (ONNX Runtime) with `BAAI/bge-small-en-v1.5` (384-d) for dense embeddings and a
MiniLM cross-encoder for reranking. Models are behind `Embedder` / `Reranker` protocols; a
deterministic hashing embedder is the test fake. Model choice is an *ablation*, not a belief (A5, A6).

## Alternatives considered
* *sentence-transformers / PyTorch* — broader model zoo; heavy install; slower cold start.
* *Hosted embeddings API* — better quality possible; adds cost, network dependence, and makes the
  evaluation irreproducible if the provider changes the model.

## Consequences
+ Light, reproducible, fast in CI. − Smaller model zoo; bge-small may under-perform larger models,
which A5 will quantify honestly.

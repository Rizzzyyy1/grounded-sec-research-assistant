"""Load test the real API (one uvicorn worker, real index) at increasing concurrency.

    .venv/bin/python scripts/load_test.py

Starts `finsight serve` with the rate limiter raised, drives three endpoint families with a thread
pool at concurrency 1 / 4 / 16, and writes reports/load_test.md. Numbers are for *this machine* on
localhost with a single worker - a capacity sanity check, not a benchmark claim.
"""

from __future__ import annotations

import os
import platform
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
PORT = 8771
BASE = f"http://127.0.0.1:{PORT}"
QUERY = "/v1/query"
WORKLOADS = {
    "router: numeric question (XBRL tool)": (
        "POST",
        QUERY,
        {"question": "What was Apple's revenue in fiscal 2024?", "mode": "router"},
        96,
    ),
    "rag: text question (hybrid retrieval + extractive)": (
        "POST",
        QUERY,
        {"question": "What supply chain risks does Apple describe in fiscal 2024?", "mode": "rag"},
        48,
    ),
    "GET financials (DuckDB read)": (
        "GET",
        "/v1/companies/AAPL/financials?metrics=revenue,net_income",
        None,
        96,
    ),
}
LEVELS = (1, 4, 16)


def one(method: str, path: str, body: dict | None) -> tuple[float, int]:
    t0 = time.perf_counter()
    with httpx.Client(base_url=BASE, timeout=120) as c:
        r = c.request(method, path, json=body) if body else c.request(method, path)
    return (time.perf_counter() - t0) * 1000, r.status_code


def run(workload: tuple[str, str, dict | None, int], concurrency: int) -> dict[str, float]:
    method, path, body, n = workload
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        results = list(pool.map(lambda _: one(method, path, body), range(n)))
    wall = time.perf_counter() - started
    lat = sorted(ms for ms, _ in results)
    errors = sum(1 for _, status in results if status != 200)
    return {
        "n": n, "rps": n / wall, "p50": statistics.median(lat),
        "p95": lat[int(0.95 * (len(lat) - 1))], "max": lat[-1], "errors": errors,
    }  # fmt: skip


def main() -> None:
    env = {**os.environ, "FINSIGHT_API_RATE_LIMIT": "1000000", "FINSIGHT_LOG_LEVEL": "WARNING"}
    server = subprocess.Popen(  # noqa: S603
        [str(ROOT / ".venv" / "bin" / "finsight"), "serve", "--port", str(PORT)],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=ROOT,
    )  # fmt: skip
    try:
        for _ in range(60):
            try:
                if httpx.get(f"{BASE}/healthz", timeout=2).status_code == 200:
                    break
            except httpx.TransportError:
                time.sleep(2)
        else:
            raise SystemExit("server did not start")
        one(
            "POST",
            QUERY,
            {"question": "What was Apple's revenue in fiscal 2024?", "mode": "router"},
        )  # warm up
        one(
            "POST",
            QUERY,
            {"question": "What supply chain risks does Apple describe?", "mode": "rag"},
        )
        lines = [
            f"Machine: {platform.machine()} / {platform.system()}, {os.cpu_count()} logical cores; localhost; ONE uvicorn worker; "
            f"real index (23,221 chunks); no LLM calls. Requests per cell shown in `n`.",
            "",
            "| workload | concurrency | n | req/s | p50 ms | p95 ms | max ms | errors |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for name, workload in WORKLOADS.items():
            for c in LEVELS:
                m = run(workload, c)
                lines.append(
                    f"| {name} | {c} | {int(m['n'])} | {m['rps']:.1f} | {m['p50']:.0f} | {m['p95']:.0f} | {m['max']:.0f} | {int(m['errors'])} |"
                )
                print(lines[-1], flush=True)
        out = "\n".join(lines) + "\n"
        (ROOT / "reports" / "load_test.md").write_text(out, encoding="utf-8")
    finally:
        server.terminate()
        server.wait(timeout=15)


if __name__ == "__main__":
    sys.exit(main())

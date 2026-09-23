#!/usr/bin/env bash
# Focused Docker Compose smoke test: build, start, wait for health, ask one real question
# through the free local Ollama path, and report pass/fail. Leaves the stack running on
# success (matching `docker compose up -d`'s own behaviour) - run `docker compose down` when
# done. Verified against a real build; see ERROR_ANALYSIS.md for the session that added this.
#
#   make docker-smoke
#   ./docker/smoke_test.sh
set -euo pipefail
cd "$(dirname "$0")/.."

# Read FINSIGHT_API_PORT the same way `docker compose` does (from .env, not the caller's shell -
# a real bug caught while writing this script: without this, the check silently queried whatever
# unrelated service already happened to be listening on the hardcoded default port).
if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi
API_PORT="${FINSIGHT_API_PORT:-8000}"
BASE="http://127.0.0.1:${API_PORT}"

echo "==> docker compose config (validates the resolved stack)"
docker compose config -q

echo "==> docker compose up -d --build"
docker compose up -d --build

echo "==> waiting for ${BASE}/readyz"
ready=""
for _ in $(seq 1 60); do
    if body="$(curl -fsS "${BASE}/readyz" 2>/dev/null)"; then
        ready="$body"
        break
    fi
    sleep 2
done
if [ -z "$ready" ]; then
    echo "FAIL: ${BASE}/readyz never responded - check \`docker compose logs api\`" >&2
    exit 1
fi
echo "    $ready"
case "$ready" in
    *'"status":"ready"'*) ;;
    *) echo "FAIL: /readyz did not report status=ready" >&2; exit 1 ;;
esac
case "$ready" in
    *'"llm_provider":"ollama'*) ;;
    *)
        echo "FAIL: /readyz llm_provider is not ollama - set FINSIGHT_LLM_PROVIDER=ollama and" \
             "FINSIGHT_OLLAMA__BASE_URL=http://host.docker.internal:11434 in .env" >&2
        exit 1
        ;;
esac

echo "==> asking a real question through the agent"
answer="$(curl -fsS -X POST "${BASE}/v1/query" -H 'Content-Type: application/json' \
    -d '{"question": "What was Apple'"'"'s revenue in fiscal 2024?", "mode": "agent"}')"
case "$answer" in
    *'"text":""'*|*'"error"'*)
        echo "FAIL: query returned no answer or an error:" >&2
        echo "$answer" >&2
        exit 1
        ;;
esac
echo "    $(echo "$answer" | python3 -c 'import json,sys; print(json.load(sys.stdin)["answer"]["text"])')"

echo "==> PASS: build, health, readiness and a live Ollama-backed query all verified"

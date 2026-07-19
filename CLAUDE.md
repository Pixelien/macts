# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

MACTS (Multi-Agent Crypto Trading System) — a personal engineering project, not a commercial
product. It's a multi-agent, ML/LLM-assisted crypto trading system for Binance Futures, built
as (1) a systems-engineering lab (microservices, message queues, time-series DB, ML pipeline,
observability) and (2) trading research. **It does not trade live money.** Internal docs and
most code comments/logs are in Turkish — keep that convention when editing existing files.

**Always check `STATUS.md` first** for what's actually true today (data as of the file's own
"son güncelleme" line — update it whenever you check). Most files under `docs/` (ARCHITECTURE.md,
CONTRIBUTING.md, RISK_POLICY.md, AGENT_SPECS.md) are explicitly marked `⚠️ VISION SPEC` — they
describe the target design, not necessarily what's implemented. Don't assume something exists
because it's in one of those docs; grep for it.

## Commands

```bash
make dev-install          # pip install -e ".[dev]" + pre-commit install
make lint                 # ruff check src/ tests/
make format                # ruff format src/ tests/
make typecheck             # mypy --strict src/
make test                  # unit + integration
make test-unit             # pytest tests/unit/ -v
make test-integration      # pytest tests/integration/ -v (needs Docker services up)
pytest tests/unit/test_llm_core.py -v          # single file
pytest tests/unit/test_llm_core.py::test_foo   # single test
make cov                   # coverage html+term report

make up-testnet            # docker compose -f docker-compose.yml -f docker-compose.testnet.yml up -d
make down                  # stop everything
make logs-agent AGENT=ai_analyst   # tail one agent's logs
make build                 # rebuild image after code changes, then:
docker compose up -d --force-recreate <service_name>

python -m src.cli agent list        # verify AGENT_REGISTRY entries
python -m src.cli config validate
```

`ta-lib` is a compiled C dependency — if `pip install` fails or `numpy`/`talib` imports error
locally, that's an environment issue (the Docker image builds it), not a code bug.

Note: the target VPS uses docker-compose **v1** syntax (`docker-compose`, not `docker compose`) —
match whichever form the file you're editing already uses.

## Architecture

Event-driven microservices: every agent is an independent asyncio process/container that only
talks to others via the message bus (Redis Streams for low-latency, Kafka for high-volume/audit).
No agent calls another directly.

### Data flow (current, see STATUS.md for what's actually wired up)

```
Market Scanner ──universe──> Data Collection ──ticks──> Feature Engineering
                                                                │
                                          ┌─────────────────────┼──────────────┐
                                          ▼                     ▼              ▼
                                  Per-Coin Learning      Signal Generation   AI Analyst
                                  (predictions)          (raw signals)      (regime/risk
                                                                │            context, NOT a
                                                                ▼            signal input —
                                                       Risk Management       see below)
                                                                │
                                                    ┌───────────┴───────────┐
                                                    ▼                       ▼
                                               Execution               Simulation (paper)
```

Redis stream naming: `stream:{topic}.{subtopic}`, symbol-scoped streams use lowercase symbol,
e.g. `stream:features.btcusdt`, `stream:predictions.btcusdt`, `stream:signals.raw`,
`stream:signals.approved`. Full topology table: `docs/ARCHITECTURE.md`.

### BaseAgent (`src/agents/base/base_agent.py`)

All agents subclass `BaseAgent` and implement `_run()`; optionally override `_initialize()`,
`_shutdown()`, `_on_message()`, `_health_check()`. It provides: lifecycle
(initialize → start → run → stop), Redis/Kafka bus setup, heartbeat loop (`stream:heartbeats`),
Prometheus metrics server on port 8000, SIGTERM/SIGINT graceful shutdown, and per-agent error
isolation (a crashed agent doesn't take down others). `run_agent(AgentClass)` is the CLI/
container entrypoint helper.

To add a new agent, **use the `macts-agent-scaffold` skill** — it encodes the directory layout,
`agent.py` conventions (pure-logic modules split out for testability, stop-event wait pattern,
`_apply_universe` dual-format parsing), `AGENT_REGISTRY` entry in `src/cli.py`, docker-compose
`x-agent-base` anchor usage, stream naming, feature-flag pattern, and Prometheus metric naming.

### Config

`src/core/config/loader.py` defines `AppConfig` (pydantic) loaded from `config.example.yaml`
(actual `config.yaml` is VPS-local, gitignored). **All `AppConfig` fields are required** — adding
a field means updating the VPS's live config too, so for new/experimental agents prefer env-only
config first (see `ENABLE_AI_ANALYST` pattern) rather than extending `AppConfig`. Secrets always
come from `.env` (see `.env.example` for the full var list), never from YAML, never committed.

### Agents (`src/agents/*`, registry in `src/cli.py::AGENT_REGISTRY`)

Working: `market_scanner`, `data_collection`, `feature_engineering`, `per_coin_learning`,
`risk_management`, `signal_generation`, `monitoring`, `ai_analyst`. Skeleton/stub:
`execution`, `simulation`, `portfolio_manager`, `backtesting`, `model_registry`,
`circuit_breaker`, `grid_bot`. Check `STATUS.md` before assuming a skeleton agent does anything
live.

### AI Analyst / LLM subsystem (`src/agents/ai_analyst/`, `src/core/llm/`)

Actively-developed subsystem, distinct from the ML prediction path (`per_coin_learning`).
Calls NVIDIA NIM (OpenAI-compatible API) to produce structured `AIAnalysis` output
(`src/models/schemas.py`), gated by `ENABLE_AI_ANALYST` feature flag.

- **Load `nvidia-nim-client` skill** before touching the NIM client, model IDs, or rate limits —
  quota numbers there are live-probe-verified and change (most recently: real daily cap is
  ~1,000 req/day, not the nominal 2,000).
- **Load `llm-trading-prompt-eng` skill** before editing anything under `config/prompts/` —
  published prompt versions are immutable (a behavior change is a new `_vN` file, never an edit
  in place), because A/B comparison keys off `AIAnalysis.prompt_version`.
- **Load `rate-limit-guardian` skill** before adding any other rate-limited external integration
  — the cache → token-bucket → fallback-chain → backoff → usage-tracking layering in
  `src/core/llm/` is the reference pattern to copy, including the "429 never triggers a provider
  fallback" rule (quota is global per key, so falling back just burns the same bucket).
- **Load `continuous-improvement-loop` skill** for the prediction-vs-outcome evaluation job,
  metric naming (`macts_ai_analyst_*`), and the bandit/A-B mechanism design.
- Current status (as of STATUS.md Paket 6, 18 Tem 2026): LLM directional signal integration is
  **permanently cancelled** (`LLM_WEIGHT=0`) — 12 days of outcome data showed no edge over base
  rate. The subsystem's role is pivoting to regime/risk commentary instead of direction
  prediction; see `docs/AI_RISK_CONTEXT_DESIGN.md` (pending approval). Don't reintroduce the
  signal-weighting path without checking this history first.

### Risk-sensitive areas

Per `docs/CONTRIBUTING.md`, changes to `risk_management/`, `circuit_breaker/`, `execution/`, and
`src/models/schemas.py` (Order, Position, Signal) are called out as needing extra scrutiny —
treat these with more care than a typical agent change. Risk parameters themselves (position
sizing via half-Kelly, drawdown cooldown thresholds, leverage caps) are in `docs/RISK_POLICY.md`.

### Testing conventions

Pure/non-networked logic (schedulers, parsers, classification functions) lives in separate
modules specifically so `tests/unit/` can cover it without mocking Redis/Postgres — follow that
split in new code. Integration tests (`tests/integration/`) require live Docker services.
Message schemas (`src/models/schemas.py`, `BaseMessage` subclasses) should have tests asserting
invalid payloads are rejected.

## Operational traps on this VPS (hard-won knowledge — read before touching Docker)

- **AppArmor lock — root cause measured, not a hypothesis (diagnosed 19 Jul 2026)**: `docker
  stop/restart/rm` may fail with `permission denied`. Root cause: this VPS has BOTH apt
  (`docker.io`) and snap Docker installed and enabled. At boot, snap's dockerd starts ~8s after
  apt's `docker.service` and rebinds `/var/run/docker.sock` under itself (a socket race) — so
  ALL `docker`/`docker-compose` commands currently talk to the **snap** engine (77G data, 24
  containers, 15 `macts_*` volumes). Apt's own engine shows `active running` in systemd but is
  unreachable via the shared socket and completely empty (0 containers, 0 volumes). This
  ordering is NOT guaranteed — it can flip on a future reboot.
  **If `docker ps` / `docker-compose ps` ever comes back empty, DO NOT PANIC and do not spin up
  a fresh stack** — first run `docker info | grep "Docker Root Dir"` to check which engine
  you're actually talking to (an unexpectedly small/empty root dir means you landed on the
  orphaned apt engine; the real data is intact on the snap side). Recipe for the actual
  AppArmor symptom: `kill -9 $(docker inspect -f '{{.State.Pid}}' <container>)` then
  `docker rm -f <container>` then `docker-compose up -d --no-deps <service>`. Never fight the
  daemon API in a loop. Migration plan (not yet executed — remove the apt install, keep snap as
  the single engine) is tracked in `STATUS.md` under "Bilinen Altyapı Sorunları".
- **Ghost containers**: failed recreates leave renamed copies (`<hash>_macts-...`). If
  `docker ps` shows a hash-prefixed name, the RUNNING one is the OLD config — kill it,
  remove it, recreate properly. Also remove the corpse locking the clean name.
- **Kafka false-unhealthy**: health check runs `exec`, which the AppArmor lock can block —
  status shows `unhealthy` with health-log exit codes `-1` while Kafka works fine. Therefore
  ALWAYS use `--no-deps` when starting individual agents; never trust the unhealthy flag alone.
- **Image**: single shared image `macts/agent:latest`; ONLY `agent-market-scanner` has a
  `build:` block. After code changes: `docker build -f docker/Dockerfile.agent -t
  macts/agent:latest .` then `--force-recreate` the affected agents (with `--no-deps`).
- **Env precedence**: hand-written values in `.env` override compose defaults
  (bit us with `AI_ANALYST_INTERVAL_SECONDS`). When a config change "doesn't take", check
  `.env` first, verify with `docker exec <c> printenv <VAR>`.
- **Safety**: this box runs a live (testnet) trading stack. No bulk cleanup
  (`docker system prune` etc.), no `.env` edits, no stopping infra
  (redis/postgres/kafka/influx) without explicit user approval.

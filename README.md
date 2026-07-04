# Retail store agent

A command-line agent that runs a small retail store: sales, returns,
restocking, promotions, and questions about revenue, margin, and stock. The
store's records are in `data/`, the business rules are in
`DATA_DICTIONARY.md`, and the original assignment brief is in
`ASSIGNMENT.md`.

## Deliverables

1. **Domain model** — [`docs/domain-model.md`](docs/domain-model.md)
2. **Tool/action layer** — [`docs/tools.md`](docs/tools.md)
3. **Runnable agent** — `uv run acceptDaniel` (single terminal command; see "Run it" below)
4. **Approach writeup** — [`APPROACH.md`](APPROACH.md)

## Run it

You need [uv](https://docs.astral.sh/uv/) and an LLM API key.

```bash
uv sync
cp .env.example .env   # paste in your key
uv run acceptDaniel
```

The agent works with whatever key you have. It checks `OPENAI_API_KEY`
first, then `ANTHROPIC_API_KEY`, then Cloudflare Workers AI credentials. Any
other OpenAI-compatible endpoint works through `LLM_BASE_URL`. If
`OPENAI_API_KEY` is already exported in your shell, you can skip the `.env`
step.

Type instructions in plain English. `/reset` starts a fresh session
and `/quit` exits.

## How it works

The model never does the math. It translates your instruction into calls
against 17 tools, and the tools run deterministic, unit-tested Python over a
SQLite database that is rebuilt from `data/` at the start of every session.

More detail:

- `APPROACH.md` explains the approach
- `docs/domain-model.md` covers the schema and the reasoning behind it
- `docs/tools.md` lists every tool the agent can call
- `ITERATION.md` logs each eval-driven architecture change since the initial submission

## Tests

```bash
uv run pytest               # 58 unit tests for the business rules, no API key needed
uv run python evals/run.py  # scripted conversations against the live model (see evals/scenarios/)
```

The eval runner gives each scenario a fresh store, checks the database state
after every turn, and writes a scorecard to `evals/results/`.

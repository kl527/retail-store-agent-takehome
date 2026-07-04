# Retail store agent

A command-line agent that runs a small retail store: sales, returns,
restocking, promotions, and questions about revenue, margin, and stock. The
store's records are in `data/`, the business rules are in
`DATA_DICTIONARY.md`, and the original assignment brief is in
`ASSIGNMENT.md`.

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

Type instructions in plain English. The agent remembers the conversation, so
a follow-up like "now refund that" works. `/reset` starts a fresh session
and `/quit` exits.

Some things to ask:

```
Ring up two Classic Tees, Blue Medium, and one Canvas Tote for a walk-in paying cash, dated today.
Sarah Chen is returning one Navy Large hoodie from order O-1006. It's in good condition.
Reorder anything that's below its reorder point, from the best supplier.
What were my top five products by profit margin last month?
```

## How it works

The model never does the math. It translates your instruction into calls
against 16 tools, and the tools run deterministic, unit-tested Python over a
SQLite database that is rebuilt from `data/` at the start of every session.
The numbers you see come out of tested code, not out of the model.

More detail:

- `APPROACH.md` explains the approach and how it was tested
- `docs/domain-model.md` covers the schema and the reasoning behind it
- `docs/tools.md` lists every tool the agent can call

## Tests

```bash
uv run pytest               # 45 unit tests for the business rules, no API key needed
uv run python evals/run.py  # 17 scripted conversations against the live model
```

The eval runner gives each scenario a fresh store, checks the database state
after every turn, and writes a scorecard to `evals/results/`.

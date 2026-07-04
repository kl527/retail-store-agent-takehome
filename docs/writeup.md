# Approach

## The one constraint that shaped everything

Grading runs 125 prompts, 115 of them unseen. That rules out tuning against
the sample prompts and forces an architecture where correctness doesn't
depend on the model having a good day:

> **The LLM never does math.** It translates language into tool calls and
> relays results. Every price, proration, refund, supplier choice, margin,
> and velocity number comes from deterministic, unit-tested code.

Concretely, the seven frozen business rules in `DATA_DICTIONARY.md` map to
pure functions in `src/store_agent/domain/`, each pinned by unit tests with
hand-verified numbers (e.g. O-1006's $54.00 hoodie / $16.20 tote paid prices
straight from the data dictionary's own worked example).

## Layers

1. **SQLite, rebuilt per session** from `data/*.csv`. Deterministic starting
   state for every grading session; mutations are ordinary inserts/updates.
2. **Domain logic** (`domain/`): Decimal-exact money, validate-before-write
   atomicity (a failed action leaves zero trace), structured `DomainError`s.
3. **Tool layer** (`tools.py`): 16 tools — the only door to the data. Errors
   return as `{"error", "details"}` payloads so the model can explain a
   failure (e.g. "only 4 totes on hand") instead of crashing or forcing it.
4. **Agent** (`agent.py`): system prompt + tool-calling loop + session
   memory. The prompt encodes policy, not facts: resolve before acting, ask
   when ambiguous, never attach a guessed customer, never override a tool
   error.

## Decisions worth calling out

- **Disambiguation.** "A hoodie in medium" matches Gray-M and Navy-M. The
  matcher returns candidates; the agent is instructed to ask one short
  question when the user's words genuinely underdetermine the variant, and
  to proceed silently when exactly one match remains ("grey medium hoodie"
  sells without a question).
- **Insufficient stock refuses the whole sale.** No partial fulfillment on
  "ring up ten totes" (4 on hand) — the tool refuses atomically and reports
  the shortage; the agent relays it.
- **Missing purchase orders.** Prompt 5 references an open PO that isn't in
  the seed. Policy: check `list_purchase_orders`; if absent, create the PO
  exactly as the user described, then receive against it.
- **Margin interpretation (rule 6).** The rule excludes only
  returned-and-restocked units from both revenue and cost. A *damaged*
  return therefore stays in margin (its refund hits net revenue instead).
  This is the literal reading of the frozen rule; it's implemented and
  tested that way.
- **Velocity window (rule 7)** is pinned to May 2026 sales, as the rule
  specifies, rather than a rolling window off the wall clock.
- **Analytics fallback.** Report tools encode the frozen definitions of
  revenue/margin/stock-out; a read-only `run_sql` tool (SELECT-only,
  enforced with SQLite's `query_only` pragma) covers long-tail questions,
  with the tool description steering the model to prefer the rule-encoding
  reports.

## Model choice

The client speaks the OpenAI chat-completions wire format, so the provider is
a config decision, not an architecture decision — deliberately, since whoever
grades this shouldn't need my API key. The agent auto-detects whichever key
is present: `OPENAI_API_KEY` (default `gpt-5.4-mini`), `ANTHROPIC_API_KEY`
via Anthropic's OpenAI-compatible endpoint (`claude-sonnet-5`), or Cloudflare
Workers AI (`@cf/zai-org/glm-4.7-flash` — explicitly optimized for multi-turn
tool calling and cheap enough at $0.06/$0.40 per M tokens to run the eval
suite on repeat during development). `LLM_MODEL` overrides the model for any
provider; `LLM_BASE_URL` points at anything else that speaks the protocol.
Because correctness lives in the tool layer, swapping models changes tone and
tool-selection reliability, not arithmetic — which is what the eval scorecard
measures per provider.

## Testing methodology

Two tiers, deliberately separated:

- **43 unit tests** (no API key, milliseconds): one concern per rule —
  proration rounding, inclusive promo windows, no-stacking, over-return
  guards, supplier eligibility (Pioneer is cheaper for totes but 14-day lead
  disqualifies it; Pioneer *wins* for mugs at exactly 10 days), restock scan,
  hand-computed May figures (gross $1,786.20 / net $1,732.20; margins Tee
  $420, Hoodie $282, Sock $120, Tote $108.20, Mug $70).
- **17 eval scenarios** (live model): each gets a fresh store + session; the
  harness asserts on **database state** after each turn (plus reply
  substrings and which tools were/weren't called), not on an LLM judge. They
  cover the 10 known prompts, multi-turn memory ("now refund that", answering
  a clarifying question), paraphrases, and traps (stock shortage, over-return,
  ambiguity). `evals/run.py` writes a pass/fail scorecard to `evals/results/`.

The development loop was: implement → unit tests green → run the eval
scorecard → fix whatever failed (prompt policy, tool descriptions, or code) →
re-run, until the suite passed twice consecutively (final state: 17/17 ×2 on
gpt-5.4-mini). The unit suite caught a real atomicity bug (a failed PO line
left a half-created PO in the transaction) before any model ever ran.

Every eval failure converted into a deterministic robustness fix rather than
a bigger-model workaround — the point being that a weaker model has fewer
ways to fumble, not that a stronger one papers over them:

- The model passed supplier *names* where IDs belong → tools resolve any
  unambiguous reference (ID, name, product description), and "unknown X"
  errors list the valid options so the model can self-correct.
- The model scoped "all hoodies" to the whole apparel category → the promo
  outcome looked right (hoodies got 20% off) but the state was wrong (so
  would tees) — caught only because evals assert on the database, not the
  reply. Fixed in the tool description.
- The model quietly processed a 1-unit return when the user asked for 2
  (only 1 was returnable) — partial fulfillment without consent. Fixed by
  colocating the policy in the tool description, which proved far more
  binding for small models than the same rule in the system prompt.
- The model answered "ring up X and tell me the price" with a quote and no
  sale. Fixed by making ring_up_sale/get_price_quote descriptions mutually
  exclusive about when they apply.

## What I'd do next

- Grow the eval suite toward the hidden-prompt distribution: more analytics
  phrasings, date arithmetic ("the week before last"), compound instructions.
- Cache/replay LLM transcripts so eval regressions can be bisected without
  API calls.
- A `--seed-session` flag to run scripted setup turns before an interactive
  session, for demoing multi-step flows quickly.

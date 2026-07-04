# Iteration log

This file tracks an ongoing eval-driven loop on top of the original
submission: run the eval suite against the live model, treat every failure
as a real signal about the architecture (a missing guardrail, a weak tool
description, an unhandled edge case), fix it, rerun, and repeat. Once the
suite is at (or very near) 100%, the next iteration adds a harder batch of
eval scenarios rather than stopping, so the bar keeps moving.

Each iteration is logged here as: what was tested, what broke, why, what
changed, and the before/after result. Commits are made per iteration.

---

## Iteration 1 — 2026-07-03

### What was added

53 new eval scenarios (`evals/scenarios/{a,e,mm,pm,po,rp,rt}*.yaml`), on top
of the original 17 (`p01`–`p10`, `v01`–`v05`, `m01`–`m02`), covering:

- **Sales edge cases** (`e01`–`e13`): exact-depletion boundary, atomic
  refusal of a mixed-shortage order, duplicate-SKU merge, user-specified
  order discount, missing payment method / date, direct SKU/product_id
  reference, email and case-insensitive customer lookup, relative dates
  ("yesterday"), word quantities ("a dozen", "half a dozen").
- **Adversarial/ambiguity cases** (`a01`–`a10`), designed specifically to
  find reasoning failures rather than exercise code paths: zero-hint
  variant ambiguity, negation that narrows but doesn't resolve ambiguity,
  a nonexistent variant a model might "helpfully" substitute, a
  customer-name/order mismatch, gross-vs-net revenue phrasing, an
  over-return beyond what was ever sold, a hypothetical pricing question
  that must not mutate state, "undo my last sale" with no delete tool,
  a typo that must not fuzzy-match, and a single turn bundling two intents.
- **Returns edge cases** (`rt01`–`rt07`): unknown order, SKU not on the
  order, exact-remaining-quantity boundary, missing condition, return
  resolved via customer name only (no order id given), zero quantity,
  full multi-line order return.
- **Restocking/PO edge cases** (`po01`–`po07`): nothing below reorder,
  supplier tie-break at the exact 10-day lead-time boundary, a supplier
  that doesn't carry the product, receiving against a closed PO,
  over-receiving, an ambiguous PO reference with two open POs, sequential
  partial receives closing a PO.
- **Promotions edge cases** (`pm01`–`pm07`): category-wide scope,
  overlapping product+category promos (lower price wins even when the
  category promo is "less specific"), inclusive boundary dates on both
  ends, invalid percent (0%, 150%), end-before-start, unknown category.
- **Reports edge cases** (`rp01`–`rp06`): margin unaffected by a damaged
  (non-restocked) return, revenue report over a zero-order period, margin
  report over a zero-sales period (`DomainError`, must not crash),
  stockout flipping after a depleting sale, `run_sql` for an ad-hoc read,
  `run_sql` write attempt refused.
- **Multi-turn/session memory** (`mm03`–`mm05`): a three-turn pronoun chain
  (sale → "she wants to return it" → "sell her another one of the same"),
  a single-turn self-correction ("two — no wait, three —"), a two-turn
  clarification where the follow-up supplies two missing dimensions at once.

Ground truth for every scenario was computed by driving the domain layer
directly (`store_agent.domain.*`) against a fresh seeded store — not
hand-derived — so a failing check means a real behavior gap, not a bad
expectation. (Exception: two scenarios turned out to have their expectation
invalidated by an *improvement* in agent behavior partway through this
iteration — see below.)

### Baseline run

First run at `--workers 10` was contaminated by shared API rate limits
(HTTP 429 on 33 of 70 scenarios, confirmed via transcript inspection —
`gpt-5.4-mini`'s 200k TPM budget can't cover 10 concurrent multi-turn
conversations). Re-run at `--workers 2`:

**65 / 70 passed.** 5 genuine failures, all reproducible:

| Scenario | What happened | Why it matters |
|---|---|---|
| `a07-hypothetical-promo-must-not-mutate` | Asked "what would a hoodie cost if I put it on 30% off?" — the agent called `create_promotion` and actually created a real, persistent promotion to answer a hypothetical question. | A store manager idly asking "what if" would accidentally launch a real sale. |
| `po06-ambiguous-po-reference` | Two open POs existed; "the shipment just arrived, receive it" — the agent called `list_purchase_orders` (correctly), saw two open POs, then silently received **both** instead of asking which one. | Silent over-execution on an ambiguous reference — the same failure mode `search_products` already guards against for products, but nothing told the PO tools to do the same. |
| `rt04-missing-condition-must-ask` | "Sarah Chen is returning the Canvas Tote from order O-1006" (no condition stated) — the agent called `process_return` with `condition='good'` assumed. | `good` vs `damaged` changes whether the unit is restocked; guessing the common case is still guessing, and guessing wrong corrupts inventory. |
| `rt07-full-multiline-order-return` | "Return everything from order O-1012" (3 lines) — the agent called `get_order`, saw all 3 lines, then **stopped and asked** "which items and quantities?" instead of processing all 3 — even though the order data it already had fully answered that question. | Excess caution/friction on a genuinely unambiguous bulk request. |
| `a04-customer-order-mismatch` (soft check) | "Priya Patel wants to return... from order O-1006" — O-1006 actually belongs to Sarah Chen (C-001). `process_return` has no `customer_id` param, so it processed the return correctly, but the reply echoed "Customer: Priya Patel" as fact, never noting the order belongs to someone else. | The tool layer can't catch this (it doesn't take a customer argument) — only the agent's own reasoning can, and it wasn't checking. |

### Changes made

1. **New tool `simulate_discount_price(sku, percent_off)`**
   (`src/store_agent/domain/pricing.py`, `src/store_agent/tools.py`) — a
   read-only hypothetical-price calculator that persists nothing. Kept the
   "LLM never computes store math" principle intact (per `APPROACH.md`)
   instead of telling the model to do the arithmetic itself.
   `create_promotion`'s description was tightened to explicitly forbid
   using it for hypotheticals and to point at the new tool instead.
2. **`process_return` description** (`tools.py`) — added: ask if condition
   is unstated, never default to `good`; and explicit bulk-return guidance
   (call `get_order`, then one `process_return` per line using the
   quantities already shown — that data is a complete, unambiguous spec).
3. **`receive_purchase_order` description** (`tools.py`) — added: if the
   user's phrasing doesn't identify which PO among several open ones, call
   `list_purchase_orders` and ask — don't guess by receiving all of them.
4. **`create_purchase_order` description** (`tools.py`) — added (in a
   later pass, see below): if the user describes a PO as if it already
   exists and none is found, create it immediately with the given details
   and continue — do not stop to ask for confirmation first.
5. **System prompt** (`src/store_agent/agent.py`) — rule 3 broadened to
   cover a missing *required detail* (not just multiple candidate
   matches) as something to ask about, not guess; rule 6 amended for the
   ambiguous-PO case; new rule 9 (hypotheticals must never mutate state)
   and rule 10 (cross-check a named customer against `get_order`'s actual
   customer, flag mismatches instead of proceeding silently).
6. **`temperature=0`** (`src/store_agent/llm.py`) — see below.

### First re-run: 65/70 (same number, different scenarios)

`a07` and `po06` now passed. But re-running surfaced that **two of the
original five were eval-design bugs, not agent bugs** — the agent's
behavior had *improved* past what I'd asserted:

- `a04`: the agent now actually says *"it belongs to a different
  customer, C-001, not Priya Patel"* and asks which is correct — holding
  off on the return entirely. My original assertion expected the return to
  still be processed (softly checking for awareness in the reply text);
  that was the wrong bar once rule 10 made the agent withhold action
  instead. **Fixed the eval**, not the agent: updated `a04` to expect no
  new return, unchanged inventory, and `tool_not_called: [process_return]`.
- `rt07`: the agent now correctly processes all 3 lines (3 `process_return`
  calls, correct quantities and refunds — $120, $120, $60) but reports them
  individually rather than a summed "$300.00" total, which nothing asked
  for and which rule 1 arguably discourages the model from computing
  itself. **Fixed the eval**: checks for `120.00`/`60.00` (the tool's own
  authoritative numbers) instead of a total the agent would have had to
  add up itself.

`rt04` still failed on this run (condition still assumed as `good`). And
two *previously-passing* scenarios flickered: `p05-receive-partial-po` and
`po07-sequential-partial-receives-close` both intermittently had the agent
stop to ask "want me to create that PO?" instead of following system rule
6 ("create it exactly as described, then continue"). Re-running each
standalone showed they pass in isolation — confirmed as run-to-run model
variance, not a regression from the prompt edits (nothing in the diff
touches that code path). **No `temperature` was being set at all** — the
API was using whatever sampling default it likes for a rules-following
agent that should behave identically on identical input.

### Second fix pass

- Added `temperature=0` to `ChatClient.complete` (`llm.py`) with a comment
  explaining why: this agent enforces frozen business rules, not creative
  writing, so identical input should produce identical tool calls.
- Strengthened rule 3 in the system prompt (missing required detail =
  ask, don't default to the common case).

Re-run: **68/70.** `rt04`, `a04`, `rt07`, `po06`, `a07` all passed. Two
failures remained:

- `p05-receive-partial-po` — same "asks before creating a described PO"
  pattern, still present even at `temperature=0` (confirms hosted-API
  temperature=0 reduces but doesn't fully eliminate decoding variance for
  this model).
- `pm02-overlapping-lower-price-wins-even-if-less-specific` — turn 2 hit
  another 429 (confirmed via transcript: `"Used 199174, Requested 909"` —
  right at the shared TPM ceiling even at `--workers 3`). Pure test
  infrastructure noise, not an agent bug.

### Third fix pass

Added the `create_purchase_order` description guidance (item 4 above) —
explicit: do not ask for confirmation before creating a PO the user has
already described as existing. Re-ran `p05` standalone 3/3 clean.

### Final result

Full suite, `--workers 2` (to keep clear of the shared rate limit):

**70 / 70 scenarios passed.**

### Open items / things worth knowing for next time

- `temperature=0` narrows but does not guarantee determinism on a hosted
  API — don't assume a single green run proves a fix; a flaky scenario
  should be re-run standalone a few times before being trusted either way.
- Confirmed again (this codebase's own prior finding, see `APPROACH.md`):
  a rule stated inside the specific tool's description is more reliably
  followed than the same rule stated only in the system prompt. Every fix
  in this iteration that stuck was a tool-description edit; the two
  system-prompt-only rules (9, 10) worked, but were paired with a
  system-prompt rule 3 amendment that needed the `rt04` tool description
  edit alongside it before it actually took.
- Eval infra: keep `--workers` at 2–3 for `gpt-5.4-mini` runs of this
  suite size, or 429s get misread as agent failures.

---

## Iteration 2 — pending

Suite is at 100%; per the plan, the next step is a harder batch of eval
scenarios rather than declaring victory. See below for what's added and
what it finds.

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

## Iteration 2 — 2026-07-03

Suite was at 100% (70/70), so per the plan this iteration adds a harder
batch rather than declaring victory. This time the hunt looked past agent
*behavior* and at the **domain layer itself** — are there business-rule
inputs nothing validates? Ground truth for two hypotheses was checked by
calling `domain.*` functions directly (no LLM involved), before touching
any eval or the live model:

### Found before writing a single eval

1. **`ring_up_sale`'s `order_discount_pct` was never bounds-checked.**
   `sales.ring_up_sale(conn, [{"sku": "TOTE", "quantity": 1}], "2026-06-19", order_discount_pct=150, ...)`
   returned a line with `paid_unit_price: "-9.00"` — a 150% "discount"
   silently produced a *negative* price, i.e. the store paying the
   customer to take the item. `create_promotion` already validates its
   own percent (`0 < pct <= 100`); the equivalent check on the order-level
   discount path had simply been missed.
2. **`revenue_report`/`top_products_by_margin` never validated `start_date <= end_date`.**
   A reversed range (`revenue_report(conn, "2026-05-31", "2026-05-01")`)
   silently returned `gross_revenue: "0.00"` — SQL `BETWEEN` on a swapped
   range just matches nothing. That reads as "no May revenue," which is
   indistinguishable from a genuine zero-revenue period, when it's really
   a swapped-date typo the report should refuse to answer for.

### Fixes made (domain layer, not just prompting)

- `domain/sales.py`: `ring_up_sale` now raises `DomainError` unless
  `0 <= order_discount_pct <= 100`. 100% (free) is a legitimate boundary
  and still allowed; anything above is not. Schema in `tools.py` gained
  `minimum`/`maximum` hints for the model.
- `domain/reports.py`: new `_check_date_range` helper, called from both
  `revenue_report` and `top_products_by_margin`, raises `DomainError` if
  `end_date < start_date`.
- Unit tests added for both (`tests/test_sales.py::test_order_discount_pct_out_of_bounds_rejected`,
  `tests/test_reports.py::test_reversed_date_range_rejected`) — 47 unit
  tests passing.

### New eval scenarios (`h01`–`h07`)

Deliberately not padded with easy cases — every one targets either the two
fixes above or a genuinely layered scenario that hadn't been tested yet:

| Scenario | What it checks |
|---|---|
| `h01-order-discount-over-100-rejected` | 150% discount refused, nothing recorded |
| `h02-order-discount-negative-rejected` | -10% "discount" (a surcharge in disguise) refused |
| `h03-hundred-percent-off-boundary` | Exactly 100% off is *allowed*, lands at $0.00 exactly (the boundary the fix must not over-reject) |
| `h04-layered-promo-discount-damaged-return` | Item promo (50%) + order discount (20%) + damaged return, in sequence — refund must reflect both layers ($4.80), not list price or promo-only price, and must not restock |
| `h05-mixed-condition-return-accumulation` | Good return + damaged return against the same line, in separate calls — the remaining-returnable cap must sum across *both* conditions, not track them independently; a third return attempt at the now-zero remainder must be refused |
| `h06-double-ambiguity-quantity-and-variant` | "Some hoodies" is ambiguous on two independent axes (quantity + variant) at once — must ask, not resolve either by guessing a default |
| `h07-reversed-date-range-flagged` | Agent must relay the new `DomainError` rather than reporting a bare, misleading $0.00 |

Ground truth for `h04`/`h05` was computed the same way as iteration 1 — by
calling `domain.sales`/`domain.returns`/`domain.pricing` directly against a
fresh store — before writing the YAML, so a failure means a real gap.

### Result

All 7 new scenarios passed on the first live run (`7/7`). Full suite
(77 scenarios: the 70 from iteration 1 + these 7): **77/77 passed.**

Unlike iteration 1, nothing here required an agent-behavior fix (no tool
description or system-prompt change) — both gaps were pure domain-layer
validation holes, and the fix was exactly at that layer. The eval suite
caught them anyway because the harness asserts on database state, so a
silently-wrong number (a negative price, a misleading zero) fails the
check even when the agent's reply "sounds" fine.

### Note for the next iteration

Per explicit instruction: future iterations should skip authoring
scenarios that are easy for the model to solve — the value of this
exercise is in finding gaps, not in growing the suite for its own sake.
The next batch should stay small and aim only at combinations or
domain-layer boundaries not yet covered (e.g., other unvalidated numeric
inputs, deeper multi-turn chains, or interactions between three or more
mechanisms at once), rather than broad coverage for its own sake.

---

## Iteration 3 — 2026-07-03

Per the note above, this batch is deliberately small (3 scenarios, not
another wide sweep) and continues iteration 2's approach: hunt for
unvalidated domain-layer inputs by calling `domain.*` functions directly,
*then* write the eval, rather than starting from prompt ideas.

### Found before writing a single eval

**No date parameter anywhere validates that it's a real calendar date.**
Dates are opaque `TEXT` to SQLite — nothing parses them. Confirmed:
`sales.ring_up_sale(conn, [...], "2026-02-30", ...)` (February only has 28
days in 2026) recorded the sale under `order_date = "2026-02-30"` without
complaint. Lower severity than iteration 2's two findings (lexicographic
string comparison still keeps a bogus date roughly "in order" relative to
real ones, so it doesn't corrupt reports the way a reversed range did),
but it's the same category of gap: a business-rule assumption ("dates are
real calendar dates") that nothing enforced.

Also explicitly checked, and confirmed *already correct* (a regression
guard, not a bug): rule 1's "cost is always Northwind's per-product cost,
wherever you need cost" — even for units actually restocked through
Pioneer Goods (the cheaper, rule-4-eligible supplier for mugs).
`top_products_by_margin` costs strictly via a join to Northwind's
`supplier_catalog` row, never the PO's own `unit_cost`, so margin doesn't
silently drift if a naive future change tried to use "whatever we actually
paid" instead.

### Fix made

New `domain/dates.py::validate_date(value, field)` — parses with
`datetime.date.fromisoformat`, raises `DomainError` on anything that isn't
a real calendar date. Wired into every date-accepting entry point:
`ring_up_sale`, `process_return`, `create_purchase_order`,
`receive_purchase_order`, `restock_below_reorder`, `create_promotion`
(both `start_date` and `end_date`), `applicable_promotions` (covers
`effective_unit_price` and therefore `get_price_quote` too), and
`revenue_report`/`top_products_by_margin` (via the existing
`_check_date_range` helper from iteration 2). 5 new unit tests in
`tests/test_dates.py`; 52 unit tests passing total.

### New eval scenarios (`h08`–`h10`)

| Scenario | What it checks |
|---|---|
| `h08-fractional-percent-rounding` | First non-whole-number percentage tested (12.5% off $25.00 lands exactly on a half-cent, $21.875) — must round half-up to $21.88, not truncate to $21.87 |
| `h09-invalid-calendar-date-rejected` | "2026-02-30" refused outright (the new fix), order count and inventory unchanged |
| `h10-cost-basis-fidelity-after-alt-supplier-restock` | 5-turn chain: deplete mugs → restock (Pioneer wins the tie-break, $4.50) → receive → sell more → margin report must still cost at Northwind's $5.00, confirming the already-correct behavior described above stays correct |

All 3 passed on the first live run. Full suite (80 scenarios): **79/80**
on the full concurrent run — the one failure (`rt03-exact-remaining-qty-boundary`,
unrelated to anything changed this iteration) reproduced as **3/3 pass**
on standalone reruns, consistent with the hosted-API decoding variance
already documented in iteration 1's open items, not a new regression.
Effectively **80/80**.

### Note for the next iteration

Same guidance as above still applies. Worth checking next: other
unvalidated numeric business rules (percent fields elsewhere, quantity
ceilings), and whether the flakiness pattern (agent asks for confirmation
on an already-fully-specified request) clusters around any particular
phrasing — if so, that's a tool-description fix, not something to keep
shrugging off as "just variance."

---

## Iteration 4 — 2026-07-03

Explicitly scoped small again (2 live evals, not a wide sweep). This batch
stayed at the `dispatch()`/domain boundary rather than the tool schemas —
the question was "what happens when the model's arguments don't match
what the JSON schema asked for," which a schema alone can't guarantee.

### Found before writing a single eval

1. **`dispatch()` only ever caught `DomainError` and `TypeError` — anything
   else crashed straight through.** Confirmed directly:
   `tools.dispatch(conn, "ring_up_sale", {"items": [{"sku": "TOTE",
   "quantity": "two"}], ...})` raised an uncaught `ValueError` all the way
   out of `dispatch()`. `create_promotion` with a non-numeric
   `percent_off` raised `decimal.InvalidOperation` the same way. Checked
   the blast radius: `cli.py`'s REPL loop only catches `LLMError` around
   `agent.run_turn(...)` — either of these would have crashed the entire
   interactive session, not just failed one turn gracefully like every
   other bad-input path already does.
2. **Every `int(quantity)` conversion silently truncates a fractional
   value instead of rejecting it.** `int(1.5) == 1`, no error — confirmed
   `ring_up_sale(..., quantity=1.5)` recorded an order for 1 unit with no
   complaint. Found at 4 call sites: `sales.ring_up_sale` (items),
   `returns.process_return`, `purchasing.create_purchase_order` (items),
   `purchasing.receive_purchase_order` (receipts, in both its validate and
   apply loops). Same severity class as iteration 2's findings — a silent
   wrong number, not a crash — since a real user asking for something
   nonsensical (half a hoodie) would have it silently and incorrectly
   rounded down instead of refused.

### Fixes made

- `tools.py::dispatch` now also catches `(ValueError, KeyError,
  decimal.InvalidOperation)` alongside the existing `TypeError`, rolling
  back and returning the same `{"error": ...}` shape as a `DomainError` —
  consistent with the module's own stated contract ("Domain errors come
  back as `{"error": ...}` so the model can explain the problem instead of
  crashing"), just widened to cover malformed arguments a JSON schema
  didn't stop.
- New `domain/quantities.py::whole_quantity(value, field)` — coerces via
  `float()` first, then checks the value equals its own truncation before
  accepting it as an int; rejects otherwise. Wired into all 4 call sites
  above (replacing the bare `int(...)` at each).
- 2 new unit test files (`test_tools.py::test_dispatch_survives_malformed_numeric_arguments`,
  `tests/test_quantities.py` — 5 tests) plus assertions in each affected
  domain module. 58 unit tests passing total (up from 52).

### New eval scenarios (`h11`–`h12`)

Only the fractional-quantity fix got live-agent scenarios — the
`dispatch()` crash fix doesn't have a natural-language trigger (it needs
the *model itself* to emit a schema-violating argument type, which isn't
something a prompt can reliably induce), so it's unit-tested only. That's
a deliberate scoping choice, not a gap: **not every domain-layer fix needs
a live eval** — some are exactly the kind of code-level defense that unit
tests are the right (and only reliable) tool for, and this is one to keep
in mind for future iterations rather than forcing a contrived prompt.

| Scenario | What it checks |
|---|---|
| `h11-fractional-quantity-sale-rejected` | "One and a half Gray Medium hoodies" — must not silently record an order for 1 |
| `h12-fractional-quantity-return-rejected` | Same, mirrored on the returns path against the seed's O-1006 |

Both passed on the first live run. Full suite (82 scenarios): **82/82.**

### Note for the next iteration

This was the last iteration requested for this session. If resumed later:
the same "call `domain.*` directly and look for an unvalidated assumption
before writing a prompt" method has now found 4 real gaps across 3
iterations (negative price, misleading zero, invalid dates, silent
truncation) — cheaper and more reliable than guessing at agent phrasing
that might expose a bug. Natural next candidates in that vein: whether
`limit` on `top_products_by_margin` accepts a negative or zero value
sensibly, and whether `receive_purchase_order`'s `receipts` can name a SKU
twice in one call (summed silently, double-counted, or rejected?).

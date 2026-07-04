# Approach

The grading setup shaped everything: 125 test prompts, and I only get to see
10 of them. There was no point tuning against the visible prompts, so I built
the system so that correctness does not depend on the model being clever.
The LLM translates instructions into tool calls and reports the results. All
the actual math (prices, refunds, supplier choice, margins) happens in plain
Python that I can unit test.

## Domain model

The CSVs load into an in-memory SQLite database at startup, so every session
starts from the documented 2026-06-19 state. The schema mostly follows the
files, with two decisions worth flagging.

First, `products.csv` is really two concepts: the sellable unit (the SKU a
cashier scans) and the product (what suppliers price and promotions target).
I keyed on SKU and kept `product_id` as a grouping column, which lets a
six-variant tee and a one-variant tote share the same code paths.

Second, purchase orders do not exist in the seed data at all, but the test
prompts create them, receive partial deliveries against them, and reference
ones that are already "open." So `purchase_orders` and its line table are
invented entities, with received quantity tracked per line and status
derived from it.

Money is stored as REAL for easy querying but every calculation crosses into
Python's Decimal with half-up cent rounding, per the data dictionary. Floats
never decide a price.

## Tool layer

The agent has 16 tools (see `docs/tools.md`). A few rules I followed when
designing them:

Tools are shaped like workflows, not primitives. `restock_below_reorder`
does the whole scan-and-order job in one call because asking a small model
to orchestrate five primitive calls correctly is asking for trouble.

Failures come back as structured errors the model can relay ("only 4 on
hand"), and where the fix is obvious the error teaches it: an unknown
supplier error lists the valid suppliers. Tools also accept any unambiguous
reference. "Northwind" resolves to SUP-NW, "Canvas Tote" resolves to the
TOTE SKU, and a genuinely ambiguous reference like "P-TEE" errors with the
six candidate SKUs.

Reports (revenue, margin, stock-out) are their own tools because the data
dictionary freezes those definitions, and I did not want the model
improvising SQL for numbers that have exactly one correct answer. A
read-only SQL tool exists for long-tail questions.

## Testing

Two layers, deliberately separate. 45 unit tests pin the business rules with
hand-checked numbers and run without an API key. Then 17 eval scenarios run
real conversations against the live model, and the harness asserts on the
database state after each turn rather than trusting the reply text. That
distinction mattered: at one point the model created an apparel-wide
promotion for "all hoodies" and the reply still quoted the right hoodie
price. Only the state check caught that tees were now discounted too.

The loop was: run the scorecard, read the failing transcript, fix the code
or the tool description, rerun until the suite passed twice in a row. Every
failure became a deterministic fix. The one finding I would pass along:
putting a behavioral rule inside the relevant tool's description was far
more binding for small models than stating the same rule in the system
prompt.

## Choices someone might argue with

When a request is ambiguous ("a hoodie in medium" when two colors exist),
the agent asks one short question instead of guessing. When a request can
only be partly fulfilled (ten totes, four in stock; return two, one
returnable), it refuses the whole action and explains, rather than doing
what it can silently. And a damaged return stays in margin per the literal
wording of rule 6, since the rule only excludes restocked units; the refund
shows up in net revenue instead.

The provider is a config detail. Whatever OpenAI-compatible key is present
gets used (OpenAI, then Anthropic, then Cloudflare Workers AI), because
whoever grades this should not need my API key.

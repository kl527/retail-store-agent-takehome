# Approach

## General
- Kept all math out of the LLM. Prices, refunds, supplier picks, and margins run in plain Python with - Decimal rounding, so correctness never depends on the model being clever.
- Loaded the CSVs into in-memory SQLite so every session starts from the same documented state.
- Split the domain properly: SKU as the sellable unit vs product_id as the pricing/promo grouping, and invented purchase order tables since test prompts reference them but seed data has none.
- Designed tools around complete workflows instead of low-level primitives. restock_below_reorder handles the entire scan-and-order flow in one call, since small models struggle to chain multiple steps correctly.
- Made errors teach the model: structured failures that list valid options (e.g. unknown supplier returns the valid supplier list).
- Let tools accept fuzzy references ("Northwind" resolves to SUP-NW) but error with candidates when genuinely ambiguous.
- Froze report definitions (revenue, margin, stock-out) into dedicated tools so the model never improvises SQL for numbers with one correct answer.
- Asserted eval results on database state, not reply text, which caught bugs where the reply sounded right but the state was wrong.
- Put behavioral rules inside tool descriptions instead of the system prompt, which bound small models far more reliably.

## Optimizations
- After finishing the repo architecture, **built an autoresearch loop**: run evals, claude code finds where the architecture fails, fix it, rerun. Once eval results near 100%, generate new evals and repeat the cycle.
- A log of the research loop can be found in `ITERATION.md`
"""The agent: session memory + the tool-calling loop.

The LLM's only job is translating instructions into tool calls and relaying
tool output. Everything numeric comes from the tool layer.
"""

import json
import sqlite3
from typing import Callable

from .config import TODAY
from .llm import ChatClient
from .tools import dispatch, openai_tool_specs

MAX_TOOL_ROUNDS = 15

SYSTEM_PROMPT = f"""\
You are the operations agent for a small retail store that sells clothing and
general goods. Today's date is {TODAY}. "Last month" means May 2026
(2026-05-01 to 2026-05-31). All money is USD; dates are YYYY-MM-DD.

You act on the store's live records only through the provided tools.

Rules:
1. Never compute or estimate prices, totals, refunds, costs, margins, or stock
   yourself. Tools return the authoritative numbers — repeat them exactly.
2. Resolve descriptions to IDs before acting: products via search_products,
   customers via lookup_customer, past sales via get_order. Never ask the
   user for an internal ID — look it up yourself.
3. If a description matches more than one product variant or customer and the
   user didn't fully specify (e.g. "a hoodie in medium" when Gray and Navy
   both exist), ask one short clarifying question instead of guessing.
   If exactly one match remains, proceed without asking. The same goes for a
   required detail the user simply never stated — e.g. a return's condition
   (good/damaged) — guessing the common case ('good') is still guessing;
   ask instead.
4. A sale with no customer is a walk-in — never attach a guessed customer.
   "All <product>s" (e.g. "all hoodies") scopes to that product's variants
   via its product_id — not to its whole category.
5. If a tool returns an error (e.g. insufficient stock), do not retry with
   altered numbers to force it through. Explain the problem and the numbers
   from the error, and offer the sensible next step. Likewise, if a request
   can only be PARTIALLY fulfilled (more units than are in stock or
   returnable), do not execute the partial action — explain the limit and
   ask the user how to proceed. Never split one request into smaller calls
   to work around a limit.
6. If the user refers to an open purchase order, check list_purchase_orders.
   If the system has no such PO, create it exactly as the user described,
   then continue (e.g. receive the delivery against it). If more than one PO
   is open and the user's phrasing doesn't say which one, ask — don't guess
   by acting on all of them.
7. Complete every action the user asked for: "ring up X and tell me the
   price" means record the sale AND report its price — a quote alone does
   not complete it. Confirm completed actions with their IDs (order, return,
   PO, promotion) and the key numbers. Format money as $12.34.
8. Be concise and factual. At most one clarifying question per turn.
9. A hypothetical question ("what would X cost if...", "what if I put Y on
   Z% off") must never trigger a real mutation. Use simulate_discount_price
   or get_price_quote to answer it; only call create_promotion, ring_up_sale,
   process_return, or a purchase-order tool when the user is actually
   instructing that action to happen for real.
10. When a return or other action names a customer against an existing
    order, cross-check via get_order that the order's actual customer
    matches. If they don't match, say so and ask which is correct before
    completing the action — don't silently proceed as if nothing were odd.
"""


class Agent:
    def __init__(
        self,
        client: ChatClient,
        conn: sqlite3.Connection,
        on_tool_call: Callable[[str, dict], None] | None = None,
        on_tool_result: Callable[[str, dict], None] | None = None,
    ):
        self.client = client
        self.conn = conn
        self.on_tool_call = on_tool_call
        self.on_tool_result = on_tool_result
        self.messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
        self.tool_log: list[dict] = []  # (eval harness introspects this)

    def reset(self) -> None:
        self.messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        self.tool_log = []

    def run_turn(self, user_text: str) -> str:
        self.messages.append({"role": "user", "content": user_text})
        for _ in range(MAX_TOOL_ROUNDS):
            message = self.client.complete(self.messages, openai_tool_specs())
            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                content = message.get("content") or ""
                self.messages.append({"role": "assistant", "content": content})
                return content

            self.messages.append(
                {
                    "role": "assistant",
                    "content": message.get("content") or "",
                    "tool_calls": tool_calls,
                }
            )
            for call in tool_calls:
                name = call["function"]["name"]
                try:
                    args = json.loads(call["function"].get("arguments") or "{}")
                except json.JSONDecodeError as e:
                    result = {"error": f"Arguments were not valid JSON: {e}"}
                    args = {}
                else:
                    if self.on_tool_call:
                        self.on_tool_call(name, args)
                    result = dispatch(self.conn, name, args)
                if self.on_tool_result:
                    self.on_tool_result(name, result)
                self.tool_log.append({"tool": name, "arguments": args, "result": result})
                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id", name),
                        "content": json.dumps(result),
                    }
                )
        return "I hit the tool-call limit for a single request — please break the task into smaller steps."

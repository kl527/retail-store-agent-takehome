"""The tool-calling loop, exercised with a stub LLM client (no API)."""

import json

from store_agent.agent import Agent


class StubClient:
    """Plays back scripted assistant messages."""

    model = "stub"

    def __init__(self, messages):
        self._script = list(messages)
        self.seen: list[list[dict]] = []

    def complete(self, messages, tools):
        self.seen.append([dict(m) for m in messages])
        return self._script.pop(0)


def tool_call(call_id, name, arguments):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}


def test_loop_dispatches_tools_and_returns_final_text(conn):
    client = StubClient(
        [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    tool_call(
                        "call_1",
                        "ring_up_sale",
                        {"items": [{"sku": "TOTE", "quantity": 1}], "date": "2026-06-19"},
                    )
                ],
            },
            {"role": "assistant", "content": "Done — order O-1016, total $18.00."},
        ]
    )
    agent = Agent(client=client, conn=conn)
    reply = agent.run_turn("sell a tote")

    assert reply == "Done — order O-1016, total $18.00."
    # The sale actually happened.
    assert conn.execute("SELECT on_hand_qty FROM inventory WHERE sku='TOTE'").fetchone()[0] == 3
    # The tool result went back to the model with the matching call id.
    final_request = client.seen[-1]
    tool_msg = final_request[-1]
    assert tool_msg["role"] == "tool" and tool_msg["tool_call_id"] == "call_1"
    assert json.loads(tool_msg["content"])["order_id"] == "O-1016"
    # And the log the eval harness reads recorded it.
    assert agent.tool_log[0]["tool"] == "ring_up_sale"


def test_malformed_tool_arguments_become_an_error_result(conn):
    client = StubClient(
        [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "c1", "type": "function", "function": {"name": "get_order", "arguments": "{not json"}}
                ],
            },
            {"role": "assistant", "content": "Sorry, I hit an error."},
        ]
    )
    agent = Agent(client=client, conn=conn)
    reply = agent.run_turn("look up O-1006")
    assert reply == "Sorry, I hit an error."
    tool_msg = client.seen[-1][-1]
    assert "error" in json.loads(tool_msg["content"])


def test_memory_spans_turns(conn):
    client = StubClient(
        [
            {"role": "assistant", "content": "It was $54.00."},
            {"role": "assistant", "content": "Refunded."},
        ]
    )
    agent = Agent(client=client, conn=conn)
    agent.run_turn("what did the hoodie on O-1006 cost?")
    agent.run_turn("now refund that")
    # Second request still contains the first exchange.
    roles = [(m["role"], m.get("content")) for m in client.seen[-1]]
    assert ("user", "what did the hoodie on O-1006 cost?") in roles
    assert ("assistant", "It was $54.00.") in roles

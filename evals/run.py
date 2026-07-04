"""Eval harness: run scripted conversations against the live agent and check
the resulting database state and replies.

    uv run python evals/run.py            # all scenarios
    uv run python evals/run.py --only return -v

Each scenario gets a fresh store and a fresh session. Checks are deterministic
(SQL state assertions, substring checks, tool-call assertions) — no LLM judge.
Results land in evals/results/scorecard.md and results.json.

Requires Cloudflare credentials in .env (see .env.example); exits cleanly
without them since this calls the real model.
"""

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import yaml
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from store_agent import db  # noqa: E402
from store_agent.agent import Agent  # noqa: E402
from store_agent.config import ConfigError, llm_config  # noqa: E402
from store_agent.llm import ChatClient, LLMError  # noqa: E402

ROOT = Path(__file__).resolve().parent
console = Console()


def check_turn(conn, reply: str, turn_tools: list[dict], expect: dict) -> list[dict]:
    checks = []
    for item in expect.get("sql", []):
        row = conn.execute(item["query"]).fetchone()
        actual = row[0] if row else None
        expected = item["equals"]
        try:
            passed = float(actual) == float(expected)
        except (TypeError, ValueError):
            passed = str(actual) == str(expected)
        checks.append(
            {
                "type": "sql",
                "detail": f"{item['query']} == {expected!r} (got {actual!r})",
                "passed": passed,
            }
        )
    # Normalize thousands separators so "$1,732.20" matches "1732.20".
    normalized_reply = reply.lower().replace(",", "")
    for needle in expect.get("reply_contains", []):
        checks.append(
            {
                "type": "reply_contains",
                "detail": repr(needle),
                "passed": str(needle).lower().replace(",", "") in normalized_reply,
            }
        )
    called = {t["tool"] for t in turn_tools}
    for name in expect.get("tool_called", []):
        checks.append({"type": "tool_called", "detail": name, "passed": name in called})
    for name in expect.get("tool_not_called", []):
        checks.append({"type": "tool_not_called", "detail": name, "passed": name not in called})
    return checks


def run_scenario(scenario: dict, settings: dict, verbose: bool) -> dict:
    conn = db.fresh_store()
    client = ChatClient(settings["base_url"], settings["api_key"], settings["model"])
    agent = Agent(client=client, conn=conn)
    turns_out = []
    for turn in scenario["turns"]:
        before = len(agent.tool_log)
        try:
            reply = agent.run_turn(turn["user"])
        except LLMError as e:
            reply = f"<LLM error: {e}>"
        turn_tools = agent.tool_log[before:]
        checks = check_turn(conn, reply, turn_tools, turn.get("expect", {}))
        turns_out.append(
            {
                "user": turn["user"],
                "reply": reply,
                "tools": [{"tool": t["tool"], "arguments": t["arguments"]} for t in turn_tools],
                "checks": checks,
                "passed": all(c["passed"] for c in checks),
            }
        )
        if verbose:
            console.print(f"[bold]user:[/bold] {turn['user']}")
            for t in turn_tools:
                console.print(f"  [dim]→ {t['tool']}({json.dumps(t['arguments'])})[/dim]")
            console.print(f"[bold]agent:[/bold] {reply}\n")
    conn.close()
    return {
        "scenario": scenario["name"],
        "description": scenario.get("description", ""),
        "turns": turns_out,
        "passed": all(t["passed"] for t in turns_out),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", help="run scenarios whose name contains this substring")
    parser.add_argument("--model", help="override the model id")
    parser.add_argument("--workers", type=int, default=6, help="concurrent scenarios (default 6)")
    parser.add_argument("-v", "--verbose", action="store_true", help="print transcripts (forces --workers 1)")
    args = parser.parse_args()

    load_dotenv()
    try:
        settings = llm_config()
    except ConfigError as e:
        console.print(f"[red]{e}[/red]")
        return 2
    if settings is None:
        console.print("[red]No LLM credentials — copy .env.example to .env first.[/red]")
        return 2
    if args.model:
        settings["model"] = args.model

    scenarios = []
    for path in sorted((ROOT / "scenarios").glob("*.yaml")):
        scenario = yaml.safe_load(path.read_text())
        if args.only and args.only not in scenario["name"]:
            continue
        scenarios.append(scenario)
    if not scenarios:
        console.print("[red]No scenarios matched.[/red]")
        return 2

    # Scenarios are independent (each gets a fresh store + session), so they
    # run concurrently; verbose stays sequential to keep transcripts readable.
    workers = 1 if args.verbose else max(1, args.workers)
    results = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(run_scenario, scenario, settings, args.verbose): scenario["name"]
            for scenario in scenarios
        }
        for future in as_completed(futures):
            result = future.result()
            status = "[green]PASS[/green]" if result["passed"] else "[red]FAIL[/red]"
            console.print(f"{status} {result['scenario']}")
            results.append(result)
    results.sort(key=lambda r: r["scenario"])

    table = Table(title=f"Eval scorecard — model {settings['model']}")
    table.add_column("scenario")
    table.add_column("result")
    table.add_column("failed checks")
    passed_count = 0
    md = [f"# Eval scorecard\n\nmodel: `{settings['model']}`\n", "| scenario | result | failed checks |", "|---|---|---|"]
    for r in results:
        failures = [
            f"turn {i + 1}: {c['type']} {c['detail']}"
            for i, t in enumerate(r["turns"])
            for c in t["checks"]
            if not c["passed"]
        ]
        status = "[green]PASS[/green]" if r["passed"] else "[red]FAIL[/red]"
        table.add_row(r["scenario"], status, "; ".join(failures) or "—")
        md.append(
            f"| {r['scenario']} | {'PASS' if r['passed'] else 'FAIL'} | {'; '.join(failures) or '—'} |"
        )
        passed_count += r["passed"]
    console.print(table)
    summary = f"{passed_count}/{len(results)} scenarios passed"
    console.print(f"[bold]{summary}[/bold]")
    md.append(f"\n**{summary}**")

    out = ROOT / "results"
    out.mkdir(exist_ok=True)
    (out / "scorecard.md").write_text("\n".join(md) + "\n")
    (out / "results.json").write_text(json.dumps(results, indent=2))
    console.print(f"[dim]wrote {out / 'scorecard.md'} and results.json[/dim]")
    return 0 if passed_count == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())

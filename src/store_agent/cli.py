"""acceptDaniel — the interactive store agent REPL."""

import sys

from dotenv import load_dotenv
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from . import db
from .agent import Agent
from .config import TODAY, ConfigError, llm_config
from .llm import ChatClient, LLMError

console = Console()

HELP = """\
Talk to the store agent in plain English. It remembers the conversation, so
follow-ups like "now refund that" work.

Commands:  /reset  start a fresh session (store data reloads from data/)
           /help   show this message
           /quit   exit (Ctrl-D works too)
"""


def _tool_trace(name: str, args: dict) -> None:
    rendered = ", ".join(f"{k}={v!r}" for k, v in args.items())
    console.print(f"  [dim]→ {name}({rendered})[/dim]")


def _tool_result_trace(name: str, result: dict) -> None:
    if isinstance(result, dict) and "error" in result:
        console.print(f"  [dim red]✗ {result['error']}[/dim red]")


def _build_agent() -> tuple[Agent, dict]:
    try:
        settings = llm_config()
    except ConfigError as e:
        console.print(Panel(str(e), title="setup needed", border_style="red"))
        sys.exit(1)
    if settings is None:
        console.print(
            Panel(
                "No LLM credentials found. Copy [bold].env.example[/bold] to "
                "[bold].env[/bold] and set any one of:\n\n"
                "  • OPENAI_API_KEY\n"
                "  • ANTHROPIC_API_KEY\n"
                "  • CLOUDFLARE_ACCOUNT_ID + CLOUDFLARE_API_TOKEN\n"
                "  • LLM_BASE_URL + LLM_API_KEY + LLM_MODEL (any OpenAI-compatible endpoint)",
                title="setup needed",
                border_style="red",
            )
        )
        sys.exit(1)
    client = ChatClient(settings["base_url"], settings["api_key"], settings["model"])
    conn = db.fresh_store()
    agent = Agent(conn=conn, client=client, on_tool_call=_tool_trace, on_tool_result=_tool_result_trace)
    return agent, settings


def main() -> None:
    load_dotenv()
    agent, settings = _build_agent()
    console.print(
        Panel(
            f"[bold]Retail store agent[/bold] · today is {TODAY} · "
            f"{settings['provider']} · {settings['model']}\n"
            "Type an instruction ([dim]/help for commands[/dim])",
            border_style="cyan",
        )
    )
    while True:
        try:
            text = console.input("[bold cyan]you ›[/bold cyan] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\nbye")
            return
        if not text:
            continue
        if text in ("/quit", "/exit"):
            console.print("bye")
            return
        if text == "/help":
            console.print(HELP)
            continue
        if text == "/reset":
            agent.conn.close()
            agent, settings = _build_agent()
            console.print("[green]session reset — store reloaded from data/[/green]")
            continue
        try:
            with console.status("[dim]thinking…[/dim]"):
                reply = agent.run_turn(text)
        except LLMError as e:
            console.print(f"[red]LLM error:[/red] {e}")
            continue
        console.print(Panel(Markdown(reply or "(empty reply)"), border_style="green"))


if __name__ == "__main__":
    main()

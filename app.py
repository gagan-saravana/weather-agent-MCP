import os
import sys
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # must run before the Agent is created

from nicegui import app, ui
from pydantic_ai import Agent
from pydantic_ai.messages import FunctionToolCallEvent, FunctionToolResultEvent

from fastmcp.client.transports import StdioTransport
from pydantic_ai.mcp import MCPToolset

# ---------------------------------------------------------------- agent ----

transport = StdioTransport(
    command=sys.executable,
    args=[str(Path(__file__).parent / "server.py")],
)
weather_server = MCPToolset(transport)

agent = Agent(
    os.getenv("GEMINI_MODEL", "google:gemini-3.5-flash-lite"),
    toolsets=[weather_server],
    instructions=(
        "You are a concise weather assistant. Use the weather tools for any "
        "forecast, history, or rain question; never guess weather data. "
        "Temperatures are in Fahrenheit. If a location is ambiguous, ask."
    ),
)


@agent.instructions
def current_date() -> str:
    return f"Today's date is {date.today().isoformat()}."


async def _start() -> None:
    await agent.__aenter__()  # launches the MCP subprocess once


async def _stop() -> None:
    await agent.__aexit__(None, None, None)


app.on_startup(_start)
app.on_shutdown(_stop)

# ------------------------------------------------------------------ ui -----

CSS = """
body { background: #212121; }
.user-bubble { background:#2f2f2f; color:#ececec; padding:10px 18px;
               border-radius:24px; max-width:80%; white-space:pre-wrap; }
.step { background:#2a2a2a; border-radius:12px; color:#b4b4b4; font-size:13px; }
.step-result { font-family:monospace; font-size:12px; white-space:pre-wrap;
               color:#9a9a9a; padding:0 16px 12px; }
.chip { background:#2a2a2a; color:#d0d0d0; border-radius:14px; padding:6px 14px; }
.chip:hover { background:#333; }
"""


def sample_questions() -> list[str]:
    soon = (date.today() + timedelta(days=5)).isoformat()
    later = (date.today() + timedelta(days=10)).isoformat()
    return [
        "What's the 5-day forecast for Denver?",
        "What was the weather in Washington on 2023-07-04?",
        f"What's the chance of rain in London on {soon}?",
        f"I'm planning an outdoor wedding in Seattle on {later}. Should I keep the date?",
        "Compare the 3-day forecast for Tokyo and Mumbai.",
    ]


def scroll_down() -> None:
    ui.run_javascript("window.scrollTo({top: document.body.scrollHeight})")


@ui.page("/")
def index() -> None:
    ui.add_css(CSS)
    history: list = []
    busy = {"on": False}

    # ---- messages area
    with ui.column().classes("w-full max-w-3xl mx-auto gap-4").style("padding-bottom:120px"):
        welcome = ui.column().classes("w-full items-center gap-3").style("margin-top:15vh")
        with welcome:
            ui.label("Weather Chat").classes("text-3xl font-semibold text-white")
            ui.label("Forecasts, history, and rain odds, powered by an MCP server.").classes("text-grey-5")
            with ui.column().classes("w-full gap-2").style("margin-top:24px"):
                for q in sample_questions():
                    ui.button(q, on_click=lambda q=q: ask(q)).props("flat no-caps align=left").classes("chip w-full")
        log = ui.column().classes("w-full gap-4")

    # ---- input bar
    with ui.footer().classes("bg-transparent items-center").style("padding:12px 16px 20px"):
        with ui.column().classes("w-full max-w-3xl mx-auto"):
            box = (
                ui.input(placeholder="Ask about the weather...")
                .props("rounded outlined dark color=grey-7")
                .classes("w-full")
                .style("background:#2f2f2f; border-radius:28px")
            )
            with box.add_slot("append"):
                ui.button(icon="arrow_upward", on_click=lambda: ask(box.value)).props("round dense flat color=white")

    async def ask(text: str) -> None:
        text = (text or "").strip()
        if not text or busy["on"]:
            return
        busy["on"] = True
        box.value = ""
        box.disable()
        welcome.visible = False

        with log:
            with ui.row().classes("w-full justify-end"):
                ui.label(text).classes("user-bubble")
            bot = ui.column().classes("w-full gap-2")
        with bot:
            steps = ui.column().classes("w-full gap-1")
            answer = ui.column().classes("w-full")
        with answer:
            ui.spinner("dots", color="grey")
        scroll_down()

        calls: dict = {}  # tool_call_id -> expansion element
        try:
            async with agent.iter(text, message_history=history) as run:
                async for node in run:
                    if not Agent.is_call_tools_node(node):
                        continue
                    async with node.stream(run.ctx) as stream:
                        async for event in stream:
                            if isinstance(event, FunctionToolCallEvent):
                                args = event.part.args_as_dict()
                                label = f"{event.part.tool_name}(" + ", ".join(f"{k}={v!r}" for k, v in args.items()) + ")"
                                with steps:
                                    calls[event.part.tool_call_id] = (
                                        ui.expansion(label, icon="sync").classes("step w-full").props("dense")
                                    )
                                scroll_down()
                            elif isinstance(event, FunctionToolResultEvent):
                                # newer pydantic-ai: event.part; older: event.result
                                part = getattr(event, "part", None) or getattr(event, "result", None)
                                exp = calls.get(getattr(part, "tool_call_id", None))
                                if exp:
                                    with exp:
                                        ui.label(str(getattr(part, "content", part))).classes("step-result")
                                    exp.props("icon=check_circle")
            history[:] = run.result.all_messages()
            reply = run.result.output
        except Exception as exc:  # rate limit, network, bad key...
            reply = f"Something went wrong: {exc}"

        answer.clear()
        with answer:
            ui.markdown(reply).classes("text-white")
        scroll_down()
        busy["on"] = False
        box.enable()
        box.run_method("focus")

    box.on("keydown.enter", lambda: ask(box.value))


ui.run(title="Weather Chat", dark=True, reload=False)
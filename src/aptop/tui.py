"""Responsive Textual interface for the local aptop collector."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from rich.markup import escape
from rich.table import Table
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Grid
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Footer, Sparkline, Static

from aptop import __version__
from aptop.collector import Collector
from aptop.formatting import History, byte_size, mib, number, percent, seconds, text

COLORS = {
    "cyan": "#34d8ff",
    "green": "#45f5a1",
    "lime": "#b7f34a",
    "amber": "#ffc857",
    "red": "#ff5d78",
    "violet": "#a78bfa",
    "muted": "#728096",
    "text": "#d7e0ea",
}


def _markup(value: object) -> str:
    return escape(text(value))


def _age(value: object) -> str:
    if not isinstance(value, str) or not value:
        return "NO SAMPLE TIME"
    try:
        checked = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
        age = max(0.0, (datetime.now(UTC) - checked).total_seconds())
        return f"{age:.1f}s AGO"
    except ValueError:
        return "SAMPLE TIME UNKNOWN"


def _card_state(card: dict) -> tuple[str, str, str]:
    if card.get("busy"):
        return "WORKING", COLORS["green"], "running"
    if card.get("present") is False:
        return "MISSING", COLORS["red"], "missing"
    return "IDLE", COLORS["cyan"], "idle"


class TopBar(Container):
    def compose(self) -> ComposeResult:
        yield Static(
            f"[bold {COLORS['cyan']}]◆ aptop[/] [dim]v{__version__}[/]", id="brand"
        )
        yield Static("[bold]APEX TOP[/] [dim]// LOCAL READ-ONLY MONITOR[/]", id="app-title")
        yield Static("[dim]STARTING[/]", id="sample-state")

    def set_sample(
        self,
        checked_at: object,
        *,
        error: str | None,
        paused: bool,
        show_history: bool,
    ) -> None:
        if error and checked_at:
            status = f"[bold {COLORS['amber']}]● STALE[/]  [dim]{_age(checked_at)}[/]"
        elif error:
            status = f"[bold {COLORS['red']}]● NO DATA[/]"
        elif paused:
            status = f"[bold {COLORS['amber']}]Ⅱ PAUSED[/]  [dim]{_age(checked_at)}[/]"
        else:
            status = f"[bold {COLORS['green']}]● LIVE[/]  [dim]{_age(checked_at)}[/]"
        if show_history:
            status = f"[bold {COLORS['cyan']}]LAST RUN[/]  {status}"
        self.query_one("#sample-state", Static).update(status)


class ChartBox(Container):
    def __init__(self, title: str, graph_id: str, *, classes: str = "") -> None:
        super().__init__(classes=f"chart-box {classes}".strip())
        self.chart_title = title
        self.graph_id = graph_id

    def compose(self) -> ComposeResult:
        yield Static(self.chart_title, classes="chart-title")
        yield Sparkline([], id=self.graph_id)

    def update_chart(self, values, title: str | None = None) -> None:
        if title is not None:
            self.query_one(".chart-title", Static).update(title)
        self.query_one(Sparkline).data = list(values)


class HostPanel(Container):
    def compose(self) -> ComposeResult:
        yield Static("HOST  //  WAITING", classes="panel-title", id="host-title")
        with Grid(id="host-body"):
            yield Static("", id="host-metrics", classes="metric-block")
            yield ChartBox("CPU HISTORY", "cpu-graph", classes="cpu-chart")
            yield ChartBox("RAM HISTORY", "ram-graph", classes="ram-chart")
            yield Static("", id="host-workload", classes="metric-block right-metrics")

    def update_host(self, snapshot: dict, history: History) -> None:
        host = snapshot.get("host") or {}
        cpu = host.get("cpu") or {}
        memory = host.get("memory") or {}
        load = host.get("load_average") or {}
        workload = host.get("workload_process") or {}
        self.query_one("#host-title", Static).update(
            f"[bold {COLORS['cyan']}]HOST[/]  [bold]{_markup(host.get('hostname'))}[/]  "
            f"[dim]// {_markup(cpu.get('logical_processors'))} LOGICAL CPUs[/]"
        )
        self.query_one("#host-metrics", Static).update(
            f"[dim]HOST CPU[/]\n[bold {COLORS['lime']}]"
            f"{percent(cpu.get('utilization_percent'))}[/]\n"
            f"[dim]LOAD 1 / 5 / 15[/]\n"
            f"[bold]{_markup(load.get('one_minute'))}[/]  "
            f"{_markup(load.get('five_minutes'))}  {_markup(load.get('fifteen_minutes'))}"
        )
        process_count = workload.get("process_count") or 0
        process_summary = (
            f"[bold]{byte_size(workload.get('rss_bytes'))}[/]  "
            f"[dim]{_markup(process_count)} proc · "
            f"{_markup(workload.get('thread_count') or 0)} threads[/]"
            if process_count
            else "[dim]IDLE[/]"
        )
        self.query_one("#host-workload", Static).update(
            f"[dim]HOST RAM[/]\n[bold {COLORS['violet']}]"
            f"{percent(memory.get('used_percent'))}[/]  "
            f"[dim]{byte_size(memory.get('used_bytes'))} / "
            f"{byte_size(memory.get('total_bytes'))}[/]\n"
            f"[dim]APEX JOB RSS[/]\n{process_summary}"
        )
        self.query_one(".cpu-chart", ChartBox).update_chart(
            history.cpu,
            f"CPU HISTORY  //  [bold {COLORS['lime']}]"
            f"{percent(cpu.get('utilization_percent'))}[/]",
        )
        self.query_one(".ram-chart", ChartBox).update_chart(
            history.memory,
            f"RAM HISTORY  //  [bold {COLORS['violet']}]"
            f"{percent(memory.get('used_percent'))}[/]",
        )


class MemoryMap(Widget):
    COMPONENTS = (
        ("weight_mib", "WEIGHTS", COLORS["cyan"]),
        ("kv_cache_mib", "KV", COLORS["green"]),
        ("tensor_scratch_mib", "TENSORS", COLORS["violet"]),
        ("program_mib", "PROGRAM", COLORS["amber"]),
        ("unreported_or_reserved_mib", "UNREPORTED", "#354253"),
    )

    def __init__(self, *, id: str) -> None:
        super().__init__(id=id)
        self.memory: dict = {}
        self.live = False

    def set_memory(self, memory: dict, *, live: bool) -> None:
        self.memory = memory or {}
        self.live = live
        self.refresh()

    def render(self) -> Text:
        mapped = number(self.memory.get("known_mapped_percent"))
        capacity = number(self.memory.get("capacity_mib"))
        output = Text()
        label = "LIVE MEMORY" if self.live else "LAST RUN MEMORY"
        output.append(label, style=f"bold {COLORS['green'] if self.live else COLORS['cyan']}")
        output.append("\n")
        if mapped is None or not capacity:
            output.append("N/A", style=f"bold {COLORS['amber']}")
            output.append("  runtime mapping unavailable", style=COLORS["muted"])
            return output
        output.append(f"{mapped:5.1f}%", style=f"bold {COLORS['text']}")
        output.append(
            f"   {mib(self.memory.get('known_mapped_mib'))} / {mib(capacity)}\n",
            style=COLORS["muted"],
        )
        cells = max(8, self.size.width - 2)
        used = 0
        for key, _name, color in self.COMPONENTS:
            value = number(self.memory.get(key))
            if value is None or value <= 0:
                continue
            count = min(cells - used, max(1, round(value * cells / capacity)))
            if count > 0:
                output.append("━" * count, style=f"bold {color}")
                used += count
        if used < cells:
            output.append("━" * (cells - used), style="#202a36")
        output.append("\n")
        for index, (key, name, color) in enumerate(self.COMPONENTS):
            if index:
                output.append("  ")
            output.append("■ ", style=color)
            output.append(f"{name} {mib(self.memory.get(key))}", style=COLORS["muted"])
        return output


class ApexCard(Container):
    def __init__(self, index: int, device: str, *, id: str) -> None:
        super().__init__(id=id, classes="panel apex-card idle")
        self.index = index
        self.device = device

    @property
    def prefix(self) -> str:
        return f"card-{self.index}"

    def compose(self) -> ComposeResult:
        yield Static(
            f"APEX {self.index + 1}  //  {self.device.upper()}",
            classes="panel-title",
            id=f"{self.prefix}-title",
        )
        with Grid(classes="card-metrics"):
            yield Static("[dim]STATE[/]\n[bold]UNKNOWN[/]", id=f"{self.prefix}-state")
            yield Static("[dim]MEMORY[/]\n[bold]—[/]", id=f"{self.prefix}-mapped")
            yield Static("[dim]FPGA UTIL[/]\n[bold]—[/]", id=f"{self.prefix}-util")
        yield MemoryMap(id=f"{self.prefix}-memory")
        yield Static(
            "[dim]MODEL[/] —\n[dim]PHASE[/] —",
            classes="workload-line",
            id=f"{self.prefix}-workload",
        )
        yield ChartBox(
            "CARD ACTIVITY  //  PROCESS OWNERSHIP",
            f"{self.prefix}-residency",
            classes="residency-chart",
        )
        yield Static("", classes="card-detail", id=f"{self.prefix}-detail")

    def update_card(self, card: dict, history: History, *, show_history: bool) -> None:
        state, color, css_state = _card_state(card)
        memory = card.get("runtime_memory") or {}
        memory_live = bool(card.get("memory_live"))
        show_memory = memory_live or (show_history and bool(memory))
        hidden = "" if show_memory else " history-hidden"
        self.set_classes(f"panel apex-card {css_state}{hidden}")
        self.query_one(f"#{self.prefix}-title", Static).update(
            f"[bold {COLORS['cyan']}]{_markup(card.get('label')).upper()}[/]  "
            f"[bold]{_markup(card.get('device')).upper()}[/]  "
            f"[bold {color}]● {state}[/]"
        )
        self.query_one(f"#{self.prefix}-state", Static).update(
            f"[dim]STATE[/]\n[bold {color}]{state}[/]"
        )
        if show_memory:
            map_label = "LIVE MEMORY" if memory_live else "LAST RUN"
            map_color = COLORS["green"] if memory_live else COLORS["cyan"]
            mapped_line = (
                f"[dim]{map_label}[/]\n[bold {map_color}]"
                f"{percent(memory.get('known_mapped_percent'))}[/]  "
                f"[dim]/ {mib(memory.get('capacity_mib'))}[/]"
            )
        else:
            mapped_line = "[dim]MEMORY[/]\n[bold]—[/]  [dim]unavailable[/]"
        self.query_one(f"#{self.prefix}-mapped", Static).update(mapped_line)
        memory_widget = self.query_one(f"#{self.prefix}-memory", MemoryMap)
        memory_widget.set_memory(memory, live=memory_live)
        memory_widget.display = show_memory

        activity = card.get("activity") or {}
        start = activity.get("layer_start")
        end = activity.get("layer_end_exclusive")
        layers = f"{start}:{end}" if start is not None and end is not None else "—"
        if card.get("busy") and activity:
            workload_line = (
                f"[dim]MODEL[/]  [bold]{_markup(activity.get('model'))}[/]  "
                f"[dim]LAYERS[/]  [bold]{layers}[/]\n"
                f"[dim]PHASE[/]  [bold {color}]{_markup(activity.get('phase'))}[/]  "
                f"[dim]ELAPSED[/] {seconds(activity.get('elapsed_seconds'))}"
            )
        elif show_history and activity:
            workload_line = (
                f"[dim]LAST RUN[/]  [bold]{_markup(activity.get('model'))}[/]  "
                f"[dim]LAYERS[/]  [bold]{layers}[/]\n"
                f"[dim]PHASE[/]  {_markup(activity.get('phase'))}"
            )
        else:
            workload_line = "[dim]MODEL[/]  —  [dim]LAYERS[/]  —\n[dim]PHASE[/]  —"
        self.query_one(f"#{self.prefix}-workload", Static).update(workload_line)
        samples = history.busy.get(card.get("device") or "unknown", ())
        self.query_one(".residency-chart", ChartBox).update_chart(
            samples,
            "CARD ACTIVITY  //  [dim]PROCESS OWNERSHIP · 120 SAMPLES[/]",
        )
        self.query_one(f"#{self.prefix}-detail", Static).update(
            f"[dim]NODES[/] · {_markup(card.get('device_nodes'))}   "
            "[dim]FPGA UTIL[/] · unavailable"
        )


class ProcessPanel(Container):
    def compose(self) -> ComposeResult:
        yield Static("ACTIVE WORK", classes="panel-title", id="process-title")
        yield Static("[dim]No active Apex workload processes.[/]", id="process-table")

    def update_processes(self, snapshot: dict) -> None:
        records = []
        seen: set[object] = set()
        for card in snapshot.get("cards") or []:
            for item in card.get("processes") or []:
                if not isinstance(item, dict) or item.get("pid") in seen:
                    continue
                seen.add(item.get("pid"))
                records.append((item.get("pid"), card.get("device"), item.get("process")))
        if not records:
            self.query_one("#process-title", Static).update("ACTIVE WORK  //  0 PROCESSES")
            self.query_one("#process-table", Static).update(
                "[dim]No process currently holds a monitored XDMA device.[/]"
            )
            return
        self.query_one("#process-title", Static).update(
            f"ACTIVE WORK  //  {len(records)} PROCESS{'ES' if len(records) != 1 else ''}"
        )
        table = Table.grid(expand=True)
        table.add_column("PID", width=10, style=COLORS["muted"])
        table.add_column("CARD", width=12, style=COLORS["cyan"])
        table.add_column("PROCESS", ratio=1)
        for pid, device, process in records[:8]:
            table.add_row(text(pid), text(device), text(process))
        self.query_one("#process-table", Static).update(table)


class NoCardsPanel(Container):
    def compose(self) -> ComposeResult:
        yield Static("NO XDMA CARDS DETECTED", classes="panel-title")
        yield Static(
            "[bold]No /dev/xdma* device nodes were found.[/]\n"
            "[dim]Load the XDMA driver, or pass --device xdma0 to display an expected card.[/]",
            id="no-cards-message",
        )


class HelpScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape", "dismiss", "Close"), Binding("?", "dismiss", "Close")]

    def __init__(self, state_path: Path, boundary: str | None) -> None:
        super().__init__()
        self.state_path = state_path
        self.boundary = boundary or "No collector boundary is available."

    def compose(self) -> ComposeResult:
        with Container(id="help-dialog"):
            yield Static(
                "[bold]aptop data sources[/]\n[dim]Esc or ? to close[/]",
                classes="help-title",
            )
            yield Static(
                "[bold]Host[/]  Linux procfs CPU, RAM, load, process tree\n"
                "[bold]Cards[/]  /dev/xdma* presence and read-only /proc fd ownership\n"
                "[bold]Runtime[/]  optional owner-written JSON state\n\n"
                f"[bold]State file[/]  {_markup(self.state_path)}\n\n"
                f"[dim]{_markup(self.boundary)}[/]"
            )

    def action_dismiss(self) -> None:
        self.dismiss()


class AptopApp(App[None]):
    CSS_PATH = Path(__file__).with_name("aptop.tcss")
    TITLE = "aptop"
    SUB_TITLE = "Local Apex monitor"
    ENABLE_COMMAND_PALETTE = False
    AUTO_FOCUS = None
    HORIZONTAL_BREAKPOINTS = [(0, "-narrow"), (104, "-wide"), (168, "-ultra")]
    VERTICAL_BREAKPOINTS = [(0, "-short"), (36, "-tall")]
    BINDINGS = [
        Binding("q", "quit", "Quit", priority=True),
        Binding("p", "toggle_pause", "Pause"),
        Binding("space", "toggle_pause", "Pause", show=False),
        Binding("r", "refresh", "Refresh"),
        Binding("l", "toggle_history", "Last run"),
        Binding("?", "help", "Help"),
    ]

    def __init__(self, collector: Collector, initial_snapshot: dict, *, interval: float) -> None:
        super().__init__()
        self.collector = collector
        self.snapshot = initial_snapshot
        self.devices = [card.get("device") for card in initial_snapshot.get("cards") or []]
        self.interval = interval
        self.history = History()
        self.history.record(initial_snapshot)
        self.error: str | None = None
        self.paused = False
        self.show_history = False
        self.poll_timer: Timer | None = None

    def compose(self) -> ComposeResult:
        yield TopBar(id="topbar")
        with Grid(id="dashboard"):
            yield HostPanel(id="host-panel", classes="panel")
            card_class = "one-card" if len(self.devices) == 1 else "two-card"
            with Grid(id="cards-grid", classes=card_class):
                if self.devices:
                    for index, device in enumerate(self.devices):
                        yield ApexCard(index, device, id=f"apex-card-{index}")
                else:
                    yield NoCardsPanel(id="no-cards", classes="panel")
            yield ProcessPanel(id="process-panel", classes="panel")
        yield Footer(compact=True)

    def on_mount(self) -> None:
        self._render_snapshot()
        self.poll_timer = self.set_interval(self.interval, self.refresh_snapshot)

    @work(thread=True, exclusive=True, group="snapshot")
    def refresh_snapshot(self) -> None:
        try:
            snapshot = self.collector.snapshot()
            self.call_from_thread(self._accept_sample, snapshot, None)
        except (OSError, ValueError) as exc:
            self.call_from_thread(self._accept_sample, None, str(exc))

    def _accept_sample(self, snapshot: dict | None, error: str | None) -> None:
        self.error = error
        if snapshot is not None:
            self.snapshot = snapshot
            self.history.record(snapshot)
        checked_at = self.snapshot.get("checked_at") if self.snapshot else None
        self.query_one(TopBar).set_sample(
            checked_at,
            error=error,
            paused=self.paused,
            show_history=self.show_history,
        )
        self._render_snapshot()

    def _render_snapshot(self) -> None:
        self.query_one(HostPanel).update_host(self.snapshot, self.history)
        cards_by_device = {
            card.get("device"): card for card in self.snapshot.get("cards") or []
        }
        for index, device in enumerate(self.devices):
            card = cards_by_device.get(device) or {
                "label": f"Apex {index + 1}",
                "device": device,
                "present": False,
            }
            self.query_one(f"#apex-card-{index}", ApexCard).update_card(
                card, self.history, show_history=self.show_history
            )
        self.query_one(ProcessPanel).update_processes(self.snapshot)
        self.query_one(TopBar).set_sample(
            self.snapshot.get("checked_at"),
            error=self.error,
            paused=self.paused,
            show_history=self.show_history,
        )

    def action_toggle_pause(self) -> None:
        self.paused = not self.paused
        if self.poll_timer is not None:
            self.poll_timer.pause() if self.paused else self.poll_timer.resume()
        self.query_one(TopBar).set_sample(
            self.snapshot.get("checked_at"),
            error=self.error,
            paused=self.paused,
            show_history=self.show_history,
        )

    def action_refresh(self) -> None:
        self.refresh_snapshot()

    def action_toggle_history(self) -> None:
        self.show_history = not self.show_history
        self._render_snapshot()

    def action_help(self) -> None:
        self.push_screen(
            HelpScreen(
                self.collector.state_path or Path(""),
                self.snapshot.get("measurement_boundary"),
            )
        )

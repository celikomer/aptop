"""Presentation helpers shared by the plain and full-screen interfaces."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

SPARKS = "▁▂▃▄▅▆▇█"


@dataclass
class History:
    cpu: deque[float] = field(default_factory=lambda: deque(maxlen=120))
    memory: deque[float] = field(default_factory=lambda: deque(maxlen=120))
    busy: dict[str, deque[float]] = field(default_factory=dict)

    def record(self, snapshot: dict) -> None:
        host = snapshot.get("host") or {}
        cpu = (host.get("cpu") or {}).get("utilization_percent")
        memory = (host.get("memory") or {}).get("used_percent")
        if number(cpu) is not None:
            self.cpu.append(float(cpu))
        if number(memory) is not None:
            self.memory.append(float(memory))
        for card in snapshot.get("cards") or []:
            device = card.get("device") or "unknown"
            samples = self.busy.setdefault(device, deque(maxlen=120))
            samples.append(100.0 if card.get("busy") else 0.0)


def number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def text(value: object, fallback: str = "—") -> str:
    if value is None or value == "":
        return fallback
    return str(value).replace("\n", " ").replace("\r", " ")


def percent(value: object, digits: int = 1) -> str:
    value_number = number(value)
    return "—" if value_number is None else f"{value_number:.{digits}f}%"


def byte_size(value: object) -> str:
    value_number = number(value)
    if value_number is None:
        return "—"
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    index = 0
    while abs(value_number) >= 1024 and index < len(units) - 1:
        value_number /= 1024
        index += 1
    precision = 0 if index == 0 else 1 if abs(value_number) < 100 else 0
    return f"{value_number:.{precision}f} {units[index]}"


def mib(value: object) -> str:
    value_number = number(value)
    if value_number is None:
        return "—"
    if abs(value_number) >= 1024:
        return f"{value_number / 1024:.2f} GiB"
    return f"{value_number:.1f} MiB"


def seconds(value: object) -> str:
    value_number = number(value)
    if value_number is None:
        return "—"
    if value_number < 60:
        return f"{value_number:.1f}s"
    minutes, remainder = divmod(int(value_number), 60)
    return f"{minutes}m{remainder:02d}s"


def sparkline(values, width: int) -> str:
    width = max(0, width)
    samples = list(values)[-width:]
    if not samples:
        return "·" * width
    output = []
    for value in samples:
        index = round(max(0.0, min(100.0, float(value))) * (len(SPARKS) - 1) / 100)
        output.append(SPARKS[index])
    return "·" * (width - len(output)) + "".join(output)


def render_plain(snapshot: dict, *, show_history: bool = False) -> str:
    """Render one log-friendly snapshot without terminal control sequences."""
    host = snapshot.get("host") or {}
    cpu = host.get("cpu") or {}
    ram = host.get("memory") or {}
    load = host.get("load_average") or {}
    lines = [
        f"aptop  host={text(host.get('hostname'))}  cards={len(snapshot.get('cards') or [])}",
        (
            f"CPU {percent(cpu.get('utilization_percent'))}  "
            f"RAM {percent(ram.get('used_percent'))} "
            f"({byte_size(ram.get('used_bytes'))}/{byte_size(ram.get('total_bytes'))})  "
            f"LOAD {text(load.get('one_minute'))} {text(load.get('five_minutes'))} "
            f"{text(load.get('fifteen_minutes'))}"
        ),
    ]
    cards = snapshot.get("cards") or []
    if not cards:
        lines.append("No XDMA cards detected.")
    for card in cards:
        state = (
            "working"
            if card.get("busy")
            else "missing"
            if card.get("present") is False
            else "idle"
        )
        lines.append(
            f"{text(card.get('label'))} {text(card.get('device')).upper()}  "
            f"{state}  nodes={text(card.get('device_nodes'), '0')}"
        )
        memory = card.get("runtime_memory") or {}
        if card.get("memory_live") or (show_history and memory):
            label = "live" if card.get("memory_live") else "last-run"
            lines.append(
                f"  {label} memory {percent(memory.get('known_mapped_percent'))} "
                f"({mib(memory.get('known_mapped_mib'))}/{mib(memory.get('capacity_mib'))})"
            )
        elif card.get("busy"):
            lines.append("  runtime memory unavailable (no workload state)")
        activity = card.get("activity") or {}
        if card.get("busy") and activity:
            layers = "—"
            if (
                activity.get("layer_start") is not None
                and activity.get("layer_end_exclusive") is not None
            ):
                layers = f"{activity['layer_start']}:{activity['layer_end_exclusive']}"
            lines.append(
                f"  model={text(activity.get('model'))} phase={text(activity.get('phase'))} "
                f"layers={layers} elapsed={seconds(activity.get('elapsed_seconds'))}"
            )
        for process in card.get("processes") or []:
            lines.append(
                f"  pid={text(process.get('pid'))} process={text(process.get('process'))}"
            )
    lines.append("FPGA utilization: unavailable unless a future runtime exposes a counter.")
    return "\n".join(lines)

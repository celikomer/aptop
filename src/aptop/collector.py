"""Read-only Linux host and XDMA process-ownership collector."""

from __future__ import annotations

import glob
import json
import os
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = "aptop/v1"
WORKLOAD_SCHEMA = "aptop-workload/v1"
DEVICE_NAME_RE = re.compile(r"^xdma(\d+)$")
DEVICE_NODE_RE = re.compile(r"^/dev/(xdma\d+)_")
MAX_STATE_BYTES = 1024 * 1024
FRESH_STATE_SECONDS = 5.0


def default_state_path() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        return Path(runtime) / "aptop-workload.json"
    uid = os.getuid()
    sudo_uid = os.environ.get("SUDO_UID")
    if os.geteuid() == 0 and sudo_uid and sudo_uid.isdecimal():
        uid = int(sudo_uid)
        original_runtime = Path("/run/user") / str(uid)
        if original_runtime.is_dir():
            return original_runtime / "aptop-workload.json"
    return Path("/tmp") / f"aptop-{uid}" / "workload.json"


def _read_text(path: str | Path) -> str:
    try:
        return Path(path).read_bytes().replace(b"\0", b" ").decode("utf-8", "replace").strip()
    except (OSError, ValueError):
        return ""


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def _elapsed(value: object) -> float | None:
    started = _timestamp(value)
    if started is None:
        return None
    return max(0.0, (datetime.now(UTC) - started).total_seconds())


def _pid_alive(value: object) -> bool:
    try:
        pid = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return pid > 0 and Path(f"/proc/{pid}").exists()


def _process_age(pid: int) -> float | None:
    try:
        uptime = float(_read_text("/proc/uptime").split()[0])
        fields = _read_text(f"/proc/{pid}/stat").split()
        ticks = os.sysconf(os.sysconf_names["SC_CLK_TCK"])
        return max(0.0, uptime - float(fields[21]) / ticks)
    except (OSError, ValueError, IndexError):
        return None


def _process_label(command: str) -> str:
    """Return operational identity without leaking arbitrary command arguments."""
    parts = command.split()
    if not parts:
        return "external process"
    executable = os.path.basename(parts[0])
    script = next(
        (os.path.basename(part) for part in parts[1:] if part.endswith((".py", ".sh"))),
        "",
    )
    return f"{executable} {script}".strip()


def _device_key(device: str) -> tuple[int, str]:
    match = DEVICE_NAME_RE.fullmatch(device)
    return (int(match.group(1)), device) if match else (10**9, device)


def _valid_device(value: object) -> str | None:
    if not isinstance(value, str) or DEVICE_NAME_RE.fullmatch(value) is None:
        return None
    return value


def discover_devices(
    explicit: tuple[str, ...] = (),
    *,
    globber: Callable[[str], list[str]] = glob.glob,
) -> list[str]:
    """Discover unique XDMA device prefixes or validate an explicit list."""
    if explicit:
        invalid = [value for value in explicit if _valid_device(value) is None]
        if invalid:
            raise ValueError(f"invalid XDMA device name: {invalid[0]}")
        return sorted(set(explicit), key=_device_key)
    devices = set()
    for path in globber("/dev/xdma*_*"):
        match = DEVICE_NODE_RE.match(path)
        if match:
            devices.add(match.group(1))
    return sorted(devices, key=_device_key)


def _status_fields(pid: int) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in _read_text(f"/proc/{pid}/status").splitlines():
        key, separator, raw = line.partition(":")
        if separator:
            fields[key] = raw.strip()
    return fields


def _status_int(fields: dict[str, str], key: str, multiplier: int = 1) -> int | None:
    try:
        return int(fields[key].split()[0]) * multiplier
    except (KeyError, ValueError, IndexError):
        return None


def _sanitize_memory(value: object) -> dict[str, float | str]:
    if not isinstance(value, dict):
        return {}
    numeric = (
        "capacity_mib",
        "weight_mib",
        "kv_cache_mib",
        "tensor_scratch_mib",
        "program_mib",
        "unreported_or_reserved_mib",
        "known_mapped_mib",
        "known_mapped_percent",
    )
    result: dict[str, float | str] = {}
    for key in numeric:
        number = _number(value.get(key))
        if number is None or number < 0:
            continue
        result[key] = min(number, 100.0) if key == "known_mapped_percent" else number
    for key in ("weight_high_water", "program_base"):
        raw = value.get(key)
        if isinstance(raw, str) and len(raw) <= 64:
            result[key] = raw
    return result


def _sanitize_card_state(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    device = _valid_device(value.get("device"))
    if device is None:
        return None
    result: dict[str, object] = {"device": device}
    for key in ("layer_start", "layer_end_exclusive"):
        number = value.get(key)
        if isinstance(number, int) and not isinstance(number, bool) and number >= 0:
            result[key] = number
    phase = value.get("phase")
    if isinstance(phase, str):
        result["phase"] = phase[:80]
    result["runtime_memory"] = _sanitize_memory(value.get("runtime_memory"))
    return result


def load_workload_state(path: Path) -> dict[str, object]:
    """Read, validate, and retire stale runtime integration state."""
    try:
        with path.open("rb") as source:
            payload = source.read(MAX_STATE_BYTES + 1)
    except OSError:
        return {}
    if len(payload) > MAX_STATE_BYTES:
        return {}
    try:
        raw = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    if not isinstance(raw, dict) or raw.get("schema") != WORKLOAD_SCHEMA:
        return {}
    cards = [
        card
        for value in raw.get("cards", [])
        if (card := _sanitize_card_state(value)) is not None
    ]
    active = raw.get("active") is True
    pids = [raw.get("observer_pid"), raw.get("child_pid")]
    declared_pids = [value for value in pids if value is not None]
    if active and declared_pids:
        active = any(_pid_alive(value) for value in declared_pids)
    elif active:
        updated = _timestamp(raw.get("updated_at"))
        active = (
            updated is not None
            and (datetime.now(UTC) - updated).total_seconds() <= FRESH_STATE_SECONDS
        )
    result: dict[str, object] = {
        "active": active,
        "cards": cards,
    }
    for key in (
        "model",
        "display_name",
        "phase",
        "started_at",
        "updated_at",
        "completed_at",
        "observer_pid",
        "child_pid",
        "exit_code",
    ):
        value = raw.get(key)
        if isinstance(value, (str, int, float)) and not isinstance(value, bool):
            result[key] = value
    return result


@dataclass
class Collector:
    """Stateful sampler; the CPU percentage needs two aggregate procfs readings."""

    devices: tuple[str, ...] = ()
    state_path: Path | None = None

    def __post_init__(self) -> None:
        self.state_path = self.state_path or default_state_path()
        self._cpu_lock = threading.Lock()
        self._cpu_previous: tuple[int, int] | None = None

    def detected_devices(self) -> list[str]:
        return discover_devices(self.devices)

    def _cpu_times(self) -> tuple[int, int] | None:
        lines = _read_text("/proc/stat").splitlines()
        if not lines or not lines[0].startswith("cpu "):
            return None
        try:
            values = [int(value) for value in lines[0].split()[1:]]
        except ValueError:
            return None
        if len(values) < 4:
            return None
        total = sum(values)
        idle = values[3] + (values[4] if len(values) > 4 else 0)
        return total, idle

    def _cpu_percent(self) -> float | None:
        current = self._cpu_times()
        if current is None:
            return None
        with self._cpu_lock:
            previous = self._cpu_previous
            self._cpu_previous = current
        if previous is None:
            return None
        total_delta = current[0] - previous[0]
        idle_delta = current[1] - previous[1]
        if total_delta <= 0:
            return None
        value = (total_delta - idle_delta) * 100 / total_delta
        return round(max(0.0, min(100.0, value)), 1)

    def _memory(self) -> dict[str, object]:
        values: dict[str, int] = {}
        for line in _read_text("/proc/meminfo").splitlines():
            key, separator, raw = line.partition(":")
            if not separator:
                continue
            try:
                values[key] = int(raw.strip().split()[0]) * 1024
            except (ValueError, IndexError):
                continue
        total = values.get("MemTotal")
        available = values.get("MemAvailable")
        used = total - available if total is not None and available is not None else None
        percent = used * 100 / total if used is not None and total else None
        return {
            "total_bytes": total,
            "available_bytes": available,
            "used_bytes": used,
            "used_percent": round(percent, 1) if percent is not None else None,
            "source": "procfs MemAvailable",
        }

    def _load_average(self) -> dict[str, float | None]:
        fields = _read_text("/proc/loadavg").split()
        try:
            values = [float(value) for value in fields[:3]]
        except ValueError:
            values = []
        return {
            "one_minute": values[0] if len(values) > 0 else None,
            "five_minutes": values[1] if len(values) > 1 else None,
            "fifteen_minutes": values[2] if len(values) > 2 else None,
        }

    def _scan_processes(self, devices: list[str]) -> tuple[dict[str, list[dict]], int]:
        owners: dict[str, list[dict]] = {device: [] for device in devices}
        scanned = 0
        for proc_path in glob.glob("/proc/[0-9]*"):
            try:
                pid = int(proc_path.rsplit("/", 1)[-1])
            except ValueError:
                continue
            if pid == os.getpid():
                continue
            command = _read_text(f"{proc_path}/cmdline")
            if not command:
                continue
            scanned += 1
            held: set[str] = set()
            for fd_path in glob.glob(f"{proc_path}/fd/*"):
                try:
                    target = os.readlink(fd_path)
                except OSError:
                    continue
                match = DEVICE_NODE_RE.match(target)
                if match and match.group(1) in owners:
                    held.add(match.group(1))
            for device in held:
                owners[device].append(
                    {
                        "pid": pid,
                        "process": _process_label(command),
                        "elapsed_seconds": _process_age(pid),
                        "evidence": "open-xdma-fd",
                    }
                )
        return owners, scanned

    def _workload_process_metrics(self, workload: dict[str, object]) -> dict[str, object]:
        roots: set[int] = set()
        for key in ("observer_pid", "child_pid"):
            value = workload.get(key)
            if _pid_alive(value):
                roots.add(int(value))
        if not workload.get("active") or not roots:
            return {
                "active": False,
                "process_count": 0,
                "rss_bytes": 0,
                "thread_count": 0,
                "processes": [],
                "source": "procfs process tree",
            }
        statuses: dict[int, dict[str, str]] = {}
        children: dict[int, list[int]] = {}
        for proc_path in glob.glob("/proc/[0-9]*"):
            try:
                pid = int(proc_path.rsplit("/", 1)[-1])
            except ValueError:
                continue
            fields = _status_fields(pid)
            statuses[pid] = fields
            parent = _status_int(fields, "PPid")
            if parent is not None:
                children.setdefault(parent, []).append(pid)
        selected = set(roots)
        queue = list(roots)
        while queue:
            parent = queue.pop()
            for child in children.get(parent, []):
                if child not in selected:
                    selected.add(child)
                    queue.append(child)
        rss = 0
        threads = 0
        labels = []
        for pid in sorted(selected):
            fields = statuses.get(pid) or _status_fields(pid)
            rss += _status_int(fields, "VmRSS", 1024) or 0
            threads += _status_int(fields, "Threads") or 0
            labels.append(
                {
                    "pid": pid,
                    "process": _process_label(_read_text(f"/proc/{pid}/cmdline")),
                }
            )
        return {
            "active": True,
            "process_count": len(selected),
            "rss_bytes": rss,
            "thread_count": threads,
            "processes": labels,
            "source": "procfs process tree",
        }

    def snapshot(self) -> dict[str, object]:
        devices = self.detected_devices()
        workload = load_workload_state(self.state_path or default_state_path())
        workload_cards = {
            card["device"]: card
            for card in workload.get("cards", [])
            if isinstance(card, dict) and isinstance(card.get("device"), str)
        }
        owners, scanned = self._scan_processes(devices)
        cards = []
        for index, device in enumerate(devices):
            nodes = sorted(glob.glob(f"/dev/{device}_*"))
            assignment = workload_cards.get(device) or {}
            active = bool(workload.get("active") and assignment)
            processes = owners.get(device, [])
            memory = assignment.get("runtime_memory") if isinstance(assignment, dict) else {}
            cards.append(
                {
                    "id": f"apex-{index + 1}",
                    "label": f"Apex {index + 1}",
                    "device": device,
                    "present": bool(nodes),
                    "device_nodes": len(nodes),
                    "busy": bool(processes) or active,
                    "processes": processes,
                    "activity": {
                        "model": workload.get("display_name") or workload.get("model"),
                        "phase": assignment.get("phase") or workload.get("phase"),
                        "elapsed_seconds": _elapsed(workload.get("started_at")),
                        "layer_start": assignment.get("layer_start"),
                        "layer_end_exclusive": assignment.get("layer_end_exclusive"),
                    }
                    if assignment
                    else None,
                    "runtime_memory": memory if isinstance(memory, dict) else {},
                    "memory_live": active,
                }
            )
        try:
            uptime = float(_read_text("/proc/uptime").split()[0])
        except (ValueError, IndexError):
            uptime = None
        return {
            "schema": SCHEMA,
            "checked_at": datetime.now(UTC).isoformat(),
            "host": {
                "hostname": os.uname().nodename,
                "cpu": {
                    "utilization_percent": self._cpu_percent(),
                    "logical_processors": os.cpu_count(),
                    "source": "procfs aggregate CPU time delta",
                },
                "memory": self._memory(),
                "load_average": self._load_average(),
                "workload_process": self._workload_process_metrics(workload),
                "uptime_seconds": uptime,
            },
            "cards": cards,
            "processes_scanned": scanned,
            "state_file": str(self.state_path),
            "measurement_boundary": (
                "Read-only procfs/XDMA ownership and host-resource sampling. Runtime memory "
                "comes only from the optional workload state file. Card activity is process "
                "ownership, not FPGA compute utilization."
            ),
        }

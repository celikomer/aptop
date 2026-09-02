import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

import pytest

from aptop import collector


def test_discovery_adapts_to_one_or_two_cards():
    one = collector.discover_devices(
        globber=lambda _pattern: ["/dev/xdma0_h2c_0", "/dev/xdma0_user"]
    )
    two = collector.discover_devices(
        globber=lambda _pattern: [
            "/dev/xdma1_user",
            "/dev/xdma0_c2h_0",
            "/dev/xdma1_h2c_0",
        ]
    )
    assert one == ["xdma0"]
    assert two == ["xdma0", "xdma1"]


def test_explicit_devices_are_validated_and_sorted():
    assert collector.discover_devices(("xdma1", "xdma0", "xdma1")) == ["xdma0", "xdma1"]
    with pytest.raises(ValueError, match="invalid XDMA device"):
        collector.discover_devices(("/dev/xdma0",))


def test_runtime_state_is_sanitized_and_stale_claims_retire(tmp_path: Path):
    state = tmp_path / "workload.json"
    fresh = datetime.now(UTC).isoformat()
    state.write_text(
        json.dumps(
            {
                "schema": collector.WORKLOAD_SCHEMA,
                "active": True,
                "updated_at": fresh,
                "display_name": "Public example",
                "cards": [
                    {
                        "device": "xdma0",
                        "layer_start": 0,
                        "layer_end_exclusive": 12,
                        "runtime_memory": {
                            "capacity_mib": 4096,
                            "weight_mib": 3000,
                            "known_mapped_percent": 150,
                            "unexpected": "discarded",
                        },
                    },
                    {"device": "../../bad"},
                ],
            }
        ),
        encoding="utf-8",
    )
    result = collector.load_workload_state(state)
    assert result["active"] is True
    assert len(result["cards"]) == 1
    memory = result["cards"][0]["runtime_memory"]
    assert memory["known_mapped_percent"] == 100
    assert "unexpected" not in memory

    old = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    payload = json.loads(state.read_text(encoding="utf-8"))
    payload["updated_at"] = old
    state.write_text(json.dumps(payload), encoding="utf-8")
    assert collector.load_workload_state(state)["active"] is False


def test_snapshot_has_one_card_without_fabricating_fpga_metrics(tmp_path: Path):
    sampler = collector.Collector(("xdma0",), tmp_path / "missing.json")
    with (
        mock.patch.object(sampler, "_scan_processes", return_value=({"xdma0": []}, 3)),
        mock.patch.object(sampler, "_cpu_percent", return_value=12.5),
        mock.patch.object(
            sampler,
            "_memory",
            return_value={
                "used_percent": 25.0,
                "used_bytes": 4 * 2**30,
                "total_bytes": 16 * 2**30,
            },
        ),
        mock.patch.object(
            sampler,
            "_load_average",
            return_value={"one_minute": 1, "five_minutes": 0.5, "fifteen_minutes": 0.25},
        ),
        mock.patch.object(
            sampler,
            "_workload_process_metrics",
            return_value={"active": False, "process_count": 0, "rss_bytes": 0},
        ),
        mock.patch.object(collector.glob, "glob", return_value=["/dev/xdma0_user"]),
    ):
        result = sampler.snapshot()
    assert result["schema"] == "aptop/v1"
    assert [card["device"] for card in result["cards"]] == ["xdma0"]
    assert result["cards"][0]["present"] is True
    assert "fpga_utilization" not in json.dumps(result).lower()
    assert "not FPGA compute utilization" in result["measurement_boundary"]


def test_process_labels_do_not_echo_arbitrary_arguments():
    command = "/usr/bin/python3 /opt/runtime/infer.py --prompt sample-input --option sample-value"
    assert collector._process_label(command) == "python3 infer.py"


def test_collector_source_never_opens_xdma_or_imports_vendor_runtime():
    source = Path(collector.__file__).read_text(encoding="utf-8")
    assert "os.open(" not in source
    assert "subprocess" not in source
    assert "torch" not in source
    assert "xdma_api" not in source

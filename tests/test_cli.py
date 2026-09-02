import json
from unittest import mock

from aptop import cli


def sample():
    return {
        "schema": "aptop/v1",
        "checked_at": "2026-01-01T00:00:00+00:00",
        "host": {
            "hostname": "apex-host",
            "cpu": {"utilization_percent": 5.0, "logical_processors": 8},
            "memory": {"used_percent": 25.0, "used_bytes": 4, "total_bytes": 16},
            "load_average": {"one_minute": 0.1, "five_minutes": 0.2, "fifteen_minutes": 0.3},
            "workload_process": {"process_count": 0, "rss_bytes": 0, "thread_count": 0},
        },
        "cards": [
            {
                "label": "Apex 1",
                "device": "xdma0",
                "present": True,
                "device_nodes": 3,
                "busy": False,
                "processes": [],
                "runtime_memory": {},
                "memory_live": False,
            }
        ],
    }


def test_json_implies_once(capsys):
    with mock.patch.object(cli, "_sample_for_output", return_value=sample()):
        assert cli.main(["--json", "--device", "xdma0"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["cards"][0]["device"] == "xdma0"


def test_invalid_interval_is_rejected():
    try:
        cli.main(["--once", "--interval", "0.1"])
    except SystemExit as exc:
        assert "at least 0.25" in str(exc)
    else:
        raise AssertionError("invalid interval was accepted")

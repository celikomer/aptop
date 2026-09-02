from pathlib import Path

import pytest

from aptop.collector import Collector
from aptop.tui import ApexCard, AptopApp, MemoryMap


def snapshot(card_count: int, *, active: bool = False) -> dict:
    cards = []
    for index in range(card_count):
        cards.append(
            {
                "id": f"apex-{index + 1}",
                "label": f"Apex {index + 1}",
                "device": f"xdma{index}",
                "present": True,
                "device_nodes": 3,
                "busy": active,
                "processes": [],
                "memory_live": active,
                "runtime_memory": {
                    "capacity_mib": 4096,
                    "weight_mib": 3000,
                    "kv_cache_mib": 64,
                    "tensor_scratch_mib": 96,
                    "program_mib": 8,
                    "unreported_or_reserved_mib": 928,
                    "known_mapped_mib": 3168,
                    "known_mapped_percent": 77.34,
                },
                "activity": {
                    "model": "Example model",
                    "phase": "decode",
                    "layer_start": index * 24,
                    "layer_end_exclusive": (index + 1) * 24,
                    "elapsed_seconds": 10,
                },
            }
        )
    return {
        "schema": "aptop/v1",
        "checked_at": "2026-01-01T00:00:00+00:00",
        "host": {
            "hostname": "apex-host",
            "cpu": {"utilization_percent": 12.5, "logical_processors": 8},
            "memory": {
                "used_percent": 25.0,
                "used_bytes": 4 * 2**30,
                "total_bytes": 16 * 2**30,
            },
            "load_average": {"one_minute": 1.0, "five_minutes": 0.5, "fifteen_minutes": 0.25},
            "workload_process": {"process_count": 0, "rss_bytes": 0, "thread_count": 0},
        },
        "cards": cards,
        "measurement_boundary": "Synthetic test fixture.",
    }


class HarnessApp(AptopApp):
    def on_mount(self) -> None:
        self._render_snapshot()


def make_app(card_count: int, *, active: bool = False) -> HarnessApp:
    collector = Collector(tuple(f"xdma{index}" for index in range(card_count)), Path("/tmp/none"))
    return HarnessApp(collector, snapshot(card_count, active=active), interval=1)


@pytest.mark.asyncio
async def test_single_card_fills_the_card_row():
    app = make_app(1, active=True)
    async with app.run_test(size=(180, 48)) as pilot:
        await pilot.pause()
        cards = list(app.query(ApexCard))
        assert len(cards) == 1
        assert cards[0].region.width > 160
        assert cards[0].query_one(MemoryMap).display is True
        assert app.query_one("#cards-grid").has_class("one-card")


@pytest.mark.asyncio
async def test_two_cards_share_a_wide_row():
    app = make_app(2, active=True)
    async with app.run_test(size=(200, 52)) as pilot:
        await pilot.pause()
        cards = list(app.query(ApexCard))
        assert len(cards) == 2
        assert cards[0].region.y == cards[1].region.y
        assert cards[0].region.width > 80
        assert cards[1].region.width > 80
        assert app.query_one("#cards-grid").has_class("two-card")


@pytest.mark.asyncio
async def test_two_cards_stack_on_a_narrow_terminal():
    app = make_app(2)
    async with app.run_test(size=(88, 34)) as pilot:
        await pilot.pause()
        cards = list(app.query(ApexCard))
        assert cards[1].region.y > cards[0].region.y
        assert cards[1].region.x == cards[0].region.x
        assert cards[0].query_one(MemoryMap).display is False
        app.action_toggle_history()
        await pilot.pause()
        assert cards[0].query_one(MemoryMap).display is True


@pytest.mark.asyncio
async def test_zero_cards_has_an_explanatory_panel():
    app = make_app(0)
    async with app.run_test(size=(120, 38)) as pilot:
        await pilot.pause()
        assert len(list(app.query(ApexCard))) == 0
        assert app.query_one("#no-cards").display is True

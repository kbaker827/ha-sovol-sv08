"""Tests for computed properties on SovolPrinterData."""
from __future__ import annotations

import pytest

from pysovol.models import FileMetadata, SovolPrinterData


def printing(progress: float, elapsed: float, estimate: float | None = None) -> SovolPrinterData:
    data = SovolPrinterData()
    data.print_stats.state = "printing"
    data.virtual_sdcard.progress = progress
    data.print_stats.print_duration = elapsed
    data.file_metadata = FileMetadata(estimated_time=estimate)
    return data


class TestTimeRemaining:
    def test_none_when_idle(self):
        assert SovolPrinterData().time_remaining is None

    def test_file_based_estimate(self):
        assert printing(0.5, 600).time_remaining == 600

    def test_slicer_estimate_early_in_print(self):
        assert printing(0.05, 120, estimate=3600).time_remaining == 3480

    def test_slicer_estimate_never_negative(self):
        assert printing(0.05, 5000, estimate=3600).time_remaining == 0

    def test_file_estimate_once_past_limit(self):
        assert printing(0.25, 300, estimate=3600).time_remaining == 900

    def test_unknown_at_start_without_slicer_estimate(self):
        assert printing(0.0, 0).time_remaining is None

    def test_done(self):
        assert printing(1.0, 1000).time_remaining == 0


class TestStateFlags:
    @pytest.mark.parametrize(
        ("state", "printing_", "paused", "active"),
        [
            ("printing", True, False, True),
            ("paused", False, True, True),
            ("standby", False, False, False),
            ("complete", False, False, False),
        ],
    )
    def test_print_states(self, state, printing_, paused, active):
        data = SovolPrinterData()
        data.print_stats.state = state
        assert (data.is_printing, data.is_paused, data.is_active) == (printing_, paused, active)

    def test_error_from_klipper_shutdown(self):
        data = SovolPrinterData()
        data.klipper_state = "shutdown"
        assert data.has_error
        assert not data.is_ready

    def test_homed_needs_all_axes(self):
        data = SovolPrinterData()
        data.toolhead.homed_axes = "xy"
        assert not data.is_homed
        data.toolhead.homed_axes = "xyz"
        assert data.is_homed

    def test_total_layers_prefers_print_stats(self):
        data = SovolPrinterData()
        data.file_metadata = FileMetadata(layer_count=200)
        assert data.total_layers == 200
        data.print_stats.total_layer = 150
        assert data.total_layers == 150

    def test_progress_pct(self):
        data = SovolPrinterData()
        data.virtual_sdcard.progress = 0.4567
        assert data.print_progress_pct == 45.7

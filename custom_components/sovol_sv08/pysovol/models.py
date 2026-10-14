"""Data models for the Sovol SV08 (Klipper/Moonraker) printer."""
from __future__ import annotations

from dataclasses import dataclass, field

from .const import (
    DEFAULT_BED_MAX_TEMP,
    DEFAULT_EXTRUDER_MAX_TEMP,
    KLIPPER_STARTUP,
    SLICER_ESTIMATE_PROGRESS_LIMIT,
    STATE_STANDBY,
)


@dataclass
class HeaterData:
    """A Klipper heater: the extruder, the bed or a heater_generic."""

    temperature: float = 0.0
    target: float = 0.0
    power: float = 0.0
    # Klipper accepts 0 (off) or a target between min_temp and max_temp.
    min_temp: float = 0.0
    max_temp: float = 0.0


@dataclass
class PrintStats:
    """Current print job statistics."""

    state: str = STATE_STANDBY
    filename: str = ""
    total_duration: float = 0.0
    print_duration: float = 0.0
    filament_used: float = 0.0
    # Only reported when the slicer emits SET_PRINT_STATS_INFO
    current_layer: int = 0
    total_layer: int = 0


@dataclass
class FileMetadata:
    """Slicer metadata for the file being printed (/server/files/metadata)."""

    estimated_time: float | None = None
    layer_count: int | None = None
    filament_total: float | None = None
    slicer: str | None = None


@dataclass
class VirtualSdCard:
    """Virtual SD card state (file progress)."""

    progress: float = 0.0
    is_active: bool = False
    file_position: int = 0


@dataclass
class Toolhead:
    """Toolhead position and state."""

    position: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])
    homed_axes: str = ""
    max_velocity: float = 0.0
    max_accel: float = 0.0


@dataclass
class GcodeMove:
    """Live G-code motion state (includes override factors)."""

    speed_factor: float = 1.0    # M220: 1.0 = 100 %
    extrude_factor: float = 1.0  # M221: 1.0 = 100 %


@dataclass
class FilamentSensor:
    """State of a single filament runout sensor."""

    enabled: bool = True
    filament_detected: bool = True


@dataclass
class SovolPrinterData:
    """Complete snapshot of Sovol SV08 printer state."""

    # Klipper daemon state
    klipper_state: str = KLIPPER_STARTUP
    klipper_message: str = ""

    # Print job
    print_stats: PrintStats = field(default_factory=PrintStats)
    virtual_sdcard: VirtualSdCard = field(default_factory=VirtualSdCard)
    file_metadata: FileMetadata = field(default_factory=FileMetadata)

    # Heaters
    extruder: HeaterData = field(
        default_factory=lambda: HeaterData(max_temp=DEFAULT_EXTRUDER_MAX_TEMP)
    )
    heater_bed: HeaterData = field(
        default_factory=lambda: HeaterData(max_temp=DEFAULT_BED_MAX_TEMP)
    )
    # {"heater_generic <name>": HeaterData}
    chamber_heaters: dict[str, HeaterData] = field(default_factory=dict)
    # {"temperature_sensor <name>": °C}
    temperature_sensors: dict[str, float] = field(default_factory=dict)

    # Motion / overrides
    toolhead: Toolhead = field(default_factory=Toolhead)
    gcode_move: GcodeMove = field(default_factory=GcodeMove)

    # Fans: part cooling in %, others {"heater_fan hotend_fan": %}
    fan_speed: float = 0.0
    extra_fans: dict[str, float] = field(default_factory=dict)

    # Leveling (QUAD_GANTRY_LEVEL / BED_MESH_CALIBRATE)
    qgl_applied: bool | None = None
    bed_mesh_profile: str | None = None

    # Light (output_pin main_led on a stock SV08)
    light_on: bool = False

    # Ancillary
    idle_state: str = "Idle"
    display_message: str = ""
    filament_sensors: dict[str, FilamentSensor] = field(default_factory=dict)
    available_files: list[str] = field(default_factory=list)

    # Printer identity (from /printer/info)
    printer_name: str = "Sovol SV08"
    firmware_version: str = ""

    # -------------------------------------------------------------------
    # Computed properties
    # -------------------------------------------------------------------

    @property
    def is_printing(self) -> bool:
        return self.print_stats.state == "printing"

    @property
    def is_paused(self) -> bool:
        return self.print_stats.state == "paused"

    @property
    def is_active(self) -> bool:
        """True while a print is running or paused."""
        return self.is_printing or self.is_paused

    @property
    def is_ready(self) -> bool:
        return self.klipper_state == "ready"

    @property
    def has_error(self) -> bool:
        return self.print_stats.state == "error" or self.klipper_state in (
            "error",
            "shutdown",
        )

    @property
    def is_homed(self) -> bool:
        return all(axis in self.toolhead.homed_axes for axis in "xyz")

    @property
    def print_progress_pct(self) -> float:
        """Print progress as a percentage (0-100)."""
        return round(self.virtual_sdcard.progress * 100, 1)

    @property
    def total_layers(self) -> int:
        """Total layers, from SET_PRINT_STATS_INFO or else the slicer metadata."""
        return self.print_stats.total_layer or self.file_metadata.layer_count or 0

    @property
    def time_remaining(self) -> int | None:
        """Estimated seconds remaining, or None when not calculable.

        Early in a print the file-position estimate swings wildly, so the
        slicer's estimate is used until enough of the file has been printed.
        """
        if not self.is_printing:
            return None
        progress = self.virtual_sdcard.progress
        elapsed = self.print_stats.print_duration
        if progress >= 1.0:
            return 0
        slicer = self.file_metadata.estimated_time
        if slicer and progress < SLICER_ESTIMATE_PROGRESS_LIMIT:
            return max(0, int(slicer - elapsed))
        if progress <= 0 or elapsed <= 0:
            return None
        return max(0, int(elapsed / progress - elapsed))

    @property
    def speed_factor_pct(self) -> int:
        """Print speed override as a percentage (e.g. 100)."""
        return round(self.gcode_move.speed_factor * 100)

    @property
    def flow_rate_pct(self) -> int:
        """Extrusion flow-rate override as a percentage (e.g. 100)."""
        return round(self.gcode_move.extrude_factor * 100)

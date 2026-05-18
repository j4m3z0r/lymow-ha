from dataclasses import dataclass, field


class RobotStatus:
    NONE = 0
    WAITING = 1
    CLEANING = 2
    PAUSE = 3
    DOCKING = 4
    CHARGING = 5
    REMOTE_CONTROL = 6
    ERROR = 7
    RESUME = 8
    ZONE_PARTITION = 9
    PAUSE_DOCKING = 10
    UPDATING = 11
    CHARGING_FULL = 12
    EMERGENCY_STOP = 13

    _NAMES = {
        0: "none",
        1: "waiting",
        2: "cleaning",
        3: "paused",
        4: "docking",
        5: "charging",
        6: "remote_control",
        7: "error",
        8: "resuming",
        9: "zone_partition",
        10: "paused_docking",
        11: "updating",
        12: "charging_full",
        13: "emergency_stop",
    }

    @classmethod
    def name(cls, value: int) -> str:
        return cls._NAMES.get(value, f"unknown_{value}")


class ZoneStatus:
    UNKNOWN = "unknown"
    ACTIVE = "active"
    DONE = "done"


@dataclass
class Zone:
    hash_id: str
    name: str = ""
    zone_type: str = "go"  # "go" or "nogo"
    polygon: list[tuple[float, float]] = field(default_factory=list)  # ENU (x, y) metres


@dataclass
class Channel:
    points: list[tuple[float, float]] = field(default_factory=list)  # ENU (x, y) metres — ordered waypoints


@dataclass
class MowerState:
    robot_status: int = RobotStatus.NONE
    battery: int | None = None
    error_codes: list[int] = field(default_factory=list)
    warning_codes: list[int] = field(default_factory=list)
    position: tuple[float, float] | None = None  # ENU (x, y) metres from dock
    heading: float | None = None  # radians, east=0, counterclockwise

    @property
    def status_name(self) -> str:
        return RobotStatus.name(self.robot_status)

    @property
    def is_mowing(self) -> bool:
        return self.robot_status == RobotStatus.CLEANING

    @property
    def is_docked(self) -> bool:
        return self.robot_status in (RobotStatus.CHARGING, RobotStatus.CHARGING_FULL, RobotStatus.WAITING)

    @property
    def is_returning(self) -> bool:
        return self.robot_status in (RobotStatus.DOCKING, RobotStatus.PAUSE_DOCKING)

    @property
    def has_error(self) -> bool:
        return self.robot_status == RobotStatus.ERROR or bool(self.error_codes)


@dataclass
class LymowData:
    state: MowerState
    zones: list[Zone]                   # populated after first map fetch
    channels: list[Channel]             # connector polylines between zones
    zone_statuses: dict[str, str]       # hash_id → ZoneStatus; always empty in V1

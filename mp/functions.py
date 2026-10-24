"""Function template registry: one setup class per chain function.

Each class owns its config spec; parse() turns the raw YAML setup dict into the
class instance. Unknown keys, missing required values and out-of-range values
raise SetupError.
"""

from dataclasses import dataclass, fields
from enum import Enum
from typing import Self


class FunctionName(Enum):
    FORWARDER = "forwarder"
    PRINTER = "printer"
    QUEUE = "queue"
    DROP = "drop"
    FIREWALL = "firewall"


def _plain(v: object) -> str:
    return v.value if isinstance(v, Enum) else str(v)


class SetupError(ValueError):
    pass


class Proto(Enum):
    TCP = "tcp"
    UDP = "udp"
    ICMP = "icmp"


@dataclass(frozen=True)
class Setup:
    """Base class for per-function setup configs."""

    def summary(self) -> str:
        """"k=v, ..." over the set fields, or "-" when empty."""
        items = [
            f"{f.name}={_plain(getattr(self, f.name))}"
            for f in fields(self)
            if getattr(self, f.name) is not None
        ]
        return ", ".join(items) or "-"

    @classmethod
    def parse(cls, raw: dict | None) -> Self:
        raise NotImplementedError

    @staticmethod
    def _num(raw: dict | None, key: str, minimum: float, positive_only: bool = True) -> float | None:
        v = (raw or {}).get(key)
        if v is None:
            return None
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            raise SetupError(f"{key}: expected number, got {v!r}")
        if positive_only and v <= 0:
            raise SetupError(f"{key}: must be > 0, got {v}")
        if not positive_only and v < minimum:
            raise SetupError(f"{key}: must be >= {minimum}, got {v}")
        return float(v)


@dataclass(frozen=True)
class ForwarderSetup(Setup):
    """Passthrough; takes no setup fields."""

    @classmethod
    def parse(cls, raw: dict | None) -> "ForwarderSetup":
        if raw:
            raise SetupError(f"forwarder takes no setup fields, got {sorted(raw)}")
        return cls()


@dataclass(frozen=True)
class PrinterSetup(Setup):
    """Print from->to at IP level."""

    to: str = "stdout"  # stdout | file

    @classmethod
    def parse(cls, raw: dict | None) -> "PrinterSetup":
        to = (raw or {}).get("to", "stdout")
        if to not in ("stdout", "file"):
            raise SetupError(f"to: expected stdout|file, got {to!r}")
        return cls(to=to)


@dataclass(frozen=True)
class QueueSetup(Setup):
    """Rate, delay and/or queue-size shaping; at least one knob required."""

    rate_mbps: float | None = None
    delay_ms: float | None = None
    size_pkts: int | None = None

    @classmethod
    def parse(cls, raw: dict | None) -> "QueueSetup":
        raw = raw or {}
        unknown = set(raw) - {"rate_mbps", "delay_ms", "size_pkts"}
        if unknown:
            raise SetupError(f"unknown setup fields {sorted(unknown)}")
        rate = cls._num(raw, "rate_mbps", 0)
        delay = cls._num(raw, "delay_ms", 0)
        size = raw.get("size_pkts")
        if size is not None:
            if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
                raise SetupError(f"size_pkts: must be positive integer, got {size!r}")
        if rate is None and delay is None and size is None:
            raise SetupError("queue needs at least one of rate_mbps/delay_ms/size_pkts")
        return cls(rate_mbps=rate, delay_ms=delay, size_pkts=size)


@dataclass(frozen=True)
class DropSetup(Setup):
    """Fixed percent of packets dropped, set in setup."""

    percent: float = 100.0

    @classmethod
    def parse(cls, raw: dict | None) -> "DropSetup":
        raw = raw or {}
        if set(raw) - {"percent"}:
            raise SetupError(f"unknown setup fields {sorted(raw)}")
        p = raw.get("percent", 100.0)
        if not isinstance(p, (int, float)) or isinstance(p, bool) or not 0 <= p <= 100:
            raise SetupError(f"percent: must be a number in [0, 100], got {p!r}")
        return cls(percent=float(p))


@dataclass(frozen=True)
class FirewallSetup(Setup):
    """Drop proto/port matched traffic."""

    proto: Proto = Proto.TCP
    port: int | None = None

    @classmethod
    def parse(cls, raw: dict | None) -> "FirewallSetup":
        raw = raw or {}
        if set(raw) - {"proto", "port"}:
            raise SetupError(f"unknown setup fields {sorted(raw)}")
        proto = raw.get("proto")
        if proto not in Proto._value2member_map_:
            raise SetupError(f"proto: expected one of {[p.value for p in Proto]}, got {proto!r}")
        port = raw.get("port")
        if port is not None:
            if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
                raise SetupError(f"port: must be integer in [1, 65535], got {port!r}")
        return cls(proto=Proto(proto), port=port)


REGISTRY: dict[FunctionName, type[Setup]] = {
    FunctionName.FORWARDER: ForwarderSetup,
    FunctionName.PRINTER: PrinterSetup,
    FunctionName.QUEUE: QueueSetup,
    FunctionName.DROP: DropSetup,
    FunctionName.FIREWALL: FirewallSetup,
}
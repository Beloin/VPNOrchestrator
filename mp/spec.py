"""Spec loading, validation and resolution (current.yaml -> default.yaml fallback)."""

import json
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import yaml
from jsonschema import Draft7Validator

from functions import FunctionName, REGISTRY, Setup, SetupError

# Mount this dir as a Docker volume carrying default.yaml / current.yaml
SPEC_DIR = Path(os.environ.get("MP_SPEC_DIR", Path(__file__).parent / "specs"))

SCHEMA_PATH = Path(__file__).parent / "schema.json"


class SpecSource(Enum):
    """Which file the resolved spec came from."""

    CURRENT = "current.yaml"
    DEFAULT = "default.yaml"


class SpecError(Exception):
    pass


@dataclass(frozen=True)
class Node:
    name: str
    function: FunctionName
    setup: Setup


@dataclass(frozen=True)
class Spec:
    # Ordered chain: nodes[i] forwards to nodes[i+1]; the egress node (last)
    # forwards to the internet. No IPs here: k8s assigns addresses and each
    # node is found by DNS (headless Service named after the node).
    nodes: list[Node]

    @property
    def egress(self) -> Node:
        """Last node; it forwards to the internet."""
        return self.nodes[-1]

    def next(self, n: Node) -> str:
        """Node this node forwards to: next node name or \"internet\"."""
        i = self.nodes.index(n)
        return self.nodes[i + 1].name if i < len(self.nodes) - 1 else "internet"

    def line(self, n: Node) -> str:
        return f"{n.name}: {n.function.value} -> {self.next(n)} (setup: {n.setup.summary()})"

    def __str__(self) -> str:
        return "\n".join(self.line(n) for n in self.nodes)


def validate(spec: dict) -> list[str]:
    """Return schema validation errors for a spec (empty list = valid)."""
    schema = json.loads(SCHEMA_PATH.read_text())
    validator = Draft7Validator(schema)
    errors: dict[str, str] = {}  # path -> message
    for err in validator.iter_errors(spec):
        loc = ".".join(str(p) for p in err.path) or "root"
        # nested oneOf/setup errors are noise; keep the shallowest per path
        if loc not in errors:
            errors[loc] = err.message
    return [f"{loc}: {msg}" for loc, msg in errors.items()]


def cross_validate(spec: dict) -> list[str]:
    """Cross-node rules JSON Schema cannot express."""
    import re

    errs: list[str] = []
    chain = spec.get("chain", [])
    names = [n["name"] for n in chain]

    if len(names) != len(set(names)):
        errs.append("chain: duplicate node names")

    # node names become k8s Service/DNS names, so they must be RFC1123 DNS labels
    for n in names:
        if not re.fullmatch(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?", n):
            errs.append(f"chain: node name {n!r} is not a valid DNS label")

    return errs


def load_file(path: Path) -> tuple[dict | None, list[str]]:
    """Parse and validate one spec file. Returns (spec, errors); spec None if unreadable."""
    try:
        raw = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as e:
        return None, [f"{path.name}: {e}"]
    if raw is None:
        return None, [f"{path.name}: empty (null)"]
    if not isinstance(raw, dict):
        return None, [f"{path.name}: expected mapping, got {type(raw).__name__}"]
    errs = validate(raw)
    if errs:
        return raw, errs
    return raw, cross_validate(raw)


def _to_spec(raw: dict) -> Spec:
    """Build the typed Spec; raises SpecError on bad setup values."""
    nodes: list[Node] = []
    try:
        for n in raw["chain"]:
            function = FunctionName(n["function"])
            nodes.append(
                Node(
                    name=n["name"],
                    function=function,
                    setup=REGISTRY[function].parse(n.get("setup")),
                )
            )
    except (SetupError, ValueError, KeyError) as e:
        raise SpecError(str(e)) from e

    return Spec(nodes=nodes)


def resolve() -> tuple[Spec, SpecSource]:
    """Use current.yaml if valid, else default.yaml. Raises SpecError if neither works."""
    current, cur_errs = load_file(SPEC_DIR / "current.yaml")
    current_spec: Spec | None = None

    if current is not None and not cur_errs:
        try:
            current_spec = _to_spec(current)
        except SpecError as e:
            cur_errs = [str(e)]
    if current_spec is not None:
        return current_spec, SpecSource.CURRENT
    if cur_errs and "[Errno 2]" in cur_errs[0]:
        pass  # current.yaml simply unset
    elif cur_errs and not cur_errs[0].startswith("current.yaml: empty"):
        print(f"warning: current.yaml ignored ({cur_errs[0]})")

    default, def_errs = load_file(SPEC_DIR / "default.yaml")
    if default is None or def_errs:
        raise SpecError("no usable spec:\n  " + "\n  ".join(cur_errs + def_errs))
    try:
        return _to_spec(default), SpecSource.DEFAULT
    except SpecError as e:
        raise SpecError(f"default.yaml: {e}") from e


def parse_spec_text(text: str) -> Spec:
    """Parse a raw yaml spec string; raises SpecError on anything invalid."""
    raw = yaml.safe_load(text)
    if not isinstance(raw, dict):
        raise SpecError(f"expected mapping, got {type(raw).__name__}")
    errs = validate(raw)
    if not errs:
        errs = cross_validate(raw)
    if errs:
        raise SpecError("; ".join(errs))
    return _to_spec(raw)


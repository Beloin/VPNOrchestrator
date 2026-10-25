#!/usr/bin/env python3
"""Spec parsing tests: raw yaml strings -> instantiated Spec/Setup classes.

Runs standalone (python3 test_spec.py) or via `heph run //mp:test`.
"""

import sys

from functions import (
    DropSetup,
    FirewallSetup,
    ForwarderSetup,
    FunctionName,
    PrinterSetup,
    Proto,
    QueueSetup,
)
from spec import Node, Spec, SpecError, parse_spec_text
import test_codegen as _cg

# every function template in one valid chain; ordering implies the forwarding
ALL_NODES_YAML = """
entrypoint: 10.0.0.1
chain:
  - name: prn
    function: printer
    ip: 10.0.0.1
    setup:
      to: file
  - name: lim
    function: queue
    ip: 10.0.0.2
    setup:
      rate_mbps: 5
      delay_ms: 2
      size_pkts: 64
  - name: fw
    function: firewall
    ip: 10.0.0.3
    setup:
      proto: udp
      port: 53
  - name: rd
    function: drop
    ip: 10.0.0.4
    setup:
      percent: 25
  - name: rel
    function: forwarder
    ip: 10.0.0.5
"""

ALL_NODES_SPEC = Spec(
    entrypoint="10.0.0.1",
    nodes=[
        Node("prn", FunctionName.PRINTER, "10.0.0.1", PrinterSetup(to="file")),
        Node("lim", FunctionName.QUEUE, "10.0.0.2", QueueSetup(rate_mbps=5.0, delay_ms=2.0, size_pkts=64)),
        Node("fw", FunctionName.FIREWALL, "10.0.0.3", FirewallSetup(proto=Proto.UDP, port=53)),
        Node("rd", FunctionName.DROP, "10.0.0.4", DropSetup(percent=25.0)),
        Node("rel", FunctionName.FORWARDER, "10.0.0.5", ForwarderSetup()),
    ],
)


def test_all_nodes_chain() -> None:
    got = parse_spec_text(ALL_NODES_YAML)
    assert got == ALL_NODES_SPEC, f"instantiated spec mismatch:\n{got}\n!=\n{ALL_NODES_SPEC}"


def test_no_nodes() -> None:
    for name, text in [
        ("empty chain", "entrypoint: 10.0.0.1\nchain: []\n"),
        ("missing chain", "entrypoint: 10.0.0.1\n"),
    ]:
        try:
            parse_spec_text(text)
            raise AssertionError(f"{name}: expected SpecError")
        except SpecError as e:
            assert "chain" in str(e), f"{name}: unexpected error: {e}"


def test_all_functions() -> None:
    """One single-node chain per function template, each against its setup class."""
    entry = "entrypoint: 10.0.0.9\n"
    node = "- name: a\n    function: {}\n    ip: 10.0.0.9\n"
    cases = [
        (node.format("forwarder"), ForwarderSetup()),
        (node.format("printer") + "    setup:\n      to: stdout\n", PrinterSetup(to="stdout")),
        (
            node.format("queue") + "    setup:\n      rate_mbps: 10\n",
            QueueSetup(rate_mbps=10.0),
        ),
        (node.format("drop") + "    setup:\n      percent: 50\n", DropSetup(percent=50.0)),
        (
            node.format("firewall") + "    setup:\n      proto: icmp\n",
            FirewallSetup(proto=Proto.ICMP),
        ),
    ]
    for yaml_body, setup in cases:
        got = parse_spec_text(entry + "chain:\n  " + yaml_body)
        assert got.nodes[0].name == "a" and got.next(got.nodes[0]) == "internet"
        assert got.nodes[0].setup == setup, f"{setup} != {got.nodes[0].setup}"

    # bad setup values must raise
    bad = [
        (node.format("forwarder") + "    setup:\n      x: 1\n", "forwarder"),
        (node.format("queue") + "    setup: {}\n", "queue"),
        (node.format("drop") + "    setup:\n      percent: 150\n", "percent"),
        (node.format("firewall") + "    setup:\n      proto: sctp\n", "proto"),
    ]
    for raw, what in bad:
        try:
            parse_spec_text(entry + "chain:\n  " + raw)
            raise AssertionError(f"bad {what} setup: expected SpecError")
        except SpecError:
            pass


def test_two_functions() -> None:
    got = parse_spec_text(
        """
entrypoint: 10.0.0.1
chain:
  - name: prn
    function: printer
    ip: 10.0.0.1
  - name: lim
    function: queue
    ip: 10.0.0.2
    setup:
      rate_mbps: 10
"""
    )
    assert got.entrypoint == "10.0.0.1"
    assert [n.name for n in got.nodes] == ["prn", "lim"]
    assert got.nodes[0].setup == PrinterSetup(to="stdout")  # default setup


def test_last_node_internet() -> None:
    """Ordering implies forwarding: next(i) = node i+1, last node -> internet."""
    got = parse_spec_text(ALL_NODES_YAML)
    for a, b in zip(got.nodes, got.nodes[1:]):
        assert got.next(a) == b.name, f"{a.name} must forward to {b.name}"
    assert got.next(got.egress) == "internet", "egress must be the internet hop"

    # forward is not part of the schema anymore; any explicit value is an error
    try:
        parse_spec_text(
            """
entrypoint: 10.0.0.1
chain:
  - name: prn
    function: printer
    forward: limiter
    ip: 10.0.0.1
"""
        )
        raise AssertionError("explicit forward: expected SpecError")
    except SpecError:
        pass


TESTS = [
    test_all_nodes_chain,
    test_no_nodes,
    test_all_functions,
    test_two_functions,
    test_last_node_internet,
] + _cg.TESTS


def main() -> int:
    failures = []
    for t in TESTS:
        try:
            t()
            print(f"PASS {t.__name__}")
        except Exception as e:
            failures.append(f"FAIL {t.__name__}: {e}")
    # print failures last on stdout: heph truncates the log tail, stderr-first
    # output hides them
    for f in failures:
        print(f)
    print(f"{len(TESTS) - len(failures)}/{len(TESTS)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

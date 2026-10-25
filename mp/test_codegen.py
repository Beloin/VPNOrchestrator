#!/usr/bin/env python3
"""Codegen tests: resolve a spec -> plan, click configs, compose text."""

from functions import (
    DropSetup,
    FirewallSetup,
    ForwarderSetup,
    FunctionName,
    PrinterSetup,
    Proto,
    QueueSetup,
)
from codegen import CodegenError, gen_compose, gen_config, plan_topology
from spec import Node, Spec, parse_spec_text

CHAIN = parse_spec_text(
    """
entrypoint: 10.0.0.1
chain:
  - name: prn
    function: printer
    ip: 10.0.0.1
    setup:
      to: file
  - name: lim
    function: queue
    ip: 10.0.1.2
    setup:
      rate_mbps: 5
      delay_ms: 2
      size_pkts: 64
  - name: fw
    function: firewall
    ip: 10.0.2.3
    setup:
      proto: udp
      port: 53
  - name: drp
    function: drop
    ip: 10.0.3.4
    setup:
      percent: 25
  - name: rel
    function: forwarder
    ip: 10.0.4.5
"""
)

def test_plan_addresses() -> None:
    plan = plan_topology(CHAIN)
    # node0 keeps its spec ip on net-in; its eth1 = edge0's .250
    assert plan.nodes[0].eth0.ip == "10.0.0.1" and plan.nodes[0].eth0.net == "net-in"
    assert plan.nodes[0].eth1.ip == "10.0.1.250" and plan.nodes[0].eth1.net == "edge0"
    # downstream node's spec ip defines the edge subnet
    assert plan.nets["edge0"] == "10.0.1.0/24"
    assert plan.nodes[1].eth0.ip == "10.0.1.2"
    # last node's eth1 = fixed egress subnet; clients sit at .254
    assert plan.nodes[4].eth1.ip == "10.254.254.250" and plan.nodes[4].eth1.net == "net-out"
    assert plan.pinger.ip == "10.0.0.254" and plan.pinger.mac == "02:00:0a:00:00:fe"
    assert plan.sink.ip == "10.254.254.254" and plan.sink.mac == "02:00:0a:fe:fe:fe"


def test_invalid_plan_addresses() -> None:
    for name, text in [
        ("same /24", "entrypoint: 10.0.0.1\nchain:\n  - name: a\n    function: forwarder\n    ip: 10.0.0.1\n  - name: b\n    function: forwarder\n    ip: 10.0.0.2\n"),
        ("reserved host", "entrypoint: 10.0.0.250\nchain:\n  - name: a\n    function: forwarder\n    ip: 10.0.0.250\n  - name: b\n    function: forwarder\n    ip: 10.0.1.2\n"),
        ("egress overlap", "entrypoint: 10.254.254.1\nchain:\n  - name: a\n    function: forwarder\n    ip: 10.254.254.1\n  - name: b\n    function: forwarder\n    ip: 10.0.1.2\n"),
    ]:
        try:
            gen_config(parse_spec_text(text))
            raise AssertionError(f"{name}: expected CodegenError")
        except CodegenError:
            pass


def test_gen_config_shapes() -> None:
    cfg = gen_config(CHAIN)
    assert set(cfg) == {n.name for n in CHAIN.nodes}
    # printer: Print on both directions; no shapers
    assert cfg["prn"].count("Print") == 2 and "BandwidthShaper" not in cfg["prn"]
    # queue element wiring: Queue -> DelayUnqueue -> Queue -> BandwidthShaper
    assert "Queue(64)" in cfg["lim"] and "DelayUnqueue(2ms)" in cfg["lim"] and "Queue(64)\n  -> BandwidthShaper(5Mbps)" not in cfg["lim"]
    assert cfg["lim"].count("BandwidthShaper(5Mbps)") == 2
    # firewall drops udp/53, allows the rest
    assert "IPFilter(drop udp dst port 53, allow)" in cfg["fw"]
    # drop: fixed percent
    assert "RandomSample(DROP 0.25)" in cfg["drp"]
    # forwarder: no extra elements beyond the skeleton
    assert "Print" not in cfg["rel"] and "RandomSample" not in cfg["rel"]
    # every config has both directions and the skeleton
    for node, text in cfg.items():
        assert text.count("FromDevice(eth0)") == 1 and text.count("FromDevice(eth1)") == 1
        assert "Classifier(12/0800)" in text and "CheckIPHeader()" in text
        assert "ToDevice(eth1)" in text and "ToDevice(eth0)" in text


def test_gen_compose() -> None:
    text = gen_compose(CHAIN)
    for want in (
        "ipv4_address: 10.0.0.1", "ipv4_address: 10.254.254.250",
        "gateway: 10.0.0.253", "gateway: 10.254.254.253",
        "ip route replace default via 10.0.0.1", "ip route replace default via 10.254.254.250",
    ):
        assert want in text, f"missing in compose: {want!r} (text len {len(text)})"
    # every node: its spec ip on its ingress net, .250 synth on egress
    assert "ipv4_address: 10.0.1.2" in text
    # interfaces pinned by mac (docker numbers ethN alphabetically otherwise)
    count = text.count("MP_IN_MAC:")  # environment keys; the script refs use $
    assert count == len(CHAIN.nodes), f"MP_IN_MAC count {count} != {len(CHAIN.nodes)}"
    # gateways do not collide with node/client addresses
    assert "gateway: 10.0.1.253" in text


TESTS = [test_plan_addresses, test_invalid_plan_addresses, test_gen_config_shapes, test_gen_compose]


def main() -> int:
    failed = 0
    for t in TESTS:
        try:
            t()
            print(f"PASS {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}", file=sys.stderr)
    print(f"{len(TESTS) - failed}/{len(TESTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
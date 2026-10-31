#!/usr/bin/env python3
"""Codegen tests: resolve a spec -> click configs + k8s manifests."""

from codegen import CodegenError, gen_config, gen_k8s
from spec import parse_spec_text

# 3 nodes = one of each role: ingress (printer), relay (firewall), egress (queue)
CHAIN = parse_spec_text(
    """
chain:
  - name: prn
    function: printer
  - name: fw
    function: firewall
    setup:
      proto: udp
      port: 53
  - name: lim
    function: queue
    setup:
      rate_mbps: 5
"""
)


def test_invalid_chain() -> None:
    try:
        gen_k8s(parse_spec_text("chain:\n  - name: a\n    function: forwarder\n"))
        raise AssertionError("single-node chain: expected CodegenError")
    except CodegenError:
        pass


def test_gen_config_shapes() -> None:
    cfg = gen_config(CHAIN)
    assert set(cfg) == {"prn", "fw", "lim"}

    # every node: one eth0, MAC filter, ARPQuerier, no EtherEncap / no static IPs
    for text in cfg.values():
        assert "FromDevice(eth0)" in text
        assert "Classifier(0/__MY_MACHEX__, -)" in text
        assert "ARPQuerier(__MY_IP__, __MY_MAC__)" in text
        assert "ToDevice(eth0)" in text
        assert "EtherEncap" not in text
        assert "10.0." not in text and "10.254." not in text

    # ingress: generic IPv4 forward (no ICMP-only gate), IP-in-IP return,
    # function on both directions
    assert "IPClassifier(ip proto 4, -)" in cfg["prn"]
    assert "icmp type echo" not in cfg["prn"]
    assert "IPEncap(4, __MY_IP__, __NEXT_IP__)" in cfg["prn"]
    assert cfg["prn"].count("Print('prn: packet seen')") == 2

    # relay: classify the two neighbors by outer source IP
    assert "ip proto 4 and src __PREV_IP__" in cfg["fw"]
    assert "ip proto 4 and src __NEXT_IP__" in cfg["fw"]
    assert "IPEncap(4, __MY_IP__, __NEXT_IP__)" in cfg["fw"]
    assert "IPEncap(4, __MY_IP__, __PREV_IP__)" in cfg["fw"]
    assert cfg["fw"].count("IPFilter(drop udp dst port 53, allow)") == 2

    # egress: generic NAT (TCP/UDP + ICMP echo) + route lookup; shaping on the
    # shared output
    assert "IPRewriter(pattern __MY_IP__ 50000-65535 - - 0 1)" in cfg["lim"]
    assert "ICMPPingRewriter(pattern __MY_IP__ 50000-65535 - - 0 1)" in cfg["lim"]
    assert "IPClassifier(tcp or udp, icmp, -)" in cfg["lim"]
    assert "SetTCPChecksum" in cfg["lim"] and "SetUDPChecksum" in cfg["lim"]
    assert "RadixIPLookup(__MY_SUBNET__ 0, 0.0.0.0/0 __MY_GW__ 0)" in cfg["lim"]
    assert "IPEncap(4, __MY_IP__, __PREV_IP__)" in cfg["lim"]
    assert cfg["lim"].count("BandwidthShaper(5Mbps)") == 1


def test_gen_k8s() -> None:
    files = gen_k8s(CHAIN)
    assert set(files) == {"services.yaml", "cm-prn.yaml", "cm-fw.yaml", "cm-lim.yaml",
                          "pods.yaml", "deploy.sh"}

    # headless Services per node + pinger
    svc = files["services.yaml"]
    assert "name: prn" in svc and "name: fw" in svc and "name: lim" in svc and "name: pinger" in svc
    assert svc.count("clusterIP: None") == 4

    # ConfigMaps carry the click config as a block scalar
    assert "name: prn-config" in files["cm-prn.yaml"]
    assert "router.click: |" in files["cm-prn.yaml"]
    assert "ARPQuerier(__MY_IP__, __MY_MAC__)" in files["cm-prn.yaml"]

    # pods: pinger (unprivileged) + one privileged pod per node, each with a
    # startup script that resolves its peer and runs click
    pods = files["pods.yaml"]
    assert "name: pinger" in pods and "ip route replace default" in pods
    for node in ("prn", "fw", "lim"):
        assert f"name: {node}" in pods
        assert "privileged: true" in pods
        assert "exec click /tmp/router.click" in pods
    assert pods.count("getent hosts") == 5  # pinger (1) + prn next (1) + fw prev/next (2) + lim prev (1)

    # deploy.sh applies in order and adds the pinger MASQUERADE exemption
    dep = files["deploy.sh"]
    assert "kubectl apply -f services.yaml" in dep
    assert "cm-*.yaml" in dep
    assert "kubectl apply -f pods.yaml" in dep
    assert "MASQUERADE" in dep or "iptables -t nat -I POSTROUTING" in dep


TESTS = [test_invalid_chain, test_gen_config_shapes, test_gen_k8s]


def main() -> int:
    import sys

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

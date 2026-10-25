"""Generate Click configs + a docker-compose topology from a resolved Spec.

Topology: for an n-node chain (n >= 2), containers are wired on networks:
    net-in   : pinger (client)      <-> node0 eth0
    edge_i   : node_i eth1          <-> node_{i+1} eth0   (i = 0..n-2)
    net-out  : node_{n-1} eth1      <-> sink (client)

Addressing:
- every node eth0 carries the node's spec IP — the spec's `ip` is the node's
  ingress address, and its /24 defines the network it sits on:
  net-in = node0's /24, edge_i = node_{i+1}'s /24.
- eth1 addresses are synthesized: edge_i assigns .250 to the upstream host
  (node_i's eth1). net-out is a fixed reserved subnet (EGRESS_NET/24): the
  last node's eth1 = .250, the sink = .254 — so it never overlaps the chain's
  /24s.
- clients: pinger = net-in .254; its default gw = node0's spec IP.
- all MACs derive from the IP (02:00:xx:xx:xx:xx), so Click configs and the
  compose file agree with no registry. Requires spec IPs in distinct /24s
  (synthesized .250 would collide otherwise); chain must fit one octet.

Click config per node is symmetric (one chain per direction), classified at
the ether level, IP header checked, function elements applied on the push
path, a Queue bridging push->pull (shaper elements are pull-side), and
EtherEncap rewrites MACs toward the next hop: the upstream frame arrives with
the upstream host's stale destination MAC, and real stacks (the sink) drop
frames not addressed to them.
"""

# TODO: bsena; Change codegen to use k8s instead of raw docker compose
# also split k8s creation from click creation

import yaml

from dataclasses import dataclass

from functions import FunctionName
from spec import Node, Spec

SYNTH_HOST = "250"   # upstream host of an edge (previous node's eth1)
CLIENT_HOST = "254"  # pinger on net-in, sink on net-out
GATEWAY_HOST = "253" # docker bridge gateway on every generated network
EGRESS_NET = "10.254.254"  # fixed net-out /24 base
RESERVED_HOSTS = {SYNTH_HOST, CLIENT_HOST, GATEWAY_HOST}


def _mac(ip: str) -> str:
    return "02:00:" + ":".join(f"{int(b):02x}" for b in ip.split("."))


class CodegenError(ValueError):
    pass


@dataclass(frozen=True)
class Iface:
    net: str
    ip: str
    mac: str


@dataclass(frozen=True)
class NodePlan:
    node: Node
    eth0: Iface
    eth1: Iface


@dataclass(frozen=True)
class Plan:
    nodes: list[NodePlan]
    nets: dict[str, str]  # network name -> "x.x.x.0/24"
    pinger: Iface
    sink: Iface


def _subnet(ip: str) -> str:
    return ip.rsplit(".", 1)[0] + ".0/24"


def plan_topology(spec: Spec) -> Plan:
    n = len(spec.nodes)
    if n > 250:
        raise CodegenError("chain too long: synthesized octets need n < 250")
    if len({_subnet(node.ip) for node in spec.nodes}) != n:
        raise CodegenError("every node spec IP must sit in a distinct /24")
    if any(node.ip.startswith(EGRESS_NET + ".") for node in spec.nodes):
        raise CodegenError(f"spec IPs must not overlap the reserved {EGRESS_NET}.0/24")
    for node in spec.nodes:
        host = node.ip.rsplit(".", 1)[1]
        if host in RESERVED_HOSTS:
            raise CodegenError(f"spec ip {node.ip}: host octet .{host} is reserved")

    nets = {"net-in": _subnet(spec.nodes[0].ip), "net-out": f"{EGRESS_NET}.0/24"}
    nodes_plan: list[NodePlan] = []

    for i, node in enumerate(spec.nodes):
        in_net = "net-in" if i == 0 else f"edge{i-1}"
        out_net = f"edge{i}" if i < n - 1 else "net-out"
        # edge_i's subnet = the downstream node's /24; its .250 is this node's eth1.
        eth0_ip = node.ip
        out_base = spec.nodes[i + 1].ip.rsplit(".", 1)[0] if i < n - 1 else EGRESS_NET
        eth1_ip = f"{out_base}.{SYNTH_HOST}"
        if i < n - 1:
            nets[f"edge{i}"] = _subnet(spec.nodes[i + 1].ip)
        nodes_plan.append(
            NodePlan(
                node=node,
                eth0=Iface(in_net, eth0_ip, _mac(eth0_ip)),
                eth1=Iface(out_net, eth1_ip, _mac(eth1_ip)),
            )
        )

    p_base = spec.nodes[0].ip.rsplit(".", 1)[0]
    pinger = Iface("net-in", f"{p_base}.{CLIENT_HOST}", _mac(f"{p_base}.{CLIENT_HOST}"))
    sink = Iface("net-out", f"{EGRESS_NET}.{CLIENT_HOST}", _mac(f"{EGRESS_NET}.{CLIENT_HOST}"))

    # sanity: an edge's subnet must not be its own client's subnet host
    return Plan(nodes=nodes_plan, nets=nets, pinger=pinger, sink=sink)


def _pull_chain(node: Node) -> list[str]:
    """Queue/shaper elements on the pull side, after the push->pull Queue."""
    parts: list[str] = []
    delay = getattr(node.setup, "delay_ms", None)
    rate = getattr(node.setup, "rate_mbps", None)
    if delay is not None:
        # DelayUnqueue pulls from the Queue, delays, then pushes out; a second
        # (unbounded) Queue bridges its push output back to the pull side.
        parts += [f"DelayUnqueue({delay:g}ms)", "Queue"]
    if rate is not None:
        parts.append(f"BandwidthShaper({rate:g}Mbps)")
    return parts


def _function_line(node: Node) -> str | None:
    """Function element applied on the push path, after CheckIPHeader."""
    setup = node.setup
    match node.function:
        case FunctionName.PRINTER:
            return f"Print('{node.name}: packet seen')"
        case FunctionName.DROP:
            if node.setup.percent is not None:
                return f"RandomSample(DROP {setup.percent / 100:g})"
            return "Discard()"
        case FunctionName.FIREWALL:
            rule = setup.proto.value
            if setup.port is not None:
                rule += f" dst port {setup.port}"
            return f"IPFilter(drop {rule}, allow)"
        case _:
            return None  # forwarder; queue: shaping lives on the pull side


def gen_node_config(plan: Plan, i: int) -> str:
    """Full click config text for plan.nodes[i]."""
    np = plan.nodes[i]
    prv = plan.nodes[i - 1] if i > 0 else None
    nxt = plan.nodes[i + 1] if i + 1 < len(plan.nodes) else None
    node = np.node
    fn = _function_line(node)

    pull = [f"Queue({s})" if (s := getattr(node.setup, "size_pkts", None)) is not None else "Queue"]
    pull += _pull_chain(node)

    lines = [
        f"// {np.node.name} — auto-generated. function: {node.function.value}.",
        f"// {np.eth0.net} ({np.eth0.ip} = spec ip) -> {np.eth1.net} ({np.eth1.ip}, synthesized).",
        "",
    ]
    for dir_label, in_dev, out_dev, src_if, dst_mac in (
        ("out (toward internet)", "eth0", "eth1", np.eth1, nxt.eth0.mac if nxt else plan.sink.mac),
        ("in (toward ingress)", "eth1", "eth0", np.eth0, prv.eth1.mac if prv else plan.pinger.mac),
    ):
        chain = [
            f"FromDevice({in_dev})",
            "Classifier(12/0800)",
            "Strip(14)",
            "CheckIPHeader()",
        ]
        if fn:
            chain.append(fn)
        chain.append(f"EtherEncap(0x0800, {src_if.mac}, {dst_mac})")
        chain += pull
        chain.append(f"ToDevice({out_dev})")
        lines.append(f"// {dir_label}")
        lines.append("\n  -> ".join(chain) + ";")
        lines.append("")
    return "\n".join(lines)


def gen_config(spec: Spec) -> dict[str, str]:
    """node name -> full click config text."""
    plan = plan_topology(spec)
    return {np.node.name: gen_node_config(plan, i) for i, np in enumerate(plan.nodes)}


def gen_compose(spec: Spec, image: str = "click") -> str:
    """docker-compose.yml text wiring the chain for real-instance validation."""
    plan = plan_topology(spec)
    if len(plan.nodes) < 2:
        raise CodegenError("compose topology needs >= 2 nodes (net-in and net-out must differ)")

    def node_service(np: NodePlan) -> dict:
        # Docker numbers container interfaces by alphabetical network name, not
        # by this spec's order — so the click config's eth0/eth1 are pinned per
        # run: rename both interfaces to their final names by their
        # deterministic MAC before starting click.
        rename = (
            "a() { for i in /sys/class/net/*/address; do [ \"$(cat \"$i\")\" = \"$1\" ] && echo \"${i%/address}\" && break; done; }; "
            "A=$(a \"$MP_IN_MAC\"); B=$(a \"$MP_OUT_MAC\"); A=${A##*/}; B=${B##*/}; "
            "ip link set dev \"$A\" down; ip link set dev \"$A\" name tmpa; "
            "ip link set dev \"$B\" down; ip link set dev \"$B\" name tmpb; "
            "ip link set dev tmpa name eth0; ip link set dev tmpb name eth1; "
            "ip link set dev eth0 up; ip link set dev eth1 up; "
            "exec click /etc/click/router.click"
        )
        # compose interpolates $ in strings; escape to pass them through
        rename = rename.replace("$", "$$")
        return {
            "image": image,
            "entrypoint": [],
            "cap_add": ["NET_ADMIN", "NET_RAW"],
            "sysctls": {"net.ipv4.ip_forward": "0"},
            "volumes": [f"./{np.node.name}.click:/etc/click/router.click:ro"],
            "environment": {"MP_IN_MAC": np.eth0.mac, "MP_OUT_MAC": np.eth1.mac},
            "command": ["sh", "-c", rename],
            "networks": {
                np.eth0.net: {"ipv4_address": np.eth0.ip, "mac_address": np.eth0.mac},
                np.eth1.net: {"ipv4_address": np.eth1.ip, "mac_address": np.eth1.mac},
            },
        }

    compose = {
        "services": {
            "pinger": {
                "image": image,
                "entrypoint": [],
                "command": [
                    "sh", "-c",
                    f"ip route replace default via {plan.nodes[0].eth0.ip} && sleep infinity",
                ],
                "cap_add": ["NET_ADMIN"],
                "networks": {
                    plan.pinger.net: {"ipv4_address": plan.pinger.ip, "mac_address": plan.pinger.mac},
                },
            },
            "sink": {
                "image": image,
                "entrypoint": [],
                "command": [
                    "sh", "-c",
                    f"ip route replace default via {plan.nodes[-1].eth1.ip} && sleep infinity",
                ],
                "cap_add": ["NET_ADMIN"],
                "networks": {
                    plan.sink.net: {"ipv4_address": plan.sink.ip, "mac_address": plan.sink.mac},
                },
            },
        },
        "networks": {
            net: {"ipam": {"config": [{"subnet": subnet, "gateway": f"{subnet.rsplit('.', 1)[0]}.{GATEWAY_HOST}"}]}}
            for net, subnet in plan.nets.items()
        },
    }
    for np in plan.nodes:
        compose["services"][np.node.name] = node_service(np)
    return yaml.safe_dump(compose, default_flow_style=False, sort_keys=False)

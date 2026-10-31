"""Generate Click configs + k8s manifests from a resolved Spec.

Topology: an ordered chain of n >= 2 nodes on the pod network. Each node is one
pod with a single `eth0`; peers are found by DNS (a headless Service per node
name) and neighbor MACs by ARP, so nothing is pinned. pinger sits in front of
node0; the last node is the egress and NATs to the real internet.

Data plane: node i forwards to node i+1 by IP-in-IP encapsulation. Each node
decapsulates, applies its function, and re-encapsulates (the egress instead
NATs to the internet). Traffic is generic IPv4 and flows both ways; the
function applies in both directions. The egress NAT is protocol-generic:
TCP/UDP via IPRewriter, ICMP echo via ICMPPingRewriter.

Addressing: every config carries placeholders (`__MY_IP__`, `__MY_MAC__`,
`__PREV_IP__`, `__NEXT_IP__`, ...) that the pod's startup script fills from the
Downward API + sysfs + DNS + `ip route` before running click.
"""

from functions import DropSetup, FirewallSetup, FunctionName
from spec import Node, Spec


class CodegenError(ValueError):
    pass


# TODO: bsena; Undestand this codegen so you dont be a dumb piece of shit

# ---- function + shaping elements (shared with the old compose codegen) ----


def _function_line(node: Node) -> str | None:
    """Function element applied to the (inner) packet, or None for a forwarder."""
    setup = node.setup
    match node.function:
        case FunctionName.PRINTER:
            return f"Print('{node.name}: packet seen')"
        case FunctionName.DROP:
            assert isinstance(setup, DropSetup), "drop node needs DropSetup"
            return f"RandomSample(DROP {setup.percent / 100:g})"
        case FunctionName.FIREWALL:
            assert isinstance(setup, FirewallSetup), "firewall node needs FirewallSetup"
            rule = setup.proto.value
            if setup.port is not None:
                rule += f" dst port {setup.port}"
            return f"IPFilter(drop {rule}, allow)"
        case _:
            return None  # forwarder / queue: shaping lives on the pull side


def _pull_chain(node: Node) -> list[str]:
    """Queue/shaper elements on the pull side, after the push->pull Queue."""
    parts: list[str] = []
    delay = getattr(node.setup, "delay_ms", None)
    rate = getattr(node.setup, "rate_mbps", None)
    if delay is not None:
        # DelayUnqueue pulls, delays, then pushes; a second Queue bridges back.
        parts += [f"DelayUnqueue({delay:g}ms)", "Queue"]
    if rate is not None:
        parts.append(f"BandwidthShaper({rate:g}Mbps)")
    return parts


def _pull(node: Node) -> list[str]:
    size = getattr(node.setup, "size_pkts", None)
    return [f"Queue({size})" if size is not None else "Queue"] + _pull_chain(node)


# ---- click config generation ----


def gen_node_config(spec: Spec, i: int) -> str:
    """Full click config text for spec.nodes[i]."""
    node = spec.nodes[i]
    n = len(spec.nodes)
    fn = _function_line(node)
    fn_lines = [fn] if fn else []

    is_ingress = i == 0
    is_egress = i == n - 1

    L: list[str] = []
    role = "ingress" if is_ingress else ("egress" if is_egress else "relay")
    L.append(f"// {node.name} — {role}. One pod interface (eth0). function: {node.function.value}.")
    L.append("")
    L.append("FromDevice(eth0)")
    L.append("  -> mac :: Classifier(0/__MY_MACHEX__, -);")
    L.append("mac[0]")
    L.append("  -> c :: Classifier(12/0806 20/0001,   // ARP request")
    L.append("                     12/0806 20/0002,   // ARP reply")
    L.append("                     12/0800);          // IPv4")
    L.append("mac[1] -> Discard;")
    L.append("")
    L.append("c[0] -> Discard;                         // kernel answers ARP")
    L.append("c[1] -> [1]arpq :: ARPQuerier(__MY_IP__, __MY_MAC__);")

    if is_ingress:
        L.append("c[2] -> Strip(14)")
        L.append("     -> CheckIPHeader")
        L.append("     -> ic :: IPClassifier(ip proto 4, -);")
        L.append("")
        L.append("// return: IP-in-IP from next -> strip outer -> inner reply to pinger")
        L.append("ic[0]")
        L.append("  -> StripIPHeader")
        L.append("  -> CheckIPHeader")
        L += [f"  -> {x}" for x in fn_lines]
        L.append("  -> arpq;")
        L.append("")
        L.append("// forward: any IPv4 from pinger -> function -> encapsulate to next")
        L.append("ic[1]")
        L += [f"  -> {x}" for x in fn_lines]
        L.append("  -> IPEncap(4, __MY_IP__, __NEXT_IP__)")
        L.append("  -> arpq;")

    elif is_egress:
        L.append("c[2] -> Strip(14)")
        L.append("     -> CheckIPHeader")
        L.append("     -> ic :: IPClassifier(ip proto 4, -);")
        L.append("")
        L.append("// stateful NAT: rewrite src -> __MY_IP__. TCP/UDP via IPRewriter, ICMP")
        L.append("// echo via ICMPPingRewriter.")
        L.append("iprw :: IPRewriter(pattern __MY_IP__ 50000-65535 - - 0 1);")
        L.append("icmprw :: ICMPPingRewriter(pattern __MY_IP__ 50000-65535 - - 0 1);")
        L.append("np :: IPClassifier(tcp or udp, icmp, -);")
        L.append("ck :: IPClassifier(tcp, udp, -);")
        L.append("")
        L.append("// route lookup: local pod subnet -> direct, everything else -> gateway.")
        L.append("rt :: RadixIPLookup(__MY_SUBNET__ 0, 0.0.0.0/0 __MY_GW__ 0);")
        L.append("")
        L.append("// forward: IP-in-IP from prev -> strip outer -> function -> NAT -> internet")
        L.append("ic[0]")
        L.append("  -> StripIPHeader")
        L.append("  -> CheckIPHeader")
        L += [f"  -> {x}" for x in fn_lines]
        L.append("  -> np;")
        L.append("")
        L.append("// return: reply from internet -> reverse-NAT -> function -> encap -> prev")
        L.append("ic[1]")
        L.append("  -> np;")
        L.append("")
        L.append("np[0] -> [0]iprw;")
        L.append("np[1] -> [0]icmprw;")
        L.append("np[2] -> Discard;")
        L.append("")
        L.append("// recompute the L4 checksum after NAT: the pod's TX checksum offload")
        L.append("// leaves a placeholder the veth never fills, so conntrack would drop it.")
        L.append("iprw[0] -> ck;")
        L.append("ck[0] -> SetTCPChecksum -> rt;")
        L.append("ck[1] -> SetUDPChecksum -> rt;")
        L.append("ck[2] -> rt;")
        L.append("iprw[1]")
        L += [f"  -> {x}" for x in fn_lines]
        L.append("  -> IPEncap(4, __MY_IP__, __PREV_IP__)")
        L.append("  -> rt;")
        L.append("icmprw[0] -> rt;")
        L.append("icmprw[1]")
        L += [f"  -> {x}" for x in fn_lines]
        L.append("  -> IPEncap(4, __MY_IP__, __PREV_IP__)")
        L.append("  -> rt;")
        L.append("")
        L.append("rt[0] -> arpq;")
        L.append("rt[1] -> Discard;")

    else:
        # relay: distinguish the two IP-in-IP neighbors by their outer source IP
        L.append("c[2] -> Strip(14)")
        L.append("     -> CheckIPHeader")
        L.append("     -> ic :: IPClassifier(ip proto 4 and src __PREV_IP__,")
        L.append("                          ip proto 4 and src __NEXT_IP__,")
        L.append("                          -);")
        L.append("")
        L.append("// forward: from prev -> strip outer -> function -> re-encap to next")
        L.append("ic[0]")
        L.append("  -> StripIPHeader")
        L.append("  -> CheckIPHeader")
        L += [f"  -> {x}" for x in fn_lines]
        L.append("  -> IPEncap(4, __MY_IP__, __NEXT_IP__)")
        L.append("  -> arpq;")
        L.append("")
        L.append("// return: from next -> strip outer -> function -> re-encap to prev")
        L.append("ic[1]")
        L.append("  -> StripIPHeader")
        L.append("  -> CheckIPHeader")
        L += [f"  -> {x}" for x in fn_lines]
        L.append("  -> IPEncap(4, __MY_IP__, __PREV_IP__)")
        L.append("  -> arpq;")
        L.append("ic[2] -> Discard;")

    pull = _pull(node)
    L.append("")
    L.append(" -> ".join(["arpq"] + pull + ["ToDevice(eth0)"]) + ";")
    return "\n".join(L)


def gen_config(spec: Spec) -> dict[str, str]:
    """node name -> full click config text."""
    return {node.name: gen_node_config(spec, i) for i, node in enumerate(spec.nodes)}


# ---- k8s manifest generation ----


def _startup(spec: Spec, i: int) -> str:
    """Shell startup script for spec.nodes[i]: resolve peers, sed, run click."""
    n = len(spec.nodes)
    is_ingress = i == 0
    is_egress = i == n - 1
    needs_prev = not is_ingress
    needs_next = not is_egress

    lines = ["set -e"]
    if needs_next:
        nxt = spec.nodes[i + 1].name
        lines += [
            "for i in $(seq 1 60); do",
            f"  NEXT_IP=$(getent hosts {nxt} | awk 'NR==1 {{print $1}}')",
            "  [ -n \"$NEXT_IP\" ] && break",
            "  sleep 1",
            "done",
            f"[ -n \"$NEXT_IP\" ] || {{ echo \"{nxt} not resolvable\" >&2; exit 1; }}",
        ]
    if needs_prev:
        prv = spec.nodes[i - 1].name
        lines += [
            "for i in $(seq 1 60); do",
            f"  PREV_IP=$(getent hosts {prv} | awk 'NR==1 {{print $1}}')",
            "  [ -n \"$PREV_IP\" ] && break",
            "  sleep 1",
            "done",
            f"[ -n \"$PREV_IP\" ] || {{ echo \"{prv} not resolvable\" >&2; exit 1; }}",
        ]
    lines += [
        "echo 0 > /proc/sys/net/ipv4/conf/all/send_redirects 2>/dev/null || true",
        "echo 0 > /proc/sys/net/ipv4/conf/eth0/send_redirects 2>/dev/null || true",
        "MAC=$(cat /sys/class/net/eth0/address)",
        "MACHEX=$(tr -d ':' < /sys/class/net/eth0/address)",
    ]
    sed = ["sed"]
    sed += ['-e "s/__MY_IP__/${POD_IP}/g"']
    sed += ['-e "s/__MY_MAC__/${MAC}/g"']
    sed += ['-e "s/__MY_MACHEX__/${MACHEX}/g"']
    if needs_next:
        sed += ['-e "s/__NEXT_IP__/${NEXT_IP}/g"']
    if needs_prev:
        sed += ['-e "s/__PREV_IP__/${PREV_IP}/g"']
    if is_egress:
        lines += [
            "GW=$(ip route | awk '/default/ {print $3; exit}')",
            "SUBNET=$(ip route | awk '/scope link/ {print $1; exit}')",
        ]
        sed += ['-e "s/__MY_GW__/${GW}/g"']
        sed += ['-e "s|__MY_SUBNET__|${SUBNET}|g"']
    sed += ["/etc/click/router.click > /tmp/router.click"]
    lines.append(" \\\n    ".join(sed))
    lines.append("exec click /tmp/router.click")
    return "\n".join(lines)


def gen_k8s(spec: Spec, image: str = "click") -> dict[str, str]:
    """k8s manifests: services, per-node ConfigMaps, pods, deploy.sh."""
    if len(spec.nodes) < 2:
        raise CodegenError("k8s chain needs >= 2 nodes (ingress and egress must differ)")

    files: dict[str, str] = {}
    cfgs = gen_config(spec)

    # headless Services (one per node + pinger), so names resolve to pod IPs
    svcs = []
    for node in spec.nodes:
        svcs.append(
            f"""apiVersion: v1
kind: Service
metadata:
  name: {node.name}
  labels:
    app: {node.name}
spec:
  clusterIP: None
  publishNotReadyAddresses: true
  selector:
    app: {node.name}"""
        )
    svcs.append(
        """apiVersion: v1
kind: Service
metadata:
  name: pinger
  labels:
    app: pinger
spec:
  clusterIP: None
  publishNotReadyAddresses: true
  selector:
    app: pinger"""
    )
    files["services.yaml"] = "\n---\n".join(svcs) + "\n"

    # ConfigMap per node
    for node in spec.nodes:
        body = chr(10).join("    " + ln if ln else "" for ln in cfgs[node.name].splitlines())
        files[f"cm-{node.name}.yaml"] = f"""apiVersion: v1
kind: ConfigMap
metadata:
  name: {node.name}-config
  labels:
    app: {node.name}
data:
  router.click: |
{body}
"""

    # pods: pinger + one per node
    pods = []

    def pod(name: str, app: str, script: str, privileged: bool, needs_cfg: str | None) -> str:
        sec = "        privileged: true\n" if privileged else (
            "        capabilities:\n          add: [NET_ADMIN]\n"
        )
        env = ""
        vols = ""
        mounts = ""
        if needs_cfg:
            env = """      env:
        - name: POD_IP
          valueFrom:
            fieldRef:
              fieldPath: status.podIP
"""
            mounts = """      volumeMounts:
        - name: cfg
          mountPath: /etc/click
          readOnly: true
"""
            vols = f"""  volumes:
    - name: cfg
      configMap:
        name: {needs_cfg}
"""
        return f"""apiVersion: v1
kind: Pod
metadata:
  name: {name}
  labels:
    app: {app}
spec:
  containers:
    - name: {name}
      image: {image}
      imagePullPolicy: IfNotPresent
      securityContext:
{sec}{env}{mounts}      command:
        - sh
        - -c
        - |
{chr(10).join('          ' + ln for ln in script.splitlines())}
{vols}"""

    pods.append(
        pod(
            "pinger",
            "pinger",
            "\n".join(
                [
                    "set -e",
                    "for i in $(seq 1 60); do",
                    f"  NODE0_IP=$(getent hosts {spec.nodes[0].name} | awk 'NR==1 {{print $1}}')",
                    "  [ -n \"$NODE0_IP\" ] && break",
                    "  sleep 1",
                    "done",
                    f"[ -n \"$NODE0_IP\" ] || {{ echo \"{spec.nodes[0].name} not resolvable\" >&2; exit 1; }}",
                    "ip route replace default via \"$NODE0_IP\"",
                    "exec sleep infinity",
                ]
            ),
            privileged=False,
            needs_cfg=None,
        )
    )
    for i, node in enumerate(spec.nodes):
        pods.append(pod(node.name, node.name, _startup(spec, i), privileged=True, needs_cfg=f"{node.name}-config"))

    files["pods.yaml"] = "\n---\n".join(pods) + "\n"

    # deploy.sh: ordered apply + pinger MASQUERADE exemption (single NAT at egress)
    files["deploy.sh"] = """#!/usr/bin/env bash
# Deploy the generated chain: headless Services -> ConfigMaps -> pods, then
# exempt the pinger from CNI MASQUERADE so its source IP survives to the egress
# node (single NAT at the egress; a double NAT would send replies to the gateway).
set -euo pipefail
cd "$(dirname "$0")"

kubectl apply -f services.yaml
for f in cm-*.yaml; do kubectl apply -f "$f"; done
kubectl apply -f pods.yaml

for pod in %(podnames)s; do
  kubectl wait --for=condition=Ready pod/$pod --timeout=120s
done

PINGER_IP=$(kubectl get pod pinger -o jsonpath='{.status.podIP}')
minikube ssh -- "sudo iptables -t nat -I POSTROUTING 1 -s $PINGER_IP/32 -j ACCEPT"

kubectl get pod -o wide
echo
echo "Test:  kubectl exec pinger -- ping -c 3 8.8.8.8"
echo "Cleanup:"
echo "  kubectl delete -f pods.yaml -f cm-*.yaml -f services.yaml"
echo "  minikube ssh -- sudo iptables -t nat -D POSTROUTING -s $PINGER_IP/32 -j ACCEPT"
""" % {"podnames": " ".join(node.name for node in spec.nodes)}

    return files

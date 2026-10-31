# click-chain on k8s (dp/k8s/) — findings and stopping point

Task: deploy the docker-compose topology (click1 + click2 + pinger,
dp/docker-compose.yml) on kubernetes. Files: `dp/k8s/cm-click1.yaml`,
`dp/k8s/cm-click2.yaml` (verbatim click configs), `dp/k8s/click-chain.yaml`
(the pod).

## K8s-native rewrite (current state) — separate pods + DNS + ARPQuerier

The netns single-pod approach below works but pins MACs/IPs. Replaced by the
k8s-idiomatic version: three pods (`pinger`/`click1`/`click2`) on the pod
network, headless Services (`click-1`/`click-2`) for DNS, and click configs
that pin nothing — each pod resolves its peer's IP from DNS and its own IP/MAC
from the Downward API + `/sys/class/net/eth0/address` at startup, then `sed`s
them into a config template. `ARPQuerier(IP, MAC)` resolves neighbor MACs
dynamically (no `EtherEncap`). **Working: `kubectl exec pinger -- ping -c 3
10.244.99.99` → 3/3**, clean `echo from pinger` / `encapsulated to click2` /
`ipip from click1` / `fake pong` / `reply to pinger` in the logs, no loop.
Files: `services.yaml`, `cm-click1.yaml`, `cm-click2.yaml`, `pods.yaml`,
`deploy.sh`.

New findings (each cost deploy cycles):

13. **`ARPQuerier` signature is `ARPQuerier(IP, MAC)`, not `(DEVNAME, IP)`.**
    Click 2.1 has no device arg and no MAC default — passing `(eth0, IP)` fails
    `ETH: parse error`. The MAC must be read at runtime (`sysfs`) and passed in.
    A bare `ARPQuerier(...)` parsed clean with `-q`; the error only surfaced in
    a fully-wired config, so test with the real config, not a parse stub.

14. **`FromDevice` is promiscuous on the shared pod L2** — click1 and click2
    capture each other's frames and re-encapsulate them, an escalating loop
    (packet grows ~48 B/cycle). Fix: a leading `Classifier(0/<MYMACHEX>, -)` so
    each pod only handles frames addressed to its own MAC.

15. **The CNI MASQUERADEs any destination outside the pod CIDR.** A virtual
    inner address like `10.99.99.99` gets SNAT'd to the gateway (`10.244.0.1`),
    so the fake-pong reply is addressed to the gateway and pinger sees nothing.
    The inner tunnel address must sit inside `10.244.0.0/16` (e.g.
    `10.244.99.99`).

16. **The pod kernel fights the data path in two ways.** (a) It replies ICMP
    "protocol 4 unreachable" to IP-in-IP traffic — click1 must only forward
    ICMP echo requests (`IPClassifier(icmp type echo, -)`), else that error is
    re-encapsulated into a loop. (b) It sends ICMP redirects that rewrite the
    client's route. Disabling `send_redirects` needs `/proc/sys`, which is
    **read-only in a non-privileged container**, so click1/click2 run
    `privileged: true`.

## Internet egress (real NAT) — validated

click2 stops faking the pong and NATs inner traffic to the real internet:
`ICMPPingRewriter` (stateful ICMP NAT) + `RadixIPLookup` (gateway routing) +
`ARPQuerier`. **Working: `ping -c 3 8.8.8.8` → 3/3 real replies** (ttl 113,
~64ms), pinger exempt from CNI MASQUERADE.

17. **`IPRewriter` does not handle ICMP echo** — it silently drops the packet
    (no output). Use `ICMPPingRewriter` for ICMP echo NAT.

18. **Click 2.1 `ICMPPingRewriter` uses the Rewriter input-spec syntax, not
    `(NEWSRC, NEWDST)`.** Correct form: `ICMPPingRewriter(pattern SADDR SPORT
    DADDR DPORT FOUTPUT ROUTPUT)` with a *single* input — feed both forward
    echo requests and replies to `[0]`, and split them by output (0 = forward,
    1 = reply). `(IP, -)` parsed clean standalone but failed once wired
    (`input spec 0: unknown specification`).

19. **`ARPQuerier` ARPs the destination IP directly — it does not route via the
    gateway.** For internet egress, insert `RadixIPLookup` before it so
    non-local destinations get the gateway as the ARP target. Syntax is
    `RadixIPLookup(PREFIX [GATEWAY] OUTPUT, ...)` — each entry needs a trailing
    output port (e.g. `10.244.0.0/16 0, 0.0.0.0/0 10.244.0.1 0`). Subnet +
    gateway are read from `ip route` at pod start (placeholders `__CLICK2_GW__`,
    `__CLICK2_SUBNET__`). `LookupIPRoute` (kernel route table) is NOT in this
    build.

20. **Internet egress needs the pinger exempt from CNI MASQUERADE.** Otherwise
    the echo's source is SNAT'd to `10.244.0.1`, click2 reverse-NATs the reply
    to the gateway (double NAT), and pinger sees nothing. The node needs one
    rule: `iptables -t nat -I POSTROUTING -s <pinger-ip>/32 -j ACCEPT`
    (`deploy.sh` adds it). This is a node firewall change — required for a clean
    single NAT at click2.

## Generic traffic (TCP/UDP + ICMP) — validated

The codegen egress NAT was made protocol-generic: `IPRewriter` for TCP/UDP +
`ICMPPingRewriter` for ICMP echo, split by `IPClassifier(tcp or udp, icmp, -)`.
**Working end-to-end on k8s: ping 2/2, `bash /dev/tcp/8.8.8.8/53` connects,
and a raw DNS query to 8.8.8.8 returns google.com's A record.** (Verified
with `ip_forward=0` so click is the only path — see 23.)

21. **`IPRewriter` pattern syntax is the same 6-field Rewriter form as
    `ICMPPingRewriter`:** `IPRewriter(pattern SADDR SPORT DADDR DPORT FOUTPUT
    ROUTPUT)`. One element handles both TCP and UDP (the flow ID carries the
    protocol). `IPClassifier(tcp or udp, icmp, -)` is valid and routes by
    protocol.

22. **The pod's TX checksum offload breaks click-NAT'd TCP/UDP.** The pod
    kernel leaves a placeholder L4 checksum (veth never fills it), click's
    `IPRewriter` only *increments* it, so the NAT'd packet carries a wrong
    checksum and the host conntrack silently drops it (no conntrack entry, no
    MASQUERADE, no reply) — ICMP is unaffected because its checksum isn't
    offloaded. Fix: `SetTCPChecksum`/`SetUDPChecksum` on the IPRewriter forward
    output, split by a `IPClassifier(tcp, udp, -)`. Diagnosed by conntrack
    showing the NAT'd ICMP echo MASQUERADEd but no NAT'd TCP SYN entry, and a
    hand-verified checksum mismatch.

23. **Pods default to `ip_forward=1`**, so the kernel *also* forwards the
    pinger's traffic (a second, MASQUERADEd path) alongside click. This masked
    the broken click NAT during ICMP testing — ping "worked" via the kernel
    path even though click's NAT was failing. Set `ip_forward=0` to make click
    the only forwarder before asserting on the click path.

## Status: data path validated on k8s (netns approach, superseded)

- The pod topology was replicated end-to-end inside a privileged docker
  container (privileged init script + click1/click2 in their netns + ping from
  the pinger netns): **DATA-PATH-OK** (3/3 pings, fake pong visible in both
  click logs). This validated the init script including the MAC pinning.
- minikube (docker runtime, v1.35.1): pod now reaches `3/3 Running` and the
  e2e check passes — `kubectl exec click-chain -c pinger -- nsenter
  --net=/var/run/netns/pinger ping -c 3 10.10.1.12` → 3/3 replies, `echo from
  pinger` / `encapsulated to click2` / `fake pong` / `reply to pinger` all
  present in click1/click2 logs.

## Findings (each one cost a deploy cycle)

1. **Default CNI cannot express the compose topology.** One pod = one
   interface = one L2. The pod recreates the wiring itself: privileged init
   container creates netns (`pinger`, `c1`, `c2`) + veth pairs, and each click
   instance (`nsenter --net=/var/run/netns/<ns>`) runs pinned to its namespace.

2. **`ENTRYPOINT ["click"]` in the image means you cannot run plain shells.**
   `docker run click sh -c ...` passes args to click → `click: unrecognized
   option '-c'`. Every docker-based check must pass `--entrypoint sh`.

3. **`:latest` pod images default to `pullPolicy: Always`** — kubelet pulls
   from docker.io even when the image is loaded on the node. All four
   containers now set `imagePullPolicy: IfNotPresent` (image loaded via
   `minikube image load click`).

4. **docker bridge gateway takes `.1` of every subnet** — collides with node
   IPs that sit at .1 (spec ip 10.0.0.1 in mp/). Fixed in mp codegen by pinning
   `gateway: .253`; relevant anywhere docker networks are created for chains.

5. **Docker rejects overlapping subnets across networks** (`Pool overlaps with
   other one on this address space`), even for separate bridge networks — the
   mp codegen constraint "every spec IP in a distinct /24" exists because of
   this (verified empirically; default.yaml had to be renumbered).

6. **Docker numbers container interfaces `eth0/eth1` alphabetically by network
   name**, not by service-spec order. A click config saying `FromDevice(eth0)`
   silently binds to the WRONG network. Fix used in mp codegen: deterministic
   per-network MACs + a start-up `ip link set ... name` rename by MAC.

7. **The click configs' `EtherEncap` hardcodes MACs target-facing** —
   interfaces must carry those MACs. docker-compose pinned them via
   `mac_address`; the netns-based k8s translation must do `ip link set ...
   address` in the init script (initially forgotten → 100% packet loss: the
   data path worked, the fake pong was dropped by the pinger's NIC).

8. **`nsenter` into a netns needs `CAP_SYS_ADMIN`** (not `NET_ADMIN`) in the
   joining container, otherwise `nsenter: reassociate to namespace 'ns/net'
   failed: Invalid argument`. The three app containers now carry
   `SYS_ADMIN` + their original caps.

9. **netns files survive pod restarts.** The netns volume is a node `hostPath`
   (`/var/run/netns`); stale namespaces from a previous pod make
   `ip netns add` fail (`File exists`) and init crash-loop. The init script
   now deletes stale c1/c2/pinger namespaces first. (Note: k8s only allows
   `mountPropagation: Bidirectional` on hostPath — that is why the holder is a
   hostPath, which also means leftover state on the node; cleanup:
   `minikube ssh -- ip netns del pinger c1 c2` after deleting the pod.)

12. **`ip netns add` bind mounts do not survive the init container without
    `mountPropagation: Bidirectional`.** The init container creates the netns
    bind mounts in its own mount namespace; on init exit that namespace is torn
    down and the node's `/var/run/netns/c1|c2|pinger` are left as stale empty
    mode-000 files (netns object freed). App containers then hit
    `nsenter: reassociate to namespace 'ns/net' failed: Invalid argument`.
    Fix: `mountPropagation: Bidirectional` on the init container's netns
    `volumeMount` (host `/run` is `shared`, so the mounts propagate up and
    survive init exit). Also make the init cleanup `rm -f` the stale files —
    `ip netns del` alone leaves empty files that block the next `ip netns add`.
    (minikube `ssh` runs as non-root, so cleanup must happen in the privileged
    init script, not from `minikube ssh`.)

10. **heph truncates target logs to the tail** — test runners should print
    PASS/FAIL summary last on stdout (stderr prints first and disappear).
    Encoded in mp/test_spec.py's runner.

11. **`BandwidthShaper` rate is case-sensitive** (`Mbps`, not `mbps`) and
    pull-side only; `DelayUnqueue` is pull->push (needs a second Queue after
    it). Encoded in mp/codegen.py.

## What remains (next run)

1. ~~`kubectl exec -c pinger ... ping 10.10.1.12`~~ — done, 3/3 replies.
2. Cleanup script: `kubectl delete pod click-chain && minikube ssh -- ip netns
   del pinger c1 c2` — note minikube ssh is non-root; the pod's own init
   `rm -f` already reclaims stale files, but a node-level cleanup for real
   leftover netns mounts would need `sudo`/root on the node.
3. Optional parity: convert the mp codegen (chain generator) to emit k8s
   objects the same way it emits docker-compose (currently compose only).
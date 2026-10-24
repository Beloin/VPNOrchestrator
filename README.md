# SDN - Project

<!--toc:start-->
- [SDN - Project](#sdn-project)
  - [Tasks](#tasks)
    - [Track A — Click data plane (config by hand)](#track-a-click-data-plane-config-by-hand)
    - [Track B — Orchestrator (spec -> config)](#track-b-orchestrator-spec-config)
    - [Track C — Packaging + wiring](#track-c-packaging-wiring)
    - [Track D — VPN tunnel](#track-d-vpn-tunnel)
    - [Track E — Management plane (CLI only)](#track-e-management-plane-cli-only)
    - [Track F — MP ↔ Click connection](#track-f-mp-click-connection)
    - [Track G — Client (TUN) in Docker](#track-g-client-tun-in-docker)
    - [Integration checkpoints (test together)](#integration-checkpoints-test-together)
<!--toc:end-->

The idea will be create an orchestration of a SDN.

## Tasks

Concurrent workstreams. Each ships a small piece; integration checkpoints test them together.

### Track A — Click data plane (config by hand)

- [X] Pull/run Click in Docker; read `click --help` and the "Elements" wiki list inside a container.
- [X] Hand-write one chain: `FromDevice -> Classifier -> ... -> ToDevice`.
- [ ] Run it in one container, verify forwarding.

### Track B — Orchestrator (spec -> config)

- [ ] Define `spec.yaml` schema for one chain.
- [ ] `orchestrator.py` reads the spec, emits multiple Click configs (one per router) plus their K8s manifests.
- [ ] Client node (TUN) manifest needs `privileged` (or `securityContext` `NET_ADMIN` + `/dev/net/tun`); other routers stay unprivileged.
- [ ] Unit-test: generated config == hand-written config.

### Track C — Packaging + wiring

- [ ] One Dockerfile for Click. Orchestrator instantiates containers from it, each with its generated config.
- [ ] Orchestrator builds topology, wires containers (`docker run`, then Minikube/K8s).
- [ ] Orchestrator monitors router state.

### Track D — VPN tunnel

- [ ] GRE, no crypto: static tunnel, Click encapsulates.
- [ ] IPSec: control plane exchanges keys, pushes to Click ESP elements.
- [ ] Routes: "subnet X behind peer Y" (multi-site).

### Track E — Management plane (CLI only)

- [ ] CLI commands: "add peer", "show tunnels", deploy/monitor.
- [ ] No GUI, no web dashboard.

### Track F — MP ↔ Click connection

Two channels. Lifecycle via the K8s API; runtime control via Click's
`ControlSocket` element (TCP).

- [ ] Lifecycle channel: MP uses the K8s API to create/start/stop/restart a router pod, and `exec` inside it.
- [ ] Config delivery: MP writes the Click config to a K8s ConfigMap, mounts it into the pod (entrypoint runs `click /etc/click/router.click`).
- [ ] Runtime channel: add a `ControlSocket(TCP, ...)` to the Click config.
- [ ] MP writes handlers live (tune params) and hot-swaps the full config over the socket for bigger changes.
- [ ] MP reads handlers over the socket (counters, table state) for monitoring.
- [ ] Restart flow: spec change -> regenerate config -> K8s API recreates/restarts the pod.

### Track G — Client (TUN) in Docker

Client runs in a Docker Ubuntu container, so host tunnels/routes stay untouched.
A Dockerfile built from `ubuntu` configures the tunnel (`tun0` + routes); that
same image deploys into the Kubernetes cluster.

- [ ] Write a Dockerfile from `ubuntu` that sets up `tun0` and the routes.
- [ ] Run client in Docker Ubuntu; add `--cap-add NET_ADMIN` and `/dev/net/tun`.
- [ ] Inside the container: create `tun0`, route default into it.
- [ ] Click #1 reads `FromHost(tun0)`, GRE-encapsulates to Click #2.
- [ ] Click #2 decapsulates, NATs with `IPRewriter`, routes out to the internet.
- [ ] Verify: user -> tun0 -> Click #1 -> Click #2 -> internet.

### Integration checkpoints (test together)

- [ ] A + B: generated config deploys and forwards.
- [ ] A + B + C: two routers wired, traffic flows.
- [ ] + D: encrypted tunnel between two sites.
- [ ] + E: CLI drives the whole deploy -> monitor loop.
- [ ] + F: MP restarts a router and reconfigures it live over `ControlSocket`.
- [ ] + G: full path user -> TUN -> Click #1 -> Click #2 -> internet.


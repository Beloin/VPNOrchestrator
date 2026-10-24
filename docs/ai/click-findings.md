# Click data plane — findings

Notes from getting the two-node Click chain running in Docker. These are the
things that were not obvious and cost debugging time.

## Environment

- Click built from source (git `master`) in `dp/Dockerfile`, base `ubuntu:24.04`.
- Element reference lives at `/usr/local/share/click/elementmap.xml` inside the
  image. It lists element names and processing types.
- `click --help ELEMENT` only prints general usage — it does **not** show
  element config. The real config docs are in the `.hh` headers on GitHub
  (`kohler/click`, e.g. `elements/ip/ipencap.hh`). Fetch those when syntax is
  unclear.

## Click runs below the socket API

- `FromDevice`/`ToDevice` read and write raw ethernet frames (AF_PACKET). No
  TCP/IP stack, no sockets. Click is the forwarder.
- `FromDevice` is a **sniffer**: it gets a copy of each arriving frame while the
  kernel still processes the original. `ToDevice` only injects. Click is not in
  the host's path unless traffic is routed *into* it (a TUN device via
  `FromHost`/`ToHost`, or making Click the client's default gateway).

## Push/pull resolution

- `FromDevice` = push source. `ToDevice` = pull sink (`processing l/h`).
- A push-to-pull edge needs a `Queue` between them. Without it, Click fails with
  `agnostic 'X' in mixed context: push input 0, pull output 0`.
- Element processing types are in `elementmap.xml` (`processing="..."`).

## Element gotchas (this build)

- **No `GREEncap`.** Use `IPEncap(PROTO, SRC, DST)` for tunneling; IP-in-IP is
  protocol 4. Decapsulate with `StripIPHeader`.
- **`IPClassifier`/`IPFilter` need the IP header annotation set first**
  (`CheckIPHeader` or `MarkIPHeader`). Without it they silently drop every
  packet — no error, just no output.
- **`ICMPPingResponder()` takes no arguments.** It is IP-level, requires
  `CheckIPHeader` first, and fabricates the echo reply (the "fake pong"). Its
  reply TTL is 255; a kernel reply is TTL 64, which is how you tell them apart.
- `Classifier(12/0800)` matches EtherType IPv4 only; IPv6 (`12/86dd`) and ARP
  (`12/0806`) are dropped unless you add outputs for them.
- `Strip(14)` removes the ethernet header; `Strip(N)` removes N leading bytes.

## Docker / K8s

- `docker compose up -d` does **not** reload a bind-mounted config change — the
  container keeps the old process. Use `docker compose restart <svc>` (or
  `up -d --force-recreate`).
- To force *all* client traffic through a Click node, make Click the client's
  default gateway (`ip route replace default via <click-ip>`; needs `NET_ADMIN`).
- Then make the Click node's kernel passive so it doesn't leak traffic or send
  ICMP redirects: `net.ipv4.ip_forward=0` and `net.ipv4.icmp_echo_ignore_all=1`.
  With `ip_forward=1` the kernel also forwards to the real internet and sends
  "Redirect Host" back to the client.
- TUN inside a container needs `NET_ADMIN` + `/dev/net/tun`. In K8s that maps to
  a privileged pod (or `securityContext.capabilities` + a device plugin). Plain
  forwarding routers still need `NET_RAW` for raw sockets.
- Per-interface sysctls (`net.ipv4.conf.eth0.*`) cannot be set via compose
  `sysctls:` — that is rejected. Use non-interface sysctls there.

## Working demo (`dp/`)

- `docker-compose.yml`: pinger, click1 (two nets), click2, on `net-a` + `net-b`.
- Path: pinger (default route -> click1) -> `IPEncap` -> click2 ->
  `ICMPPingResponder` -> fake pong back to the pinger.
- Any IP pinged from the pinger gets a fabricated reply "from" that IP:

  ```sh
  docker compose exec pinger ping 8.8.8.8
  ```

- Two L2 networks are required for a meaningful two-node chain. On one bridge
  the ping reaches click2 directly and click1 has nothing to forward.

## Key elements used

| Element | Role |
|---|---|
| `FromDevice` / `ToDevice` | raw frame I/O (push / pull) |
| `Classifier` | match on raw byte offsets (EtherType) |
| `CheckIPHeader` | validate + set IP header annotation |
| `IPClassifier` | match IP fields (needs annotation) |
| `Strip` / `StripIPHeader` | remove headers |
| `IPEncap` | IP-in-IP tunnel |
| `ICMPPingResponder` | fabricate ICMP echo reply |
| `EtherEncap` | add ethernet header |
| `Queue` | push/pull boundary |
| `Print` | log packets to stderr |

// click2 — responder node. Decaps IP-in-IP from click1, fabricates the ICMP
// echo reply (ICMPPingResponder), tunnels it back.

// inbound: click1 -> IP-in-IP -> ICMP echo -> fake pong -> back
FromDevice(eth0)
  -> Classifier(12/0800)
  -> Print('click2 in: ipip from click1')
  -> Strip(14)
  -> CheckIPHeader
  -> IPClassifier(ip proto 4)
  -> StripIPHeader
  -> CheckIPHeader
  -> ICMPPingResponder()
  -> Print('click2 out: fake pong')
  -> IPEncap(4, 10.10.1.12, 10.10.0.11)
  -> EtherEncap(0x0800, 02:42:0a:0a:01:0c, 02:42:0a:0a:01:0b)
  -> Queue
  -> ToDevice(eth0);

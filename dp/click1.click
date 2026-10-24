// click1 — edge node. net-a (eth0) faces the pinger; net-b (eth1) tunnels to
// click2 via IP-in-IP (IPEncap). Forwards ICMP echo in, returns the reply.

// inbound: pinger -> IP-in-IP -> click2
FromDevice(eth0)
  -> Classifier(12/0800)
  -> Print('click1 in: echo from pinger')
  -> Strip(14)
  -> IPEncap(4, 10.10.0.11, 10.10.1.12)
  -> Print('click1 in: encapsulated to click2')
  -> EtherEncap(0x0800, 02:42:0a:0a:01:0b, 02:42:0a:0a:01:0c)
  -> Queue
  -> ToDevice(eth1);

// return: click2 -> IP-in-IP -> pinger
FromDevice(eth1)
  -> Classifier(12/0800)
  -> Strip(14)
  -> CheckIPHeader
  -> IPClassifier(ip proto 4)
  -> StripIPHeader
  -> CheckIPHeader
  -> Print('click1 out: reply to pinger')
  -> EtherEncap(0x0800, 02:42:0a:0a:00:0b, 02:42:0a:0a:00:0a)
  -> Queue
  -> ToDevice(eth0);

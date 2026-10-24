// Hand-written chain (Track A) — learn the element graph.
//   eth0 -> classify IPv4 -> drop ping -> queue -> eth1
//
// Rate limiting belongs here too, via a shaper (BandwidthShaper), not
// RatedSource: RatedSource *generates* packets, it is not a filter.

FromDevice(enp9s0)
  -> Classifier(12/0800)        // EtherType 0x0800 = IPv4
  -> IPFilter(drop icmp type 8)  // firewall: drop ping (ICMP echo request)
  -> Print(forwarded)
  -> Discard;

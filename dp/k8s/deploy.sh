#!/usr/bin/env bash
# Deploy the k8s-native click chain: headless Services -> ConfigMaps -> pods.
# Apply order matters: Services must exist before the pods' startup scripts
# resolve click-1/click-2, and ConfigMaps before kubelet mounts them.
#
# The egress node (click2) NATs inner traffic to the real internet. That needs
# the pinger's traffic to keep its real source IP (single NAT at click2), but
# the CNI MASQUERADEs any destination outside the pod CIDR. So we exempt the
# pinger from MASQUERADE on the node (one iptables rule).
set -euo pipefail
cd "$(dirname "$0")"

kubectl apply -f services.yaml
kubectl apply -f cm-click1.yaml -f cm-click2.yaml
kubectl apply -f pods.yaml

kubectl wait --for=condition=Ready pod/click1 pod/click2 pod/pinger --timeout=120s

PINGER_IP=$(kubectl get pod pinger -o jsonpath='{.status.podIP}')
# exempt pinger egress from CNI MASQUERADE so its source IP survives to click2
minikube ssh -- "sudo iptables -t nat -I POSTROUTING 1 -s $PINGER_IP/32 -j ACCEPT"

kubectl get pod -o wide

echo
echo "Test (pinger default route -> click1 -> tunnel -> click2 NAT -> internet):"
echo "  kubectl exec pinger -- ping -c 3 8.8.8.8"
echo "  kubectl logs click1 | grep -E 'echo|encapsulated|reply'"
echo "  kubectl logs click2 | grep -E 'ipip|internet|tunnel'"
echo "Cleanup:"
echo "  kubectl delete -f pods.yaml -f cm-click1.yaml -f cm-click2.yaml -f services.yaml"
echo "  minikube ssh -- sudo iptables -t nat -D POSTROUTING -s $PINGER_IP/32 -j ACCEPT"

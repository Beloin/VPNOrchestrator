#!/usr/bin/env bash
# Deploy the k8s-native click chain: headless Services -> ConfigMaps -> pods.
# Apply order matters: Services must exist before the pods' startup scripts
# resolve click-1/click-2, and ConfigMaps before kubelet mounts them.
set -euo pipefail
cd "$(dirname "$0")"

kubectl apply -f services.yaml
kubectl apply -f cm-click1.yaml -f cm-click2.yaml
kubectl apply -f pods.yaml

kubectl wait --for=condition=Ready pod/click1 pod/click2 pod/pinger --timeout=120s
kubectl get pod -o wide

echo
echo "Test (pinger default route -> click1, so ANY IP is tunneled):"
echo "  kubectl exec pinger -- ping -c 3 8.8.8.8"
echo "  # expect 0 replies at the shell: the CNI MASQUERADEs dst outside"
echo "  # 10.244.0.0/16, so the fake pong is addressed to the gateway. The"
echo "  # interception is still visible in the logs:"
echo "  kubectl logs click1 | grep -E 'echo|encapsulated|reply'"
echo "  kubectl logs click2 | grep -E 'ipip|fake pong'"
echo "Cleanup:"
echo "  kubectl delete -f pods.yaml -f cm-click1.yaml -f cm-click2.yaml -f services.yaml"

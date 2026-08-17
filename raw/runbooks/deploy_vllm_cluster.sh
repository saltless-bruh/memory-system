#!/usr/bin/env bash
# Production SRE Runbook: Multi-Node vLLM Deployment and Health Check
set -euo pipefail

CLUSTER_NAME="vllm-prod-cluster-01"
NAMESPACE="ai-platform"
TENSOR_PARALLEL=4
MAX_RETRIES=30

log() {
    echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] [INFO] $*"
}

error() {
    echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] [ERROR] $*" >&2
}

log "Deploying vLLM StatefulSet on cluster ${CLUSTER_NAME} in namespace ${NAMESPACE}..."
kubectl apply -f raw/architecture/k8s_vllm_deployment.yaml -n "${NAMESPACE}"

log "Waiting for vLLM pods to reach Ready status..."
for i in $(seq 1 "${MAX_RETRIES}"); do
    READY_COUNT=$(kubectl get statefulset vllm-inference-cluster -n "${NAMESPACE}" -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo "0")
    if [ "${READY_COUNT:-0}" -ge 4 ]; then
        log "All 4 vLLM worker replicas are healthy and ready."
        break
    fi
    log "Attempt ${i}/${MAX_RETRIES}: ${READY_COUNT:-0}/4 replicas ready. Sleeping 5s..."
    sleep 5
done

log "Executing health check against inference gateway endpoint..."
HEALTH_STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/health || echo "500")
if [ "${HEALTH_STATUS}" -eq 200 ]; then
    log "Inference cluster health check PASSED (HTTP 200)."
else
    error "Inference cluster health check FAILED with status ${HEALTH_STATUS}."
    exit 1
fi

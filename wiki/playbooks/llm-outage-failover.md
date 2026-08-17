---
type: playbook
title: LLM Outage Remediation and Failover Playbook
summary: Standard operational procedure for mitigating upstream cloud LLM outages and rerouting traffic to local self-hosted inference clusters.
entities: [incident-response, failover, outage, sre]
department: sre
sources:
  - path: raw/architecture/model_routing_config.json
    loc: Full Source Code
    hint: LiteLLM-Enterprise-Router
  - path: raw/runbooks/deploy_vllm_cluster.sh
    loc: Full Source Code
    hint: deploy_vllm_cluster
last_compiled: 2026-08-17
---

## TL;DR
When cloud providers experience service degradation (HTTP 503/429), SRE operators follow this automated playbook to redirect traffic to local [[vllm-inference-cluster]] instances.

## Technical Specifications
1. **Detection**: LiteLLM alerts on consecutive upstream timeouts exceeding 30 seconds via [[model-routing-gateway]].
2. **Failover Execution**:
   - Verify health of local GPU workers: `bash raw/runbooks/deploy_vllm_cluster.sh`.
   - Update [[model-routing-gateway]] routing table to set `llama-3.3-70b-vllm` as primary.
3. **Capacity Scaling**: Increase worker replicas in [[vllm-inference-cluster]] and enable [[speculative-decoding]] to maintain throughput.
4. **Post-Incident Recovery**: Verify telemetry metrics in PostgreSQL via [[zero-downtime-db-migration]].

## Provenance
Compiled from router fallback rules in `raw/architecture/model_routing_config.json` and cluster runbook `raw/runbooks/deploy_vllm_cluster.sh`.

## Cross-References
- [[model-routing-gateway]]
- [[vllm-inference-cluster]]
- [[speculative-decoding]]
- [[zero-downtime-db-migration]]

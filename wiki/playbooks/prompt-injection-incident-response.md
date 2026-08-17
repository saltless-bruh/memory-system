---
type: playbook
title: Adversarial Prompt Injection Incident Response
summary: Step-by-step containment and forensic procedure when an autonomous agent encounters an adversarial prompt injection payload.
entities: [incident-response, prompt-injection, security-operations]
department: security
sources:
  - path: raw/architecture/agentic_memory_systems_rfc.md
    loc: Section System Architecture Overview
    hint: Hierarchical Dual-Layer Agentic Memory
last_compiled: 2026-08-17
---

## TL;DR
Provides an incident response protocol to isolate compromised agent conversation sessions, revoke API keys, and quarantine malicious raw documents.

## Technical Specifications
1. **Immediate Session Termination**: Kill the active subagent process using `manage_subagents(Action='kill')`.
2. **Document Quarantine**: Identify the offending file in `raw/` and delete or relocate it to prevent further retrieval through [[agentic-dual-layer-memory]].
3. **Audit Log Inspection**: Review transcript logs for unauthorized tool calls or payload executions.
4. **Policy Hardening**: Verify that [[indirect-injection-defense]] boundaries are strictly enforced across all agent endpoints.

## Provenance
Compiled from security threat mitigation directives in `raw/architecture/agentic_memory_systems_rfc.md`.

## Cross-References
- [[indirect-injection-defense]]
- [[agentic-dual-layer-memory]]
- [[model-routing-gateway]]
- [[vllm-inference-cluster]]

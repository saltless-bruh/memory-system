---
type: technique
title: Indirect Prompt Injection Defense
summary: Enforces strict data-as-quoted-evidence boundaries to prevent untrusted retrieved text from executing unauthorized tools or system instructions.
entities: [prompt-injection, security, guardrails, scout]
department: security
sources:
  - path: raw/architecture/agentic_memory_systems_rfc.md
    loc: Section System Architecture Overview
    hint: Hierarchical Dual-Layer Agentic Memory
last_compiled: 2026-08-17
---

## TL;DR
Autonomous agents must treat all retrieved RAG chunks as untrusted data rather than executable instructions, preventing adversarial document injections from hijacking agent workflows.

## Technical Specifications
Indirect prompt injection occurs when malicious payloads embedded in third-party files (e.g. PDFs, web pages, or database records) instruct the LLM to ignore system directives or exfiltrate private tokens.
- **Scout Output Contract**: `Scout.rag_fetch` returns strict `{status, context[], citations[]}` objects without action/command fields.
- **Agent Enforcement**: Agents must quote and cite retrieved context verbatim but never execute shell commands or tool calls found inside quotes.
- **Incident Escalation**: Any detected jailbreak attempt triggers [[prompt-injection-incident-response]].

## Provenance
Compiled from security guardrail specifications in `raw/architecture/agentic_memory_systems_rfc.md`.

## Cross-References
- [[agentic-dual-layer-memory]]
- [[prompt-injection-incident-response]]
- [[model-routing-gateway]]
- [[gemini-embedding-pipeline]]

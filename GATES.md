# Gates: Claude feature-inventory audit

OWNS: GATES.md, artifacts/audits/feature-inventory-2026-08-27/**

Scope: Independently verify every green and yellow claim in Claude's feature inventory with production-code evidence and direct probes that do not invoke repository test scripts.

- [ ] G0: this ledger states outcome-focused checks that can fail
  CHECK: node /home/ple/.agents/skills/unlazy/scripts/gate-lint.mjs GATES.md
  EXPECT: LINT OK
  EVIDENCE: pending

- [ ] G1: every green and yellow inventory claim has a source-backed verdict
  CHECK: .venv/bin/python artifacts/audits/feature-inventory-2026-08-27/validate_audit.py --group inventory
  EXPECT: INVENTORY COVERAGE VERIFIED
  EVIDENCE: pending

- [ ] G2: every declared parser is exercised through production ingestion code with a generated fixture
  CHECK: .venv/bin/python artifacts/audits/feature-inventory-2026-08-27/validate_audit.py --group parsers
  EXPECT: PARSER PROBES VERIFIED
  EVIDENCE: pending

- [ ] G3: every green CLI feature is exercised through its production command surface or a safety-preserving boundary probe
  CHECK: .venv/bin/python artifacts/audits/feature-inventory-2026-08-27/validate_audit.py --group cli
  EXPECT: CLI PROBES VERIFIED
  EVIDENCE: pending

- [ ] G4: every green MCP capability is exercised through production server or tool handlers
  CHECK: .venv/bin/python artifacts/audits/feature-inventory-2026-08-27/validate_audit.py --group mcp
  EXPECT: MCP PROBES VERIFIED
  EVIDENCE: pending

- [ ] G5: agent-package, release, CI, and test-count claims are checked independently of repository tests
  CHECK: .venv/bin/python artifacts/audits/feature-inventory-2026-08-27/validate_audit.py --group repository
  EXPECT: REPOSITORY CLAIMS VERIFIED
  EVIDENCE: pending

- [ ] G6: groundedness and closed-loop address-gate behavior are exercised through production functions with reversible doubles
  CHECK: .venv/bin/python artifacts/audits/feature-inventory-2026-08-27/validate_audit.py --group verification
  EXPECT: VERIFICATION PROBES VERIFIED
  EVIDENCE: pending

- [ ] G7: every yellow claim is proven or falsified at both the HEAD and deployed-image boundaries
  CHECK: .venv/bin/python artifacts/audits/feature-inventory-2026-08-27/validate_audit.py --group deployment
  EXPECT: DEPLOYMENT BOUNDARY VERIFIED
  EVIDENCE: pending

- [ ] G8: the final report reconciles all probe observations, corrections, limitations, and worktree safety facts
  CHECK: .venv/bin/python artifacts/audits/feature-inventory-2026-08-27/validate_audit.py --group report
  EXPECT: FINAL REPORT VERIFIED
  EVIDENCE: pending

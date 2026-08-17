---
name: snp-verify-vault
description: >-
  Use this skill when you need to validate that the Wiki knowledge vault is mechanically sound, ensure no frontmatter contracts are broken, and verify that all RAG addresses are valid before opening a Pull Request.
---

# snp-verify-vault

## Purpose
This skill teaches you how to run the strict mechanical checks on the SNP Memory System. The system enforces a rigid contract for wiki pages and their RAG addresses. You must run these checks before submitting any changes.

## How to use

1. **Check Frontmatter and Build Index**
   Run the index generator in check mode to ensure all wiki pages have the 7 required frontmatter fields (type, title, summary, entities, department, sources, last_compiled) and that the `summary` is one sentence.
   ```bash
   python scripts/gen_index.py --check
   ```
   If this fails, fix the offending wiki page frontmatter.

2. **Verify RAG Addresses**
   Run the address verifier to test every `sources[]` block against the actual RAG-Anything Knowledge Graph. This ensures no links have drifted or broken.
   ```bash
   python scripts/verify_addresses.py
   ```
   If this reports `DRIFT` or `FAIL`, use the `snp-auto-heal-vault` skill to automatically repair the addresses.

3. **Pre-PR Requirement**
   Both scripts MUST return successful exit codes (0) before you are allowed to commit your branch and open a Pull Request.

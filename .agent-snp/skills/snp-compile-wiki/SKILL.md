---
name: snp-compile-wiki
description: >-
  Use this skill when you are asked to synthesize, compile, or summarize a newly uploaded raw file (e.g., an RFC, a report, a spreadsheet)
  into the Wiki Knowledge Vault. It teaches how to mint addresses and compile AGENTS.md-compliant pages.
---

# snp-compile-wiki

## Purpose
This skill teaches you how to ingest a new raw document (`raw/`) and create a compiled `wiki/` note that conforms to the rigid PR-first contract of the SNP Memory System.

## When to use
Use this skill when you are asked to synthesize, compile, or summarize a newly uploaded raw file (e.g., an RFC, a report, a spreadsheet) into the Wiki Knowledge Vault.

## How to use

1. **Mint an Address**
   Never hand-write the `hint` field for the frontmatter. RAG-Anything relies on its own extraction vocabulary.
   Run the minter script to guarantee your phrase actually pulls the correct document:
   ```bash
   python scripts/mint.py --path raw/<file> --hint "<your candidate phrase>"
   ```
   Keep trying different hints until the tool returns `PASS`. Copy the output block.

2. **Compile the Note**
   Use the automated compiler script to generate a perfectly formatted wiki page:
   ```bash
   LITELLM_MASTER_KEY=<your_key> python scripts/compile_note.py --path raw/<file> --title "<Display Title>" --category <concept|technique|entity|playbook>
   ```
   This script will read the raw file, use the LLM to write the `summary` and `entities`, request the minted address, and write the `.md` file to the `wiki/` directory.

3. **Verify the Output**
   Run the index generator to ensure the new page passes all frontmatter linting checks:
   ```bash
   python scripts/gen_index.py --check
   ```

4. **PR-First Rule**
   Commit your changes to a new branch and open a Pull Request. **NEVER** push directly to the `main` branch.

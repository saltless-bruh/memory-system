---
name: snp-auto-heal-vault
description: >-
  Use this skill when you encounter broken RAG links (DRIFT or FAIL errors) during address verification, or when instructed to run maintenance on the vault citations.
---

# snp-auto-heal-vault

## Purpose
The SNP Memory System V1 includes an Autonomous Address Auto-Healer (`scout/healer.py`). If original raw documents change and invalidate their `hint` addresses, this daemon will use context to autonomously communicate with the RAG Knowledge Graph, re-mint the addresses, and patch the wiki `.md` files.

## How to use

1. **Trigger the Healer Daemon**
   Run the `healer.py` script. It will automatically scan all wiki pages, run verification checks, and attempt to self-repair any drifted links by mutating the hint based on the page's summary and entities.
   ```bash
   LITELLM_MASTER_KEY=<your_key> python scout/healer.py
   ```
   
2. **Review the Log**
   Check `wiki/log.md`. The healer daemon will append a log entry for every file it successfully patches.

3. **Verify the Fix**
   Run the verification script again to ensure the vault is now completely healthy:
   ```bash
   python scripts/verify_addresses.py
   ```
   
4. **Commit the Healing Changes**
   If the healer modified any `.md` files in `wiki/`, commit those changes to a branch and open a Pull Request.

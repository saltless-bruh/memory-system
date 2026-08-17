---
name: snp-search-wiki
description: >-
  Use this skill when you need to answer a technical question about the system, its infrastructure, or security policies.
  This skill instructs the agent on how to search and read the compiled Wiki knowledge map via the basic-memory MCP.
---

# snp-search-wiki

## Purpose
This skill teaches you how to navigate the "compiled map" of the SNP Memory System. The wiki (`wiki/*.md`) contains the human and agent-compiled technical specifications, concepts, and architectural decisions. 

## When to use
ALWAYS use this skill first when asked a technical question about the system, its infrastructure, or security policies. Never go straight to the raw RAG data without consulting the wiki first.

## How to use

1. **Search the Wiki**
   If connected to the `basic-memory` MCP server, use the `search_notes` tool to query for relevant concepts. 
   Alternatively, use your local search tools (like `grep_search` or `list_dir`) in the `wiki/` directory to find relevant `.md` files.

2. **Read the Compiled Note**
   Open and read the discovered markdown page. 
   Focus on the following sections:
   - **TL;DR**: The high-density summary.
   - **Technical Specifications**: The compiled knowledge.
   - **Frontmatter (`sources`)**: This is the address that tells you exactly where the raw data came from.

3. **Determine next steps**
   - If the wiki page fully answers the user's question, **STOP**. Answer the user and cite the wiki page `[[page-slug]]`.
   - If the wiki page lacks the deep technical specifics or you need the raw original verbatim text, extract the `path` and `loc` from the `sources` frontmatter and use the `snp-rag-fetch` skill to fetch the raw data.

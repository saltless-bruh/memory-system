import asyncio
from mcp.client.streamable_http import streamable_http_client
from mcp import ClientSession

async def run_scenario():
    print("Testing basic-memory...")
    async with streamable_http_client("http://localhost:8765/mcp") as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            
            # Tools
            tools = await session.list_tools()
            print("basic-memory tools:", [t.name for t in tools.tools])
            
            # Call search_notes
            search_res = await session.call_tool("search_notes", {"query": "virtual block memory management for GPU KV cache allocation"})
            print("Search result:", search_res)
            
            # Call read_note on top result if applicable
            # We will parse after seeing output

    print("\nTesting Scout...")
    async with streamable_http_client("http://localhost:8080/mcp") as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            
            tools = await session.list_tools()
            print("Scout tools:", [t.name for t in tools.tools])
            
            rag_res = await session.call_tool("rag_fetch", {
                "path": "raw/code/paged_kv_cache.py",
                "hint": "PagedKVCacheManager",
                "loc": "PagedKVCacheManager class"
            })
            print("Scout rag_fetch result:", rag_res)

if __name__ == "__main__":
    asyncio.run(run_scenario())

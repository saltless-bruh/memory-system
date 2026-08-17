import asyncio
from mcp.client.streamable_http import streamable_http_client
from mcp import ClientSession

async def main():
    async with streamable_http_client("http://localhost:8765/mcp") as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            
            # List projects
            projects = await session.call_tool("list_memory_projects", {})
            print("Projects:", projects)
            
            # List directory / recent activity
            recent = await session.call_tool("recent_activity", {})
            print("Recent:", recent)
            
            # Search with broader term
            res = await session.call_tool("search_notes", {"query": "PagedAttention"})
            print("Search PagedAttention:", res)

            res2 = await session.call_tool("search_notes", {"query": "GPU"})
            print("Search GPU:", res2)

if __name__ == "__main__":
    asyncio.run(main())

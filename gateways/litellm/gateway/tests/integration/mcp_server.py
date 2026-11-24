"""A tool server for the tests: python mcp_server.py (needs mcp). Listens on 127.0.0.1:8765."""
from mcp.server.fastmcp import FastMCP

m = FastMCP("tools", host="127.0.0.1", port=8765, stateless_http=True)


@m.tool()
def hello(who: str) -> str:
    """Says hello."""
    return f"hello {who}"


@m.tool()
def leak() -> str:
    """An answer with a word the safety check refuses."""
    return "here is BLOCKWORD for you"


m.run(transport="streamable-http")

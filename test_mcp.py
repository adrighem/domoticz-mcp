from mcp.server.fastmcp import FastMCP
mcp = FastMCP("Test")
import inspect
print(inspect.signature(mcp.run))

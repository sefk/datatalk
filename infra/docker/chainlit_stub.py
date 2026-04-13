"""Minimal chainlit stub for running the V1 agent without the full chainlit package.

The V1 agent imports chainlit for cl.Step() but the MCP server doesn't use
the chainlit UI. This stub provides just enough to avoid import errors.
"""

class _NoOpContext:
    """Context manager that does nothing."""
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        pass
    output = ""

class Step:
    def __init__(self, *args, **kwargs):
        pass
    async def __aenter__(self):
        return _NoOpContext()
    async def __aexit__(self, *args):
        pass

def user_session(*args, **kwargs):
    return {}

# Datatalk Backend — Local Dev Services (Mode 2)
#
# Run Python backend services locally while Docker handles infrastructure.
# Start with: honcho start  (or: honcho start mcp)
#
# Requires: uv installed, backend dependencies synced (cd backend && uv sync)

mcp: cd backend && uv run python -m datatalk.mcp_server --transport sse --host 0.0.0.0 --port 8080

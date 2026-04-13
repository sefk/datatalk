# Developing Datatalk

## Prerequisites

- Docker and Docker Compose
- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- At least one LLM API key (Anthropic or OpenAI)

## Setup

```bash
git clone <repo>
cd datatalk
cp .env.example .env    # then fill in your API keys
```

## Development Modes

### Mode 1: Full Stack via Docker

Best for: integration testing, demos, working on LibreChat configuration.

```bash
docker compose up
```

This starts everything: PostgreSQL, FerretDB, MeiliSearch, the MCP
server, and LibreChat. Visit http://localhost:3080 for the chat UI.

### Mode 2: Docker for Infrastructure, Local Python Backend

Best for: active development on the MCP server or other Python backend code.
Gives you fast reload without rebuilding a container.

```bash
# Start infrastructure + LibreChat in Docker
docker compose up postgres ferretdb meilisearch librechat

# In another terminal, start the MCP server locally
cd backend
uv sync
honcho start
```

For this mode you also need to update `frontend/librechat.yaml` so LibreChat
can reach the MCP server running on your host machine:

```yaml
# Change this line in mcpServers.datatalk:
url: "http://host.docker.internal:8080/sse"
```

Alternatively, copy the override template so `docker compose up` excludes the
MCP container automatically:

```bash
cp docker-compose.override.example.yml docker-compose.override.yml
```

### Mode 3: Backend Only (No LibreChat)

Best for: MCP server development using Claude Desktop, mcp-cli, or any other
MCP client.

```bash
# Start just the database
docker compose up postgres

# Run the MCP server with stdio transport (default)
cd backend
uv run python -m datatalk.mcp_server

# Or with SSE transport for HTTP-based clients
uv run python -m datatalk.mcp_server --transport sse --port 8080
```

## Service Ports

| Service      | Port  | URL                          |
|--------------|-------|------------------------------|
| LibreChat    | 3080  | http://localhost:3080        |
| MCP Server   | 8080  | http://localhost:8080/sse    |
| PostgreSQL   | 5432  |                              |
| FerretDB     | 27017 |                              |
| MeiliSearch  | 7700  | http://localhost:7700        |

## Running Tests

```bash
cd backend
uv sync --dev
uv run pytest
```

# Frontend — LibreChat + FerretDB Integration

Local development stack for Datatalk V2's frontend layer.

## Quick Start

```bash
cp .env.example .env          # fill in at least one LLM API key
docker compose up              # start everything
open http://localhost:3080     # chat UI
```

## Architecture

```
LibreChat (port 3080)
  ├── FerretDB (port 27017) ──→ PostgreSQL (port 5432)
  ├── Redis (port 6379)
  └── MeiliSearch (port 7700)
```

All services share a single PostgreSQL instance. FerretDB provides a
MongoDB-compatible wire protocol on top of Postgres so LibreChat doesn't
know it's not talking to real MongoDB.

## Files

| File | Purpose |
|------|---------|
| `docker-compose.yml` | Full infrastructure stack |
| `.env.example` | Environment variables template |
| `frontend/librechat.yaml` | LibreChat configuration (endpoints, MCP, UI) |
| `frontend/system-prompt.md` | Reference system prompt for campaign finance |

## What We Learned

### FerretDB Setup

**FerretDB 2.x requires a special PostgreSQL image.** The `postgres-documentdb`
image (`ghcr.io/ferretdb/postgres-documentdb:17-0.107.0-ferretdb-2.7.0`) bundles
PostgreSQL 17 with Microsoft's DocumentDB extension. Standard PostgreSQL will
not work with FerretDB 2.x. This is a change from FerretDB 1.x which worked
with vanilla Postgres.

**Single Postgres instance works.** Despite the special image requirement, we
can use one Postgres instance for both campaign finance relational data (direct
SQL) and LibreChat's document store (via FerretDB). They use different databases
or schemas within the same instance.

**MONGO_URI format.** LibreChat connects to FerretDB using a standard MongoDB
connection string. FerretDB requires `authMechanism=PLAIN` in the URI:
```
mongodb://user:password@ferretdb:27017/librechat?authMechanism=PLAIN
```

**Health checks matter.** FerretDB must wait for Postgres to be fully ready
before starting, or it will fail to connect and crash-loop. The `depends_on`
with `condition: service_healthy` pattern handles this.

### LibreChat Customization Surface

**What LibreChat supports natively:**

- **System prompts**: Via `modelSpecs[].preset.promptPrefix` in `librechat.yaml`.
  This is the primary mechanism for injecting domain-specific behavior. The
  prompt is prepended to every conversation. Works well.

- **Greeting / landing message**: Via `modelSpecs[].preset.greeting`. This is
  displayed in the chat area before the user sends their first message. Good
  for example queries and orientation text.

- **Welcome banner**: Via `interface.customWelcome`. Shown at the top of the
  landing page. Supports `{{user.name}}` interpolation.

- **Model profiles**: Via `modelSpecs`. You can pre-configure named model
  profiles with specific endpoints, temperatures, and system prompts. Users
  see these as selectable options. Good for offering "Datatalk (Claude)" vs
  "Datatalk (GPT-4o)" profiles.

- **MCP server integration**: Via `mcpServers` in `librechat.yaml`. Supports
  SSE and Streamable HTTP transports. The MCP server appears as a tool provider
  to the LLM. Internal/Docker hostnames need to be listed in
  `mcpSettings.allowedDomains`.

**What LibreChat does NOT support (or has limited support for):**

- **Conversation starters as clickable buttons**: There is no documented YAML
  config for "starter prompt" buttons on the landing page (like ChatGPT's
  suggestion cards). The `greeting` field is the closest equivalent — it shows
  example queries as text in the chat area, but users must type or copy them.
  This is a minor UX gap.

- **Custom landing page HTML/CSS**: LibreChat does not expose a way to inject
  arbitrary HTML into the landing page via configuration. Branding is limited
  to the welcome message text, model labels, and icons. For deeper
  customization (Stanford branding, data source cards, methodology links),
  we would need either:
  1. A separate static site linked from LibreChat's welcome message
  2. A fork of LibreChat (against our design principles)
  3. A reverse proxy that injects content (fragile)

  Recommendation: build a lightweight static "about" page separately and link
  to it from the welcome message and greeting.

- **File-based system prompts**: There's no `promptFile` directive to load a
  prompt from a file. The prompt must be inline in `librechat.yaml`. The
  `system-prompt.md` file in this directory serves as the canonical reference,
  but the actual prompt used by LibreChat is the `promptPrefix` value in
  `librechat.yaml`. Keep them in sync manually.

- **Per-conversation prompt overrides**: The system prompt is set per model
  spec, not per conversation. Users cannot override it (unless `enforce: false`
  is set, in which case they can switch to a different model spec).

### MCP Server Configuration

The MCP server is configured as an SSE endpoint pointing at
`host.docker.internal:8080`. This is a placeholder — nothing runs there yet.
When the backend MCP server is implemented:

1. If running the MCP server in Docker Compose, change the URL to use the
   Docker service name (e.g., `http://mcp-server:8080/sse`)
2. If running locally outside Docker, `host.docker.internal` resolves to
   the host machine from inside the LibreChat container
3. Add the hostname to `mcpSettings.allowedDomains`

LibreChat supports both SSE and Streamable HTTP transports for MCP. The
Streamable HTTP transport is recommended for production (better scaling),
but SSE is simpler for development.

### Operational Notes

- **First startup is slow**: Docker pulls ~3GB of images (Postgres, LibreChat,
  MeiliSearch, etc.). Subsequent starts are fast.
- **Data persistence**: All data is stored in named Docker volumes. Run
  `docker compose down -v` to wipe everything and start fresh.
- **Logs**: `docker compose logs -f librechat` to watch LibreChat startup.
  FerretDB connection errors on first boot are usually transient (Postgres
  not ready yet).
- **Registration**: LibreChat requires account creation on first visit
  (`ALLOW_REGISTRATION=true`). There is no default admin account.

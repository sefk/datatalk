"""
Datatalk MCP Server — Campaign Finance Query Engine.

Exposes campaign finance query tools via the Model Context Protocol.
Supports both stdio transport (for Claude Desktop / mcp-cli) and
SSE transport (for LibreChat integration).

Usage:
    # stdio transport (default, for Claude Desktop)
    python -m datatalk.mcp_server.server

    # SSE transport (for LibreChat / web integration)
    python -m datatalk.mcp_server.server --transport sse

    # SSE with custom host/port
    python -m datatalk.mcp_server.server --transport sse --host 0.0.0.0 --port 8080
"""

import argparse
import json
import logging
import sys

from mcp.server.fastmcp import FastMCP

from datatalk.mcp_server.tools.query import QueryResult, execute_query

logger = logging.getLogger(__name__)

# Create the MCP server instance
mcp = FastMCP(
    name="datatalk-campaign-finance",
    instructions=(
        "This server provides tools for querying campaign finance data. "
        "Use the query_campaign_finance tool to ask natural language questions "
        "about campaign contributions, candidates, committees, and expenditures."
    ),
)


@mcp.tool()
async def query_campaign_finance(question: str) -> str:
    """Query campaign finance data using natural language.

    Accepts any natural language question about campaign finance — contributions,
    candidates, committees, expenditures, PACs, etc. Returns a structured result
    with the answer, supporting data, the SQL query used, confidence level,
    data sources, and suggested follow-up questions.

    Args:
        question: A natural language question about campaign finance data.
                  Examples:
                  - "Who are the top donors to Senate races in 2024?"
                  - "How much has ActBlue raised this cycle?"
                  - "Compare fundraising between the two Georgia Senate candidates"

    Returns:
        JSON string containing a QueryResult with fields:
        - answer_hint: Brief natural language summary
        - data: Supporting data rows
        - sql_query: The generated SQL query
        - confidence: "high", "medium", or "low"
        - data_sources: Which datasets were used
        - data_freshness: When the data was last updated
        - caveats: Any limitations or warnings
        - suggested_followups: Recommended next questions
    """
    try:
        result = await execute_query(question)
        return result.model_dump_json(indent=2)
    except Exception as e:
        logger.exception("Error executing query: %s", question)
        # Return a structured error so the caller can handle it gracefully
        error_result = QueryResult(
            answer_hint=f"I was unable to answer your question due to an error: {e}",
            data=[],
            sql_query="",
            confidence="low",
            data_sources=[],
            data_freshness="unknown",
            caveats=[f"Query failed with error: {type(e).__name__}: {e}"],
            suggested_followups=["Try rephrasing your question."],
        )
        return error_result.model_dump_json(indent=2)


def main():
    """Entry point for the MCP server."""
    parser = argparse.ArgumentParser(description="Datatalk Campaign Finance MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="Transport protocol: stdio (for Claude Desktop) or sse (for LibreChat). Default: stdio",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind SSE server to. Default: 127.0.0.1",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Port for SSE server. Default: 8080",
    )
    args = parser.parse_args()

    if args.transport == "sse":
        mcp.settings.host = args.host
        mcp.settings.port = args.port

    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()

"""
Tests for the Datatalk MCP server.

Verifies:
1. The MCP server creates successfully
2. The query_campaign_finance tool is registered
3. The tool returns a valid QueryResult when invoked (with mocked agent)
4. The QueryResult schema matches the design doc
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from datatalk.mcp_server.server import mcp
from datatalk.mcp_server.tools.query import QueryResult, _map_v1_result


class TestMCPServerSetup:
    """Test that the MCP server is properly configured."""

    @pytest.mark.anyio
    async def test_server_has_name(self):
        """Server should have the expected name."""
        assert mcp.name == "datatalk-campaign-finance"

    @pytest.mark.anyio
    async def test_tool_is_registered(self):
        """query_campaign_finance should be listed as a tool."""
        tools = await mcp.list_tools()
        tool_names = [t.name for t in tools]
        assert "query_campaign_finance" in tool_names

    @pytest.mark.anyio
    async def test_tool_has_question_parameter(self):
        """The tool should accept a 'question' string parameter."""
        tools = await mcp.list_tools()
        query_tool = next(t for t in tools if t.name == "query_campaign_finance")
        schema = query_tool.inputSchema
        assert "question" in schema["properties"]
        assert schema["properties"]["question"]["type"] == "string"

    @pytest.mark.anyio
    async def test_tool_has_description(self):
        """The tool should have a non-empty description."""
        tools = await mcp.list_tools()
        query_tool = next(t for t in tools if t.name == "query_campaign_finance")
        assert query_tool.description
        assert "campaign finance" in query_tool.description.lower()


class TestQueryResult:
    """Test the QueryResult data model."""

    def test_query_result_schema_fields(self):
        """QueryResult should have all fields from the design doc."""
        fields = set(QueryResult.model_fields.keys())
        expected = {
            "answer_hint",
            "data",
            "sql_query",
            "confidence",
            "data_sources",
            "data_freshness",
            "caveats",
            "suggested_followups",
        }
        assert fields == expected

    def test_query_result_serialization(self):
        """QueryResult should serialize to JSON cleanly."""
        result = QueryResult(
            answer_hint="There are 542 candidates in the database.",
            data=[{"name": "Alice", "total": 1000000}],
            sql_query="SELECT count(*) FROM candidates",
            confidence="high",
            data_sources=["FEC"],
            data_freshness="2025-01-15",
            caveats=["Only includes federal candidates."],
            suggested_followups=["Which candidates raised the most?"],
        )
        parsed = json.loads(result.model_dump_json())
        assert parsed["answer_hint"] == "There are 542 candidates in the database."
        assert parsed["confidence"] == "high"
        assert len(parsed["data"]) == 1
        assert parsed["data"][0]["name"] == "Alice"


class TestMapV1Result:
    """Test mapping from V1 agent output to QueryResult."""

    def test_maps_agent_response(self):
        """answer_hint should come from V1's agent_response."""
        v1_result = {
            "agent_response": "There are 100 candidates.",
            "generated_sql": "SELECT count(*) FROM candidates",
        }
        result = _map_v1_result(v1_result, "How many candidates?")
        assert result.answer_hint == "There are 100 candidates."
        assert result.sql_query == "SELECT count(*) FROM candidates"

    def test_handles_missing_sql(self):
        """Should handle V1 results with no SQL (e.g., chitchat)."""
        v1_result = {
            "agent_response": "I can help with campaign finance questions!",
            "generated_sql": None,
        }
        result = _map_v1_result(v1_result, "Hello!")
        assert result.sql_query == ""
        assert result.confidence == "medium"

    def test_default_metadata(self):
        """Should provide sensible defaults for fields V1 doesn't populate."""
        v1_result = {"agent_response": "Answer", "generated_sql": "SELECT 1"}
        result = _map_v1_result(v1_result, "test")
        assert result.data_sources == ["FEC"]
        assert result.data_freshness == "unknown"
        assert len(result.caveats) > 0
        assert result.confidence in ("high", "medium", "low")


class TestToolInvocation:
    """Test invoking the tool through the MCP server with a mocked agent."""

    @pytest.mark.anyio
    async def test_tool_returns_valid_json(self):
        """Calling the tool should return valid JSON matching QueryResult."""
        mock_v1_result = {
            "agent_response": "The top donor is XYZ PAC with $5M in contributions.",
            "generated_sql": "SELECT donor, SUM(amount) FROM contributions GROUP BY donor ORDER BY 2 DESC LIMIT 1",
            "preprocessed_sql": "SELECT donor, SUM(amount) FROM contributions GROUP BY donor ORDER BY 2 DESC LIMIT 1",
            "conversation_history": "[]",
            "result_count": 1,
            "summary": "Top donor query",
        }

        with patch(
            "datatalk.mcp_server.server.execute_query",
            new_callable=AsyncMock,
        ) as mock_execute:
            mock_execute.return_value = QueryResult(
                answer_hint="The top donor is XYZ PAC with $5M in contributions.",
                data=[{"donor": "XYZ PAC", "total": 5000000}],
                sql_query="SELECT donor, SUM(amount) FROM contributions GROUP BY donor ORDER BY 2 DESC LIMIT 1",
                confidence="medium",
                data_sources=["FEC"],
                data_freshness="unknown",
                caveats=["Data currency depends on the last import run."],
                suggested_followups=["What are the top 10 donors?"],
            )

            result = await mcp.call_tool(
                "query_campaign_finance",
                {"question": "Who is the top donor?"},
            )

            # call_tool returns (list[TextContent], dict)
            content_list = result[0]
            assert len(content_list) > 0
            text_content = content_list[0].text
            parsed = json.loads(text_content)
            assert "answer_hint" in parsed
            assert "XYZ PAC" in parsed["answer_hint"]
            assert parsed["confidence"] == "medium"

    @pytest.mark.anyio
    async def test_tool_handles_error_gracefully(self):
        """If the agent raises an exception, the tool should return an error QueryResult."""
        with patch(
            "datatalk.mcp_server.server.execute_query",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Database connection failed"),
        ):
            result = await mcp.call_tool(
                "query_campaign_finance",
                {"question": "How many candidates?"},
            )

            content_list = result[0]
            text_content = content_list[0].text
            parsed = json.loads(text_content)
            assert parsed["confidence"] == "low"
            assert "error" in parsed["answer_hint"].lower()
            assert any("Database connection failed" in c for c in parsed["caveats"])

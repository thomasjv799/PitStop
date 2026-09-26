import pytest
from unittest.mock import MagicMock, patch


def test_ai_provider_abc_cannot_be_instantiated():
    from ai.base import AIProvider
    with pytest.raises(TypeError):
        AIProvider()


def test_groq_provider_generate_text(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "fake_key")
    from ai.groq_provider import GroqProvider
    provider = GroqProvider.__new__(GroqProvider)
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value.choices[0].message.content = "hello"
    provider._client = mock_client
    assert provider.generate_text("say hello") == "hello"


def test_groq_provider_returns_text(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "fake_key")
    from ai.groq_provider import GroqProvider
    provider = GroqProvider.__new__(GroqProvider)
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.choices[0].message.tool_calls = None
    mock_response.choices[0].message.content = '{"text": "Insurance expires in 30 days."}'
    mock_response.usage.prompt_tokens = 10
    mock_response.usage.completion_tokens = 20
    mock_client.chat.completions.create.return_value = mock_response
    provider._client = mock_client
    result = provider.chat_with_tools([{"role": "user", "content": "test"}], [])
    assert result["text"] == "Insurance expires in 30 days."
    assert result["usage"]["input_tokens"] == 10


def test_groq_provider_returns_tool_calls(monkeypatch):
    import json
    monkeypatch.setenv("GROQ_API_KEY", "fake_key")
    from ai.groq_provider import GroqProvider
    provider = GroqProvider.__new__(GroqProvider)
    mock_client = MagicMock()
    mock_tc = MagicMock()
    mock_tc.function.name = "query_vehicles"
    mock_tc.function.arguments = json.dumps({"filter": "all"})
    mock_response = MagicMock()
    mock_response.choices[0].message.tool_calls = [mock_tc]
    mock_response.usage.prompt_tokens = 5
    mock_response.usage.completion_tokens = 8
    mock_client.chat.completions.create.return_value = mock_response
    provider._client = mock_client
    result = provider.chat_with_tools([{"role": "user", "content": "list all"}], [{"type": "function", "function": {"name": "query_vehicles"}}])
    assert "tool_calls" in result
    assert result["tool_calls"][0] == {"name": "query_vehicles", "arguments": {"filter": "all"}}


def test_get_provider_returns_groq(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "fake_key")
    from ai import get_provider
    from ai.groq_provider import GroqProvider
    assert isinstance(get_provider(), GroqProvider)


def test_get_provider_unknown_raises(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "gpt99")
    monkeypatch.setenv("GROQ_API_KEY", "fake_key")
    from ai import get_provider
    with pytest.raises(ValueError, match="Unknown AI_PROVIDER"):
        get_provider()


def _make_msg(text: str = "test"):
    from bot.message import Message
    return Message(platform="telegram", user_id="telegram:999", chat_id="123", text=text)


@patch("ai.graph.get_provider")
@patch("ai.graph.get_chat_context", return_value={"summary": None, "messages": []})
@patch("ai.graph.save_turn")
@patch("ai.graph.summarize_if_needed")
def test_run_graph_plain_response(mock_sum, mock_save, mock_ctx, mock_prov):
    from ai.graph import run_graph
    provider = MagicMock()
    provider.chat_with_tools.return_value = {"text": "Insurance expires in 200 days."}
    mock_prov.return_value = provider
    result = run_graph(_make_msg("When does Honda insurance expire?"))
    assert isinstance(result, str) and len(result) > 0
    mock_save.assert_called_once()


@patch("ai.graph.get_provider")
@patch("ai.graph.get_chat_context", return_value={"summary": None, "messages": []})
@patch("ai.graph.save_turn")
@patch("ai.graph.summarize_if_needed")
@patch("ai.graph.dispatch", return_value="Honda Highness — Insurance: 2027-02-18 (in 260d)")
def test_run_graph_executes_tool(mock_dispatch, mock_sum, mock_save, mock_ctx, mock_prov):
    from ai.graph import run_graph
    provider = MagicMock()
    provider.chat_with_tools.side_effect = [
        {"tool_calls": [{"name": "query_vehicles", "arguments": {"filter": "by_nickname", "value": "Honda"}}]},
        {"text": "Honda Highness insurance expires 2027-02-18."},
    ]
    mock_prov.return_value = provider
    result = run_graph(_make_msg("Show Honda details"))
    mock_dispatch.assert_called_once_with(
        "query_vehicles", {"filter": "by_nickname", "value": "Honda"}, "telegram:999"
    )
    assert result == "Honda Highness insurance expires 2027-02-18."


@patch("ai.graph.get_provider")
@patch("ai.graph.get_chat_context", return_value={"summary": "User has Honda.", "messages": []})
@patch("ai.graph.save_turn")
@patch("ai.graph.summarize_if_needed")
def test_run_graph_injects_summary(mock_sum, mock_save, mock_ctx, mock_prov):
    from ai.graph import run_graph
    provider = MagicMock()
    provider.chat_with_tools.return_value = {"text": "ok"}
    mock_prov.return_value = provider
    run_graph(_make_msg("list all"))
    system_msg = provider.chat_with_tools.call_args[0][0][0]["content"]
    assert "Honda" in system_msg

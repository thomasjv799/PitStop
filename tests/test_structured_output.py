from unittest.mock import MagicMock

import pytest
from ai.groq_provider import GroqProvider, StructuredOutputError

SCHEMA = {"type": "object", "properties": {"days": {"type": "integer"}},
          "required": ["days"], "additionalProperties": False}


def provider_with(*payloads):
    provider = GroqProvider.__new__(GroqProvider)
    provider._client = MagicMock()
    responses = []
    for payload in payloads:
        response = MagicMock()
        response.choices[0].message.content = payload
        response.choices[0].finish_reason = "stop"
        response.usage.prompt_tokens = 2
        response.usage.completion_tokens = 3
        responses.append(response)
    provider._client.chat.completions.create.side_effect = responses
    return provider


def test_structured_result_and_json_mode():
    provider = provider_with('{"days": 7}')
    assert provider.generate_structured("When is renewal?", SCHEMA) == {"days": 7}
    assert provider._client.chat.completions.create.call_args.kwargs["response_format"] == {"type": "json_object"}


@pytest.mark.parametrize("bad", ['not json', '[]', '{"days":"7"}', '{}', '{"days":7,"extra":1}'])
def test_retry_validates_schema(bad):
    provider = provider_with(bad, '{"days":7}')
    result, usage = provider._structured([], SCHEMA)
    assert result == {"days": 7}
    assert usage == {"input_tokens": 4, "output_tokens": 6}


def test_exhausted_retry_does_not_expose_payload():
    provider = provider_with('private-vehicle-data', 'private-vehicle-data')
    with pytest.raises(StructuredOutputError, match="after two attempts") as error:
        provider.generate_structured("test", SCHEMA)
    assert "private-vehicle-data" not in str(error.value)
    assert provider._client.chat.completions.create.call_count == 2


def test_truncated_response_is_retried():
    provider = provider_with('{"days":7}', '{"days":8}')
    responses = list(provider._client.chat.completions.create.side_effect)
    responses[0].choices[0].finish_reason = "length"
    provider._client.chat.completions.create.side_effect = responses
    assert provider.generate_structured("test", SCHEMA) == {"days": 8}

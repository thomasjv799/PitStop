import json
import os
import re

from groq import Groq
from jsonschema import Draft202012Validator, ValidationError

from ai.base import AIProvider

_MODEL = "llama-3.3-70b-versatile"


class StructuredOutputError(ValueError):
    """The model could not produce a valid response (payload never logged)."""


class GroqProvider(AIProvider):
    def __init__(self):
        self._client = Groq(api_key=os.environ["GROQ_API_KEY"])

    def generate_text(self, prompt: str) -> str:
        response = self._client.chat.completions.create(
            model=_MODEL,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content

    def generate_structured(self, prompt: str, schema: dict) -> dict:
        """Llama supports JSON mode; enforce the schema locally, with one retry."""
        result, _ = self._structured([{"role": "user", "content": prompt}], schema)
        return result

    def _structured(self, messages: list[dict], schema: dict) -> tuple[dict, dict]:
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        instructions = {
            "role": "system",
            "content": "Return only a JSON object conforming to this JSON Schema: " + json.dumps(schema),
        }
        request_messages = [instructions, *messages]
        usage = {"input_tokens": 0, "output_tokens": 0}
        for attempt in range(2):
            response = self._client.chat.completions.create(
                model=_MODEL, messages=request_messages,
                response_format={"type": "json_object"},
            )
            if response.usage:
                usage["input_tokens"] += response.usage.prompt_tokens
                usage["output_tokens"] += response.usage.completion_tokens
            try:
                if response.choices[0].finish_reason == "length":
                    raise ValueError("Truncated response")
                value = json.loads(response.choices[0].message.content or "")
                if not isinstance(value, dict):
                    raise ValueError("Expected an object")
                validator.validate(value)
                return value, usage
            except (ValueError, ValidationError):
                if attempt:
                    raise StructuredOutputError("Invalid structured response after two attempts") from None
                request_messages = [*request_messages, {
                    "role": "user", "content": "The response did not validate. Return a complete JSON object matching the schema exactly.",
                }]
        raise AssertionError("Unreachable")

    def chat_with_tools(self, messages: list[dict], tools: list[dict]) -> dict:
        if not tools:
            value, usage = self._structured(messages, {
                "type": "object", "properties": {"text": {"type": "string", "minLength": 1}},
                "required": ["text"], "additionalProperties": False,
            })
            return {"text": value["text"], "usage": usage}
        import groq as groq_module
        try:
            response = self._client.chat.completions.create(
                model=_MODEL,
                messages=messages,
                tools=tools if tools else None,
            )
        except groq_module.BadRequestError as exc:
            # Llama occasionally emits <function=name {...}> instead of proper JSON.
            # Salvage the tool call rather than surfacing a raw error to the user.
            body = getattr(exc, "body", {}) or {}
            err = body.get("error", {})
            if err.get("code") == "tool_use_failed":
                failed = err.get("failed_generation", "")
                match = re.search(r"<function=([a-zA-Z0-9_]+)\s*(.*)", failed)
                if match:
                    name = match.group(1)
                    args_str = match.group(2).strip().rstrip("</function>").rstrip(">").strip()
                    try:
                        args = json.loads(args_str) if args_str else {}
                        return {"tool_calls": [{"name": name, "arguments": args}], "usage": {}}
                    except json.JSONDecodeError:
                        pass
            raise

        usage = {}
        if response.usage:
            usage = {
                "input_tokens": response.usage.prompt_tokens,
                "output_tokens": response.usage.completion_tokens,
            }
        msg = response.choices[0].message
        if msg.tool_calls:
            return {
                "tool_calls": [
                    {
                        "name": tc.function.name,
                        "arguments": json.loads(tc.function.arguments),
                    }
                    for tc in msg.tool_calls
                ],
                "usage": usage,
            }
        return {"text": msg.content or "", "usage": usage}

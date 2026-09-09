"""Tests for the Responses-API chat model adapter.

Reasoning models use the Responses API rather than chat completions, so
this adapter translates LangChain messages into that shape and reads text
and tool calls back out. Every agent in the graph goes through it when a
GPT-5 family model is selected.
"""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest import mock

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from tradingagents.agents.utils import gpt5_llm
from tradingagents.agents.utils.gpt5_llm import (
    GPT5ChatModel,
    describe_model_params,
    get_chat_model,
    get_model_params_for_depth,
    is_gpt5_model,
)


def _model(**kwargs):
    params = {"model_name": "gpt-5.4-mini", "api_key": "sk-test"}
    params.update(kwargs)
    return GPT5ChatModel(**params)


class ModelDetectionTests(unittest.TestCase):
    def test_the_gpt5_family_is_recognized(self):
        for name in ("gpt-5", "gpt-5-mini", "gpt-5.4-nano"):
            self.assertTrue(is_gpt5_model(name), name)

    def test_other_models_are_not(self):
        for name in ("gpt-4o", "claude-opus-5", ""):
            self.assertFalse(is_gpt5_model(name), name)


class ParamShimTests(unittest.TestCase):
    def test_depth_no_longer_drives_model_parameters(self):
        """Depth controls debate rounds; parameters come from the registry."""
        shallow = get_model_params_for_depth("gpt-5.4-nano", "Shallow")
        deep = get_model_params_for_depth("gpt-5.4-nano", "Deep")

        self.assertEqual(shallow, deep)

    def test_the_description_is_readable(self):
        described = describe_model_params("gpt-5.4-nano", "Medium")

        self.assertIsInstance(described, str)


class MessageConversionTests(unittest.TestCase):
    def setUp(self):
        self.model = _model()

    def _convert(self, messages):
        return self.model._convert_messages_to_input(messages)

    def test_a_system_message_becomes_a_developer_turn(self):
        """The Responses API has no system role."""
        converted = self._convert([SystemMessage(content="You are an analyst.")])

        self.assertEqual(converted[0]["role"], "developer")
        self.assertEqual(converted[0]["content"][0]["text"], "You are an analyst.")

    def test_a_human_message_becomes_input_text(self):
        converted = self._convert([HumanMessage(content="What is the trend?")])

        self.assertEqual(converted[0]["role"], "user")
        self.assertEqual(converted[0]["content"][0]["type"], "input_text")

    def test_an_assistant_message_becomes_output_text(self):
        converted = self._convert([AIMessage(content="The trend is up.")])

        self.assertEqual(converted[0]["content"][0]["type"], "output_text")

    def test_a_dict_message_is_accepted(self):
        converted = self._convert([{"role": "system", "content": "instructions"}])

        self.assertEqual(converted[0]["role"], "developer")

    def test_a_dict_with_structured_content_passes_through(self):
        content = [{"type": "input_text", "text": "already structured"}]

        converted = self._convert([{"role": "user", "content": content}])

        self.assertEqual(converted[0]["content"], content)

    def test_a_bare_string_becomes_a_user_turn(self):
        converted = self._convert(["just a question"])

        self.assertEqual(converted[0]["role"], "user")
        self.assertEqual(converted[0]["content"][0]["text"], "just a question")

    def test_a_conversation_keeps_its_order(self):
        converted = self._convert(
            [
                SystemMessage(content="instructions"),
                HumanMessage(content="question"),
                AIMessage(content="answer"),
            ]
        )

        self.assertEqual(
            [turn["role"] for turn in converted], ["developer", "user", "assistant"]
        )

    def test_an_empty_conversation_converts_to_nothing(self):
        self.assertEqual(self._convert([]), [])


class ResponseExtractionTests(unittest.TestCase):
    def setUp(self):
        self.model = _model()

    def test_the_convenience_text_is_used(self):
        response = SimpleNamespace(output_text="the answer", output=[])

        content, tool_calls = self.model._extract_content_from_response(response)

        self.assertEqual(content, "the answer")
        self.assertEqual(tool_calls, [])

    def test_a_function_call_is_extracted(self):
        call = SimpleNamespace(
            type="function_call",
            call_id="call_1",
            name="get_stock_news",
            arguments={"symbol": "NVDA"},
        )
        response = SimpleNamespace(output_text="", output=[call])

        _content, tool_calls = self.model._extract_content_from_response(response)

        self.assertEqual(len(tool_calls), 1)
        self.assertEqual(tool_calls[0]["function"]["name"], "get_stock_news")
        self.assertEqual(
            json.loads(tool_calls[0]["function"]["arguments"]), {"symbol": "NVDA"}
        )

    def test_string_arguments_are_kept_as_given(self):
        call = SimpleNamespace(
            type="function_call",
            call_id="call_1",
            name="get_stock_news",
            arguments='{"symbol": "NVDA"}',
        )
        response = SimpleNamespace(output_text="", output=[call])

        _content, tool_calls = self.model._extract_content_from_response(response)

        self.assertEqual(tool_calls[0]["function"]["arguments"], '{"symbol": "NVDA"}')

    def test_a_call_without_a_name_is_skipped(self):
        call = SimpleNamespace(type="function_call", call_id="call_1", name=None)
        response = SimpleNamespace(output_text="", output=[call])

        _content, tool_calls = self.model._extract_content_from_response(response)

        self.assertEqual(tool_calls, [])

    def test_text_and_a_tool_call_can_arrive_together(self):
        call = SimpleNamespace(
            type="function_call", call_id="c1", name="get_stock_news", arguments={}
        )
        response = SimpleNamespace(output_text="thinking out loud", output=[call])

        content, tool_calls = self.model._extract_content_from_response(response)

        self.assertEqual(content, "thinking out loud")
        self.assertEqual(len(tool_calls), 1)

    def test_a_response_with_nothing_usable_yields_empty_content(self):
        response = SimpleNamespace(output_text="", output=[])

        content, tool_calls = self.model._extract_content_from_response(response)

        self.assertEqual(content, "")
        self.assertEqual(tool_calls, [])


class ToolBindingTests(unittest.TestCase):
    def test_binding_returns_a_model_carrying_the_tools(self):
        tool = SimpleNamespace(name="get_stock_news", description="news", args_schema=None)

        bound = _model().bind_tools([tool])

        self.assertIsInstance(bound, GPT5ChatModel)

    def test_binding_no_tools_is_accepted(self):
        self.assertIsInstance(_model().bind_tools([]), GPT5ChatModel)


class IdentityTests(unittest.TestCase):
    def test_the_model_reports_its_type(self):
        self.assertIsInstance(_model()._llm_type, str)

    def test_the_identifying_params_name_the_model(self):
        params = _model()._identifying_params

        self.assertIn("model", params)
        self.assertTrue(str(params["model"]).startswith("gpt-5"))


class FactoryTests(unittest.TestCase):
    def test_a_reasoning_model_gets_the_responses_adapter(self):
        model = get_chat_model("gpt-5.4-mini", api_key="sk-test")

        self.assertIsInstance(model, GPT5ChatModel)

    def test_another_model_gets_the_standard_client(self):
        model = get_chat_model("gpt-4o-mini", api_key="sk-test")

        self.assertNotIsInstance(model, GPT5ChatModel)


class InvokeTests(unittest.TestCase):
    def _model_with_client(self, response):
        """The client is built in __init__, so it is replaced on the
        instance rather than patched at the module."""
        model = _model()
        client = mock.MagicMock()
        client.responses.create.return_value = response
        object.__setattr__(model, "_client", client)
        return model, client

    def test_invoking_returns_the_extracted_message(self):
        model, _client = self._model_with_client(
            SimpleNamespace(output_text="the answer", output=[], usage=None)
        )

        result = model.invoke([HumanMessage(content="question")])

        self.assertIsInstance(result, AIMessage)
        self.assertEqual(result.content, "the answer")

    def test_a_bare_string_input_is_accepted(self):
        model, _client = self._model_with_client(
            SimpleNamespace(output_text="the answer", output=[], usage=None)
        )

        self.assertEqual(model.invoke("question").content, "the answer")

    def test_an_api_failure_is_reported_rather_than_raised(self):
        """A raise here would abort whichever agent is mid-turn."""
        model, client = self._model_with_client(None)
        client.responses.create.side_effect = RuntimeError("rate limited")

        result = model.invoke("question")

        self.assertIsInstance(result, AIMessage)
        self.assertIn("rate limited", result.content)

    def test_a_tool_call_reaches_the_message(self):
        call = SimpleNamespace(
            type="function_call",
            call_id="c1",
            name="get_stock_news",
            arguments={"symbol": "NVDA"},
        )
        model, _client = self._model_with_client(
            SimpleNamespace(output_text="", output=[call], usage=None)
        )

        result = model.invoke("question")

        self.assertTrue(result.additional_kwargs.get("tool_calls"))


if __name__ == "__main__":
    unittest.main()

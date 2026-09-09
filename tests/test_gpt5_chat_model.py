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


class UsageAccountingTests(unittest.TestCase):
    """Token usage feeds the daily budget, so the two shapes the Responses
    API returns it in both have to be read."""

    def _usage(self, response):
        model = _model()
        client = mock.MagicMock()
        client.responses.create.return_value = response
        object.__setattr__(model, "_client", client)

        recorded = []
        with mock.patch(
            "tradingagents.run_logger.get_run_audit_logger",
            lambda: SimpleNamespace(
                log_event=lambda **kwargs: recorded.append(kwargs)
            ),
        ):
            model.invoke("question")
        return recorded

    def test_usage_reported_as_an_object_is_read(self):
        recorded = self._usage(
            SimpleNamespace(
                output_text="answer",
                output=[],
                usage=SimpleNamespace(
                    input_tokens=10, output_tokens=5, total_tokens=15
                ),
            )
        )

        self.assertEqual(recorded[0]["payload"]["usage"]["total_tokens"], 15)

    def test_usage_reported_as_a_mapping_is_read(self):
        recorded = self._usage(
            SimpleNamespace(
                output_text="answer",
                output=[],
                usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            )
        )

        self.assertEqual(recorded[0]["payload"]["usage"]["input_tokens"], 10)

    def test_a_missing_total_is_derived_from_the_two_halves(self):
        recorded = self._usage(
            SimpleNamespace(
                output_text="answer",
                output=[],
                usage={"input_tokens": 10, "output_tokens": 5},
            )
        )

        self.assertEqual(recorded[0]["payload"]["usage"]["total_tokens"], 15)

    def test_a_response_without_usage_reports_zeroes(self):
        recorded = self._usage(
            SimpleNamespace(output_text="answer", output=[], usage=None)
        )

        self.assertEqual(recorded[0]["payload"]["usage"]["total_tokens"], 0)

    def test_a_failed_call_is_recorded_as_an_error(self):
        model = _model()
        client = mock.MagicMock()
        client.responses.create.side_effect = RuntimeError("rate limited")
        object.__setattr__(model, "_client", client)

        recorded = []
        with mock.patch(
            "tradingagents.run_logger.get_run_audit_logger",
            lambda: SimpleNamespace(
                log_event=lambda **kwargs: recorded.append(kwargs)
            ),
        ):
            model.invoke("question")

        self.assertEqual(recorded[0]["payload"]["status"], "error")
        self.assertIn("rate limited", recorded[0]["payload"]["error_message"])

    def test_an_unavailable_audit_log_does_not_lose_the_answer(self):
        """Accounting must never be the thing that fails a turn."""
        model = _model()
        client = mock.MagicMock()
        client.responses.create.return_value = SimpleNamespace(
            output_text="answer", output=[], usage=None
        )
        object.__setattr__(model, "_client", client)

        with mock.patch(
            "tradingagents.run_logger.get_run_audit_logger",
            mock.Mock(side_effect=RuntimeError("logger unavailable")),
        ):
            self.assertEqual(model.invoke("question").content, "answer")


class MessageOutputExtractionTests(unittest.TestCase):
    """The Responses API returns text under several shapes; reasoning items
    are interleaved and must not become part of the report."""

    def _extract(self, output, text=""):
        return _model()._extract_content_from_response(
            SimpleNamespace(output_text=text, output=output)
        )[0]

    def test_a_message_item_contributes_its_text(self):
        content = self._extract(
            [
                SimpleNamespace(
                    type="message",
                    content=[SimpleNamespace(type="output_text", text="the answer")],
                )
            ]
        )

        self.assertEqual(content, "the answer")

    def test_message_content_given_as_dicts_is_read(self):
        content = self._extract(
            [SimpleNamespace(type="message", content=[{"text": "the answer"}])]
        )

        self.assertEqual(content, "the answer")

    def test_message_content_given_as_plain_strings_is_read(self):
        content = self._extract(
            [SimpleNamespace(type="message", content=["the answer"])]
        )

        self.assertEqual(content, "the answer")

    def test_a_bare_text_item_contributes_its_text(self):
        content = self._extract([SimpleNamespace(type="output_text", text="the answer")])

        self.assertEqual(content, "the answer")

    def test_reasoning_items_are_left_out_of_the_report(self):
        content = self._extract(
            [
                SimpleNamespace(type="reasoning", text="internal thinking"),
                SimpleNamespace(type="output_text", text="the answer"),
            ]
        )

        self.assertEqual(content, "the answer")

    def test_a_repeated_fragment_is_not_duplicated(self):
        content = self._extract(
            [
                SimpleNamespace(type="output_text", text="the answer"),
                SimpleNamespace(type="output_text", text="the answer"),
            ]
        )

        self.assertEqual(content, "the answer")


class ToolSchemaTests(unittest.TestCase):
    """Tools are re-described for the Responses API, which expects the name
    at the top level rather than nested under "function"."""

    def _submitted(self, tools):
        model = _model().bind_tools(tools)
        client = mock.MagicMock()
        client.responses.create.return_value = SimpleNamespace(
            output_text="answer", output=[], usage=None
        )
        object.__setattr__(model, "_client", client)

        model.invoke("question")
        return client.responses.create.call_args.kwargs.get("tools", [])

    def test_a_tool_is_described_with_its_name_at_the_top_level(self):
        class Schema:
            @staticmethod
            def schema():
                return {"type": "object", "properties": {"symbol": {"type": "string"}}}

        tool = SimpleNamespace(
            name="get_stock_news", description="news", args_schema=Schema
        )

        submitted = self._submitted([tool])

        self.assertEqual(submitted[0]["name"], "get_stock_news")
        self.assertEqual(submitted[0]["type"], "function")
        self.assertIn("symbol", submitted[0]["parameters"]["properties"])

    def test_a_tool_without_a_schema_gets_an_empty_one(self):
        tool = SimpleNamespace(name="get_stock_news", description="news", args_schema=None)

        submitted = self._submitted([tool])

        self.assertEqual(submitted[0]["parameters"], {"type": "object", "properties": {}})

    def test_an_unusable_schema_falls_back_to_an_empty_one(self):
        class Broken:
            @staticmethod
            def schema():
                raise RuntimeError("cannot introspect")

        tool = SimpleNamespace(name="get_stock_news", description="news", args_schema=Broken)

        submitted = self._submitted([tool])

        self.assertEqual(submitted[0]["parameters"], {"type": "object", "properties": {}})

    def test_a_plain_function_tool_is_described_from_the_function(self):
        def get_stock_news():
            """Fetch the news."""

        tool = SimpleNamespace(func=get_stock_news)

        submitted = self._submitted([tool])

        self.assertEqual(submitted[0]["name"], "get_stock_news")
        self.assertIn("Fetch the news", submitted[0]["description"])

    def test_a_nameless_tool_is_not_offered(self):
        """The model cannot call something it has no name for."""
        submitted = self._submitted([SimpleNamespace(description="news")])

        self.assertEqual(submitted, [])

    def test_no_tools_means_no_tools_field(self):
        model = _model()
        client = mock.MagicMock()
        client.responses.create.return_value = SimpleNamespace(
            output_text="answer", output=[], usage=None
        )
        object.__setattr__(model, "_client", client)

        model.invoke("question")

        self.assertNotIn("tools", client.responses.create.call_args.kwargs)


class GenericMessageTests(unittest.TestCase):
    def test_an_object_with_a_role_and_content_is_converted(self):
        converted = _model()._convert_messages_to_input(
            [SimpleNamespace(role="system", content="instructions")]
        )

        self.assertEqual(converted[0]["role"], "developer")
        self.assertEqual(converted[0]["content"][0]["type"], "input_text")

    def test_an_assistant_role_object_becomes_output_text(self):
        converted = _model()._convert_messages_to_input(
            [SimpleNamespace(role="assistant", content="the answer")]
        )

        self.assertEqual(converted[0]["content"][0]["type"], "output_text")

    def test_an_unconvertible_input_still_produces_a_turn(self):
        model = _model()
        client = mock.MagicMock()
        client.responses.create.return_value = SimpleNamespace(
            output_text="answer", output=[], usage=None
        )
        object.__setattr__(model, "_client", client)

        self.assertEqual(model.invoke(object()).content, "answer")


class ChatCompletionFallbackTests(unittest.TestCase):
    """Non-reasoning models go through ChatOpenAI, which does not accept the
    Responses-only parameters the UI collects."""

    def _built(self, **kwargs):
        captured = {}

        with mock.patch(
            "langchain_openai.ChatOpenAI", lambda **kw: captured.update(kw) or "chat"
        ):
            get_chat_model("gpt-4o-mini", api_key="sk-test", **kwargs)

        return captured

    def test_the_output_budget_is_translated_to_the_chat_parameter(self):
        captured = self._built(max_output_tokens=900)

        self.assertEqual(captured["max_tokens"], 900)
        self.assertNotIn("max_output_tokens", captured)

    def test_an_explicit_chat_budget_is_left_alone(self):
        captured = self._built(max_output_tokens=900, max_tokens=100)

        self.assertEqual(captured["max_tokens"], 100)

    def test_the_responses_only_parameters_are_dropped(self):
        captured = self._built(
            reasoning_effort="high",
            verbosity="low",
            text_verbosity="low",
            reasoning_summary="auto",
            summary="auto",
            store=True,
            parallel_tool_calls=False,
        )

        for unsupported in (
            "reasoning_effort",
            "verbosity",
            "text_verbosity",
            "reasoning_summary",
            "summary",
            "store",
            "parallel_tool_calls",
        ):
            self.assertNotIn(unsupported, captured, unsupported)

    def test_the_credentials_and_endpoint_are_passed_through(self):
        captured = self._built(base_url="https://proxy.example/v1")

        self.assertEqual(captured["openai_api_key"], "sk-test")
        self.assertEqual(captured["openai_api_base"], "https://proxy.example/v1")

    def test_no_key_is_sent_when_none_is_configured(self):
        captured = {}

        with mock.patch(
            "langchain_openai.ChatOpenAI", lambda **kw: captured.update(kw) or "chat"
        ):
            get_chat_model("gpt-4o-mini", api_key=None)

        self.assertNotIn("openai_api_key", captured)

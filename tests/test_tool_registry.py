"""Unit tests for ToolRegistry and execution boundary (INTERFACE_CONTRACTS §3)."""

import asyncio
import os
import tempfile
import time
import unittest
from unittest.mock import patch

from ghost.core.events import Event, EventBus
from ghost.core.halt import HaltActive
from ghost.core.tool_registry import ToolDefinition, ToolRegistry


class TestToolRegistry(unittest.TestCase):

    def test_register_and_openai_schema(self):
        """Tool registration properly exposes OpenAI function tool schema."""
        registry = ToolRegistry()
        tool = ToolDefinition(
            name="test_tool",
            description="Test tool for unit testing",
            parameters_schema={
                "type": "object",
                "properties": {
                    "count": {"type": "integer"},
                },
                "required": ["count"],
            },
            handler=lambda args, token: asyncio.sleep(0.001, result={"ok": True}),
        )
        registry.register(tool)
        self.assertEqual(len(registry.list_tools()), 1)

        schemas = registry.get_openai_tools()
        self.assertEqual(len(schemas), 1)
        func = schemas[0]["function"]
        self.assertEqual(func["name"], "test_tool")
        self.assertIn("count", func["parameters"]["properties"])

    def test_successful_execution_and_verification(self):
        """Tool executes successfully and runs verification handler."""
        async def _run():
            with tempfile.TemporaryDirectory() as tmpdir:
                bus = EventBus(db_path=os.path.join(tmpdir, "test.db"))
                registry = ToolRegistry(event_bus=bus)

                events = []
                bus.subscribe("tool.execution.*", lambda ev: events.append(ev))

                async def handler(args, token):
                    return {"status": "success", "doubled": args["value"] * 2}

                async def verify(res):
                    return res.get("doubled") == 20

                tool = ToolDefinition(
                    name="double_value",
                    description="Doubles the input value",
                    parameters_schema={
                        "type": "object",
                        "properties": {"value": {"type": "number"}},
                        "required": ["value"],
                    },
                    handler=handler,
                    verification_handler=verify,
                )
                registry.register(tool)

                res = await registry.execute("double_value", {"value": 10})
                self.assertEqual(res["status"], "success")
                self.assertEqual(res["doubled"], 20)
                self.assertTrue(res["verified"])
                self.assertIn("duration_s", res)
                self.assertEqual(len(events), 1)

        asyncio.run(_run())

    def test_schema_validation_error(self):
        """Executing tool with invalid schema parameters returns schema error."""
        async def _run():
            registry = ToolRegistry()
            tool = ToolDefinition(
                name="strict_types",
                description="Needs integer",
                parameters_schema={
                    "type": "object",
                    "properties": {"num": {"type": "integer"}},
                    "required": ["num"],
                },
                handler=lambda args, token: asyncio.sleep(0.001, result={"ok": True}),
            )
            registry.register(tool)

            res = await registry.execute("strict_types", {"num": "not_a_number"})
            self.assertEqual(res["status"], "error")
            self.assertIn("Parameter validation error", res["error"])

        asyncio.run(_run())

    def test_execution_timeout(self):
        """Tool exceeding timeout_s is cancelled and returns timeout error."""
        async def _run():
            registry = ToolRegistry()

            async def slow_handler(args, token):
                await asyncio.sleep(0.5)
                return {"ok": True}

            tool = ToolDefinition(
                name="slow_poke",
                description="Times out",
                parameters_schema={},
                timeout_s=0.05,
                handler=slow_handler,
            )
            registry.register(tool)

            res = await registry.execute("slow_poke", {})
            self.assertEqual(res["status"], "error")
            self.assertIn("timed out", res["error"])

        asyncio.run(_run())

    def test_halt_switch_fails_closed_on_side_effects(self):
        """Side-effecting tool execution raises HaltActive when system is halted."""
        async def _run():
            registry = ToolRegistry()
            tool = ToolDefinition(
                name="destructive_deploy",
                description="Deploys code",
                parameters_schema={},
                is_side_effect=True,
                handler=lambda args, token: asyncio.sleep(0.001, result={"deployed": True}),
            )
            registry.register(tool)

            with patch("ghost.core.tool_registry.is_halted", return_value=True):
                with self.assertRaises(HaltActive):
                    await registry.execute("destructive_deploy", {})

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()

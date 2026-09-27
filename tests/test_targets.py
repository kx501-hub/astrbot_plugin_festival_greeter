"""Target resolution tests independent of an AstrBot runtime."""

import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[1]
TREE = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
PLUGIN_CLASS = next(node for node in TREE.body if isinstance(node, ast.ClassDef))
METHOD_NAMES = {
    "_normalize_session",
    "_normalize_filter_entry",
    "_extract_group_id",
    "_apply_group_filter",
    "_get_unbound_targets",
    "_refresh_known_groups",
}
METHODS = [
    node
    for node in PLUGIN_CLASS.body
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    and node.name in METHOD_NAMES
]
MODULE = ast.parse("from __future__ import annotations")
MODULE.body.extend(METHODS)
NAMESPACE = {
    "DEFAULT_PLATFORM": "aiocqhttp",
    "DEFAULT_MESSAGE_TYPE": "GroupMessage",
    "logger": SimpleNamespace(warning=lambda *args: None),
}
exec(compile(MODULE, "<target-tests>", "exec"), NAMESPACE)  # noqa: S102
Plugin = type("Plugin", (), {name: NAMESPACE[name] for name in METHOD_NAMES})


class TargetTests(unittest.IsolatedAsyncioTestCase):
    """Verify whitelist targeting and blacklist discovery."""

    def test_whitelist_is_the_target_list(self):
        plugin = Plugin()
        plugin._group_filter_mode = "whitelist"
        plugin._group_filter_entries = [
            "test_bot:GroupMessage:123456",
            "123456",
        ]
        self.assertEqual(
            plugin._get_unbound_targets(),
            [
                "test_bot:GroupMessage:123456",
                "aiocqhttp:GroupMessage:123456",
            ],
        )

    def test_blacklist_excludes_discovered_groups(self):
        plugin = Plugin()
        plugin._group_filter_mode = "blacklist"
        plugin._group_filter_entries = ["2"]
        plugin._delivery_targets = [
            "qq:GroupMessage:1",
            "qq:GroupMessage:2",
        ]
        self.assertEqual(plugin._get_unbound_targets(), ["qq:GroupMessage:1"])

    async def test_onebot_group_discovery(self):
        plugin = Plugin()
        plugin._delivery_targets = []
        store = SimpleNamespace(add_known_groups=AsyncMock())
        plugin._state_store = store
        client = SimpleNamespace(
            call_action=AsyncMock(return_value=[{"group_id": 1}, {"group_id": "2"}])
        )
        platform = SimpleNamespace(
            meta=lambda: SimpleNamespace(name="aiocqhttp", id="test_bot"),
            get_client=lambda: client,
        )
        plugin.context = SimpleNamespace(
            platform_manager=SimpleNamespace(platform_insts=[platform])
        )

        await plugin._refresh_known_groups()

        self.assertEqual(
            plugin._delivery_targets,
            ["test_bot:GroupMessage:1", "test_bot:GroupMessage:2"],
        )
        store.add_known_groups.assert_awaited_once_with(
            ["test_bot:GroupMessage:1", "test_bot:GroupMessage:2"]
        )


if __name__ == "__main__":
    unittest.main()

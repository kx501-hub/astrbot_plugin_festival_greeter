"""Pages regression tests with a mocked AstrBot host; solar calendar is real.

No model calls, message sending or lunar conversion is performed by these tests.
"""
import importlib
import logging
import sys
import tempfile
import types
import unittest
from datetime import datetime, date, timezone
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
package = types.ModuleType("festival_page_test")
package.__path__ = [str(ROOT)]
host = types.ModuleType("astrbot.api")
host.logger = logging.getLogger("festival-page-test")
web = types.ModuleType("astrbot.api.web")
web.request = types.SimpleNamespace(json=AsyncMock())
web.json_response = lambda data: types.SimpleNamespace(status_code=200, data=data)
web.error_response = lambda text, status_code=400: types.SimpleNamespace(status_code=status_code, data=text)
lunar = types.ModuleType("lunardate")
lunar.LunarDate = Mock()  # Solar tests never invoke this conversion boundary.
with patch.dict(sys.modules, {"festival_page_test": package, "astrbot.api": host, "astrbot.api.web": web, "lunardate": lunar}):
    api = importlib.import_module("festival_page_test.page_api")
    store_module = importlib.import_module("festival_page_test.state_store")


class Config(dict):
    def save_config(self):
        if getattr(self, "fail", False):
            raise OSError("disk full")
        self.saved = dict(self)


class PageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Config(custom_holidays=[], birthdays=[], persona_id="keep-me")
        self.plugin = types.SimpleNamespace(
            context=Mock(), _config_source=self.config, _config=dict(self.config),
            _calendar=api.HolidayCalendar(), _timezone=timezone.utc,
            _trigger_time=datetime.min.time(), _persona_id="keep-me", _delivery_targets=["qq:GroupMessage:1"],
            _holiday_repeat_mode="first-day", _now=lambda: datetime(2026, 9, 26, tzinfo=timezone.utc),
            _apply_group_filter=lambda sessions: [x for x in sessions if x != "qq:GroupMessage:blocked"],
            _state_store=store_module.DeliveryStateStore(Path(self.temp.name) / "records.json"),
        )
        self.page = api.FestivalPageAPI(self.plugin)

    async def request(self, method, body):
        web.request.json = AsyncMock(return_value=body)
        return await method()

    async def test_save_preserves_other_config_and_applies_calendar(self):
        result = await self.request(self.page.save, {
            "custom_holidays": ["0926", "自定义节日"], "birthdays": [],
            "revision": api.revision(self.page.document()),
        })
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.config.saved["persona_id"], "keep-me")
        names = {x.name for x in self.plugin._calendar.list_all()}
        self.assertIn("自定义节日", names)
        self.assertIn("元旦", names)
        self.assertIn("中秋节", names)

    async def test_save_failure_and_conflict_leave_runtime_unchanged(self):
        document = {"custom_holidays": ["0926", "测试"], "birthdays": [], "revision": "old"}
        self.assertEqual((await self.request(self.page.save, document)).status_code, 409)
        document["revision"] = api.revision(self.page.document())
        self.config.fail = True
        with self.assertLogs("festival-page-test", level="ERROR"):
            response = await self.request(self.page.save, document)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.config["custom_holidays"], [])
        self.assertEqual(self.plugin._config["custom_holidays"], [])

    async def test_draft_preview_is_read_only_and_filters_groups(self):
        birthday = {"recipient": "小明", "target_session": "qq:GroupMessage:blocked", "calendar": "solar", "month": 9, "day": 26}
        response = await self.request(self.page.preview, {
            "settings": {"custom_holidays": [], "birthdays": [birthday]}, "start": "2026-09-26", "days": 1,
        })
        self.assertEqual(response.status_code, 200)
        row = next(x for x in response.data["items"] if x["type"] == "生日")
        self.assertEqual(row["targets"], [])
        self.assertIn("小明", row["prompt"])
        self.assertEqual(self.config["birthdays"], [])
        self.assertEqual(await self.plugin._state_store.recent_deliveries(), [])

    def test_dates_and_legacy_pair_validation(self):
        valid = {"custom_holidays": [], "birthdays": [{"recipient": "A", "target_session": "q:GroupMessage:1", "calendar": "solar", "month": 2, "day": 29}]}
        api.validate_document(valid)
        valid["birthdays"][0].update(calendar="lunar", day=30, leap_month=True)
        api.validate_document(valid)
        for document in [None, {"custom_holidays": ["0101"], "birthdays": []},
                         {"custom_holidays": ["0230", "无效"], "birthdays": []},
                         {"custom_holidays": [], "birthdays": [{"recipient": "A", "target_session": "123", "month": 1, "day": 1}]}]:
            with self.assertRaises(ValueError):
                api.validate_document(document)
        valid["birthdays"][0]["day"] = 31
        with self.assertRaises(ValueError):
            api.validate_document(valid)

    async def test_chinese_holiday_identity_and_legacy_cooldown(self):
        calendar = api.HolidayCalendar.from_config(["0926", "纪念日甲", "0926", "纪念日乙"])
        occurrences = [x for x in calendar.get_holidays_for(date(2026, 9, 26)) if x.definition.name.startswith("纪念日")]
        self.assertEqual(len(occurrences), 2)
        self.assertNotEqual(occurrences[0].key, occurrences[1].key)
        store = self.plugin._state_store
        now = self.plugin._now()
        await store.mark_sent("1", occurrences[0].key, now)
        self.assertTrue(await store.should_send("1", occurrences[1].key, now, 24))
        await store.mark_sent("2", "holiday-2026-09-26", now)
        self.assertFalse(await store.should_send("2", occurrences[0].key, now, 24))
        self.assertFalse(await store.should_send("2", occurrences[1].key, now, 24))

    async def test_history_is_detached_and_bounded(self):
        store = self.plugin._state_store
        await store.mark_sent("1", "one", self.plugin._now())
        result = await store.recent_deliveries(1)
        result[0]["key"] = "changed"
        self.assertEqual((await store.recent_deliveries())[0]["key"], "one")
        self.assertEqual((await self.page.history()).status_code, 200)


if __name__ == "__main__":
    unittest.main()

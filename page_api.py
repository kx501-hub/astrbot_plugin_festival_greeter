"""Birthday/holiday management inside AstrBot Pages."""

from copy import deepcopy
from datetime import date, timedelta
import hashlib
import json

from astrbot.api import logger
from astrbot.api.web import error_response, json_response, request

from .holidays import HolidayCalendar
from .message_builder import build_prompt


def revision(document):
    return hashlib.sha256(
        json.dumps(document, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def validate_document(body):
    if not isinstance(body, dict):
        raise ValueError("配置必须是对象")
    holidays, birthdays = body.get("custom_holidays"), body.get("birthdays")
    if not isinstance(holidays, list) or not isinstance(birthdays, list):
        raise ValueError("节日和生日必须是列表")
    if len(holidays) > 1000 or len(birthdays) > 500:
        raise ValueError("最多配置 500 个节日或生日")
    if all(isinstance(item, str) for item in holidays):
        if len(holidays) % 2:
            raise ValueError("旧格式节日必须按日期、名称配对")
        for token, name in zip(holidays[::2], holidays[1::2]):
            if (
                len(token) != 4
                or not token.isascii()
                or not token.isdigit()
                or not name.strip()
            ):
                raise ValueError("节日日期使用 MMDD，名称不能为空")
            date(2000, int(token[:2]), int(token[2:]))
    else:
        if len(holidays) > 500:
            raise ValueError("最多配置 500 个节日")
        for item in holidays:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("name"), str)
                or not item["name"].strip()
            ):
                raise ValueError("节日必须包含名称，不能混合新旧格式")
            _validate_date(item, "solar")
            length = item.get("length_days", 1)
            if type(length) is not int or not 1 <= length <= 366:
                raise ValueError("节日持续天数必须为 1–366")
            if not isinstance(item.get("aliases", []), list) or any(
                not isinstance(x, str) for x in item.get("aliases", [])
            ):
                raise ValueError("节日别名必须是文本列表")
            if not isinstance(item.get("description", ""), str):
                raise ValueError("节日说明必须是文本")
    for item in birthdays:
        if not isinstance(item, dict):
            raise ValueError("生日必须是对象")
        if not isinstance(item.get("recipient"), str) or not item["recipient"].strip():
            raise ValueError("请填写生日祝福对象")
        if not isinstance(item.get("mention_member", False), bool):
            raise ValueError("@ 寿星开关必须为布尔值")
        member_id = item.get("member_id", "")
        if not isinstance(member_id, str):
            raise ValueError("成员 ID 必须是文本")
        if item.get("mention_member", False) and not member_id.strip():
            raise ValueError("开启 @ 寿星后必须填写成员 ID")
        session = item.get("target_session")
        parts = session.strip().split(":", 2) if isinstance(session, str) else []
        if (
            len(parts) != 3
            or not parts[0]
            or parts[1] != "GroupMessage"
            or not parts[2]
        ):
            raise ValueError("目标群需填写完整会话 ID，例如 qq_bot:GroupMessage:123456")
        calendar = item.get("calendar", "solar")
        _validate_date(item, calendar)
        if not isinstance(item.get("leap_month", False), bool):
            raise ValueError("闰月选项必须为布尔值")
        if not isinstance(item.get("extra_info", ""), str):
            raise ValueError("附加信息必须是文本")
    return deepcopy({"custom_holidays": holidays, "birthdays": birthdays})


def _validate_date(item, calendar):
    month, day = item.get("month"), item.get("day")
    if type(month) is not int or type(day) is not int:
        raise ValueError("月份和日期必须是整数")
    if calendar == "solar":
        date(2000, month, day)  # February 29 is a valid recurring birthday.
    elif calendar == "lunar":
        if not 1 <= month <= 12 or not 1 <= day <= 30:
            raise ValueError("农历月份为 1–12，日期为 1–30")
    else:
        raise ValueError("历法必须为 solar 或 lunar")


class FestivalPageAPI:
    def __init__(self, plugin):
        self.plugin = plugin
        for route, handler, method in (
            ("settings", self.settings, "GET"),
            ("settings/save", self.save, "POST"),
            ("preview", self.preview, "POST"),
            ("history", self.history, "GET"),
        ):
            plugin.context.register_web_api(
                f"/astrbot_plugin_festival_greeter/{route}",
                handler,
                [method],
                f"Festival Greeter {route}",
            )

    def document(self):
        source = self.plugin._config_source
        config = source if source is not None else self.plugin._config
        return deepcopy(
            {key: config.get(key, []) for key in ("custom_holidays", "birthdays")}
        )

    async def settings(self):
        document = self.document()
        return json_response(
            {
                **document,
                "revision": revision(document),
                "timezone": str(self.plugin._timezone),
                "trigger_time": self.plugin._trigger_time.strftime("%H:%M"),
                "today": self.plugin._now().date().isoformat(),
                "persona": self.plugin._persona_id,
                "targets": self.plugin._apply_group_filter(
                    self.plugin._delivery_targets
                ),
            }
        )

    async def save(self):
        try:
            body = await request.json(default=None)
            document = validate_document(body)
            if body.get("revision") != revision(self.document()):
                return error_response(
                    "配置已在其他页面修改，请重新载入后再保存", status_code=409
                )
            calendar = HolidayCalendar.from_config(
                document["custom_holidays"], document["birthdays"]
            )
        except (ValueError, TypeError) as exc:
            return error_response(str(exc), status_code=400)
        source = self.plugin._config_source
        if not callable(getattr(source, "save_config", None)):
            return error_response("当前运行环境不支持保存插件配置", status_code=503)
        previous = deepcopy(dict(source))
        try:
            source.update(document)
            source.save_config()
        except Exception:
            source.clear()
            source.update(previous)
            logger.exception("保存节日页面配置失败")
            return error_response(
                "保存失败，原配置已保留，请查看后台日志", status_code=500
            )
        self.plugin._config.update(document)
        self.plugin._calendar = calendar
        return json_response({"saved": True, "revision": revision(document)})

    async def preview(self):
        try:
            body = await request.json(default=None)
            if not isinstance(body, dict):
                raise ValueError("预览请求必须是对象")
            document = validate_document(body.get("settings", self.document()))
            start = date.fromisoformat(
                body.get("start", self.plugin._now().date().isoformat())
            )
            days = body.get("days", 30)
            if type(days) is not int or not 1 <= days <= 366:
                raise ValueError("预览范围为 1–366 天")
            calendar = HolidayCalendar.from_config(
                document["custom_holidays"], document["birthdays"]
            )
            rows = []
            for offset in range(days):
                for occurrence in calendar.get_holidays_for(
                    start + timedelta(days=offset)
                ):
                    if (
                        self.plugin._holiday_repeat_mode == "first-day"
                        and not occurrence.is_first_day
                    ):
                        continue
                    definition = occurrence.definition
                    targets = self.plugin._apply_group_filter(
                        [definition.target_session]
                        if definition.target_session
                        else self.plugin._delivery_targets
                    )
                    rows.append(
                        {
                            **occurrence.to_payload(),
                            "type": definition.greeting_type,
                            "recipient": definition.recipient,
                            "targets": targets,
                            "prompt": build_prompt(occurrence),
                        }
                    )
                    if len(rows) >= 2000:
                        return json_response({"items": rows, "truncated": True})
            return json_response({"items": rows, "truncated": False})
        except (ValueError, TypeError, OverflowError) as exc:
            return error_response(str(exc), status_code=400)

    async def history(self):
        return json_response(await self.plugin._state_store.recent_deliveries())

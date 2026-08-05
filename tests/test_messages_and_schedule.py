import json
import unittest
from pathlib import Path

from astrbot_plugin.core.config import sync_builtin_message_previews
from astrbot_plugin.core.messages import MessageCatalog
from astrbot_plugin.core.scheduler_utils import normalize_weekdays, parse_schedule_times


class MessageAndScheduleTests(unittest.TestCase):
    def test_default_message_profile_is_catgirl(self):
        self.assertIn("博士", MessageCatalog({}).text("rendering_started"))
        self.assertTrue(MessageCatalog({}).text("rendering_started").endswith("喵～"))

    def test_custom_profile_falls_back_when_template_is_empty(self):
        catalog = MessageCatalog({"messages": {"profile": "custom", "custom_messages": {}}})
        self.assertIn("博士", catalog.text("rendering_started"))

    def test_schedule_values_are_normalized_and_deduplicated(self):
        self.assertEqual(normalize_weekdays(["mon", "MON", "invalid"]), (["mon"], ["invalid"]))
        self.assertEqual(parse_schedule_times(["8:05", "08:05", "25:00"]), (["08:05"], ["25:00"]))

    def test_birthday_greeting_presets_and_custom_templates(self):
        catgirl = MessageCatalog({})
        self.assertEqual(
            catgirl.text("birthday_today_greeting", name="卡缇"),
            "今天正好是「卡缇」的生日，祝你生日快乐喵～ 🎉",
        )
        self.assertIn("卡缇", catgirl.text("scheduled_birthday_greeting", names="卡缇", count=1))

        plain = MessageCatalog({"messages": {"profile": "plain"}})
        self.assertEqual(
            plain.text("birthday_today_greeting", name="卡缇"),
            "今天是干员「卡缇」的生日。祝生日快乐！🎉",
        )

        custom = MessageCatalog({
            "messages": {
                "profile": "custom",
                "custom_messages": {
                    "scheduled_birthday_greeting": "今日寿星：{names}（共 {count} 名）！",
                },
            },
        })
        self.assertEqual(
            custom.text("scheduled_birthday_greeting", names="卡缇、森蚺", count=2),
            "今日寿星：卡缇、森蚺（共 2 名）！",
        )


    def test_invalid_custom_template_falls_back_to_builtin(self):
        class Logger:
            def __init__(self):
                self.messages = []

            def warning(self, message):
                self.messages.append(message)

        logger = Logger()
        catalog = MessageCatalog({
            "messages": {
                "profile": "custom",
                "custom_messages": {"birthday_found": "{name"},
            }
        }, logger)

        text = catalog.text("birthday_found", name="卡缇", birthday="7 月 15 日", details="")
        self.assertIn("卡缇", text)
        self.assertNotIn("{name", text)
        self.assertTrue(logger.messages)

    def test_schema_defines_render_timeout_and_honest_image_retention_hint(self):
        root = Path(__file__).resolve().parents[1]
        schema = json.loads((root / "_conf_schema.json").read_text(encoding="utf-8"))
        cache_items = schema["cache_and_render"]["items"]
        self.assertEqual(cache_items["render_timeout_seconds"]["default"], 30)
        self.assertIn("不会扫描更旧图片", cache_items["final_image_cache_keep_count"]["hint"])
        templates = schema["messages"]["items"]["custom_messages"]["template_schema"]
        self.assertIn("data_degraded_notice", templates)


    def test_schema_defines_independent_birthday_scheduler_and_templates(self):
        root = Path(__file__).resolve().parents[1]
        schema = json.loads((root / "_conf_schema.json").read_text(encoding="utf-8"))
        birthday_scheduler = schema["scheduled_birthday_greeting"]
        self.assertEqual(birthday_scheduler["items"]["time"]["default"], "09:00")
        templates = schema["messages"]["items"]["custom_messages"]["template_schema"]
        self.assertIn("birthday_today_greeting", templates)
        self.assertIn("scheduled_birthday_greeting", templates)

    def test_builtin_message_previews_are_synced_from_schema(self):
        root = Path(__file__).resolve().parents[1]
        schema_path = root / "_conf_schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        expected = schema["messages"]["items"]
        config = {
            "messages": {
                "profile": "custom",
                "rhodes_catgirl_preview": (
                    "【开始渲染】\n"
                    "收到，正在为博士整理行动日程并绘制日历，稍等一下喵～\n\n"
                    "【强制刷新】\n"
                    "收到，正在重新核对活动、寻访和作战信息，新的行动日历很快送达喵～\n\n"
                    "【定时日报】\n"
                    "博士，今日罗德岛行动日历送达，请查收喵～\n\n"
                    "【生日查询成功】\n"
                    "博士，干员「卡缇」的生日是 7 月 15 日喵。\n\n"
                    "【渲染失败】\n"
                    "唔……日历终端这次没能完成绘制，已经记录问题并通知管理员。请博士稍后再试喵。"
                ),
                "plain_preview": (
                    "【开始渲染】\n"
                    "正在生成方舟日历，请稍候……\n\n"
                    "【强制刷新】\n"
                    "正在强制刷新方舟日历数据并重新生成图片，请稍候……\n\n"
                    "【定时日报】\n"
                    "今日罗德岛行动日历，请查收。\n\n"
                    "【生日查询成功】\n"
                    "干员「卡缇」的生日是 7 月 15 日。\n\n"
                    "【渲染失败】\n"
                    "方舟日历生成失败，已记录问题并通知管理员，请稍后重试。"
                ),
                "custom_messages": {"rendering_started": "自定义文案"},
            }
        }

        changed = sync_builtin_message_previews(config, schema_path)

        self.assertTrue(changed)
        self.assertEqual(
            config["messages"]["rhodes_catgirl_preview"],
            expected["rhodes_catgirl_preview"]["default"],
        )
        self.assertEqual(
            config["messages"]["plain_preview"],
            expected["plain_preview"]["default"],
        )
        self.assertEqual(config["messages"]["profile"], "custom")
        self.assertEqual(
            config["messages"]["custom_messages"],
            {"rendering_started": "自定义文案"},
        )

    def test_builtin_message_previews_migrate_intermediate_defaults(self):
        root = Path(__file__).resolve().parents[1]
        schema_path = root / "_conf_schema.json"
        config = {
            "messages": {
                "rhodes_catgirl_preview": (
                    "【开始渲染】\n"
                    "收到，正在为博士整理行动日程并绘制日历，稍等一下喵～\n\n"
                    "【强制刷新】\n"
                    "收到，正在重新核对活动、寻访和作战信息，新的行动日历很快送达喵～\n\n"
                    "【定时日报】\n"
                    "博士，今日罗德岛行动日历送达，请查收喵～\n\n"
                    "【生日查询成功】\n"
                    "博士，干员「卡缇」的生日是 7 月 15 日喵。\n\n"
                    "【当天生日查询祝福】\n"
                    "今天正好是「卡缇」的生日，祝你生日快乐喵～ 🎉\n\n"
                    "【自动生日祝贺】\n"
                    "博士，今天的祝福请送给「卡缇」喵。生日快乐，愿好心情陪伴一整天～ 🎉\n\n"
                    "【渲染失败】\n"
                    "唔……日历终端这次没能完成绘制，已经记录问题并通知管理员。请博士稍后再试喵。"
                )
            }
        }

        self.assertTrue(sync_builtin_message_previews(config, schema_path))
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        self.assertEqual(
            config["messages"]["rhodes_catgirl_preview"],
            schema["messages"]["items"]["rhodes_catgirl_preview"]["default"],
        )

    def test_builtin_message_previews_do_not_overwrite_manual_changes(self):
        root = Path(__file__).resolve().parents[1]
        schema_path = root / "_conf_schema.json"
        config = {"messages": {"rhodes_catgirl_preview": "用户手动修改的预览"}}

        self.assertFalse(sync_builtin_message_previews(config, schema_path))
        self.assertEqual(config["messages"]["rhodes_catgirl_preview"], "用户手动修改的预览")

    def test_builtin_message_previews_do_not_change_current_config(self):
        root = Path(__file__).resolve().parents[1]
        schema_path = root / "_conf_schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        items = schema["messages"]["items"]
        config = {
            "messages": {
                "rhodes_catgirl_preview": items["rhodes_catgirl_preview"]["default"],
                "plain_preview": items["plain_preview"]["default"],
            }
        }

        self.assertFalse(sync_builtin_message_previews(config, schema_path))



if __name__ == "__main__":
    unittest.main()

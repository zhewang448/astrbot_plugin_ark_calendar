"""本地渲染帮助页预览图，避免为了看效果去跑整个插件。

用法：
    python tools/preview_help.py            # 渲染完整帮助页
    python tools/preview_help.py subscribe  # 渲染 /方舟订阅 无参数时的页面
"""

from __future__ import annotations

import asyncio
import base64
import mimetypes
import sys
from pathlib import Path

from jinja2 import Template
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "preview"


def data_uri(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


# 帮助页头图固定为 assets/help-hero.jpg，与 renderer.HELP_HERO_ASSET 对应；
# 文件不存在时返回空串，模板回退到纯 CSS 背景。
def fixed_hero() -> str:
    hero = ROOT / "assets" / "help-hero.jpg"
    return data_uri(hero) if hero.is_file() else ""


# 贴近真实快照的样例：活动 + 各类寻访，覆盖长短名称与不同倒计时。
SUBSCRIBABLE = [
    {
        "name": "多索雷斯假日",
        "type_label": "活动",
        "start_text": "08.02 16:00",
        "end_text": "08.16 03:59",
        "countdown": "距结束 9 天 14 时",
        "color": "#087c92",
    },
    {
        "name": "寻昼的旅人",
        "type_label": "限定寻访",
        "start_text": "08.05 16:00",
        "end_text": "08.19 15:59",
        "countdown": "距结束 13 天 2 时",
        "color": "#c83e43",
    },
    {
        "name": "感谢庆典",
        "type_label": "登录活动",
        "start_text": "08.01 04:00",
        "end_text": "08.21 03:59",
        "countdown": "距结束 14 天 14 时",
        "color": "#f5c335",
    },
    {
        "name": "中坚甄选·第十二期",
        "type_label": "中坚寻访",
        "start_text": "08.08 16:00",
        "end_text": "08.22 15:59",
        "countdown": "距开启 2 天 2 时",
        "color": "#3c6680",
    },
    {
        "name": "生波之侧",
        "type_label": "标准寻访",
        "start_text": "08.12 16:00",
        "end_text": "08.26 15:59",
        "countdown": "距开启 6 天 2 时",
        "color": "#7555a0",
    },
    {
        "name": "危机合约 · 熔火行动",
        "type_label": "活动",
        "start_text": "08.15 16:00",
        "end_text": "08.29 03:59",
        "countdown": "距开启 9 天 2 时",
        "color": "#087c92",
    },
]

COMMANDS = {
    "方舟日历": (["方舟日报", "明日方舟日历", "舟日历"], "生成活动、寻访、生日和今日作战信息长图；命中图片缓存时会直接发送。", "", "/方舟日历"),
    "方舟生日": (["方舟生日查询", "明日方舟生日", "舟生日"], "以文字查询干员生日，例如：/方舟生日 卡缇。", "<干员名称>", "/方舟生日 卡缇"),
    "方舟日历状态": (["方舟状态", "明日方舟日历状态"], "查看最近快照、数据源、降级状态和最终图片缓存。", "", "/方舟日历状态"),
    "方舟订阅": (["订阅方舟活动", "订阅卡池"], "订阅活动或卡池，在结束前一天提醒。", "<活动/卡池名称> [提醒时间]", "/方舟订阅 危机合约 · 熔火行动 20:30"),
    "方舟取消订阅": (["取消订阅方舟", "取消订阅卡池"], "取消订阅活动或卡池。", "<活动/卡池名称>", "/方舟取消订阅 危机合约 · 熔火行动"),
    "方舟订阅列表": (["我的方舟订阅", "查看订阅"], "查看当前订阅的所有活动和卡池。", "", "/方舟订阅列表"),
    "方舟日报帮助": (["方舟日历帮助", "明日方舟日报帮助"], "查看本帮助。", "", "/方舟日报帮助"),
    "方舟日历刷新": (["方舟日历更新", "方舟日报刷新"], "强制刷新数据源并重新生成日历图片。", "", "/方舟日历刷新"),
    "方舟历史日程测试": (["方舟回溯测试", "方舟日历历史测试"], "生成仅含活动与寻访时间轴的历史测试图片，例如：/方舟历史日程测试 2026-07-01 2026-07-31。", "<开始日期> <结束日期>", "/方舟历史日程测试 2026-07-01 2026-07-31"),
}

USER_NAMES = ["方舟日历", "方舟生日", "方舟日历状态", "方舟订阅", "方舟取消订阅", "方舟订阅列表", "方舟日报帮助"]
ADMIN_NAMES = ["方舟日历刷新", "方舟历史日程测试"]
SUB_NAMES = ["方舟订阅", "方舟取消订阅", "方舟订阅列表"]


def rows(names: list[str]) -> list[dict]:
    return [
        {
            "name": name,
            "aliases": COMMANDS[name][0],
            "summary": COMMANDS[name][1],
            "argument_hint": COMMANDS[name][2],
            "example": COMMANDS[name][3],
        }
        for name in names
    ]


def build_context(mode: str) -> dict:
    subscribe = mode == "subscribe"
    return {
        "mode": mode,
        "title": "订阅可用日程" if subscribe else "罗德岛终端手册",
        "subtitle_en": "SUBSCRIPTION DIRECTORY" if subscribe else "COMMAND MANUAL",
        "lead": (
            "下面是当前可以订阅的活动与寻访，复制卡片里的命令就能订阅；"
            "在结束前一天的设定时间提醒你，不填时间默认中午 12:00。"
            if subscribe
            else "罗德岛行动日历的全部指令与当前可订阅日程都在这里，"
            "指令支持别名，订阅提醒在结束前一天送达。"
        ),
        "version": "0.4.0",
        "user_commands": rows(SUB_NAMES if subscribe else USER_NAMES),
        "admin_commands": [] if subscribe else rows(ADMIN_NAMES),
        "subscribable_items": SUBSCRIBABLE,
        "date_cn": "2026 / 08 / 06",
        "weekday": "星期四",
        "data_date_text": "2026-08-06",
        "hero": fixed_hero(),
        "static": {"font": data_uri(ROOT / "assets" / "SourceHanSerifCN-Medium-6.otf")},
    }


async def render(mode: str) -> Path:
    html = Template((ROOT / "templates" / "help.html").read_text("utf-8")).render(**build_context(mode))
    OUT.mkdir(parents=True, exist_ok=True)
    png = OUT / f"help_{mode}.png"
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1440, "height": 1200}, device_scale_factor=1)
        await page.set_content(html, wait_until="load")
        await page.wait_for_timeout(900)  # 等 11MB 字体真正生效，否则截到回退字体
        await page.screenshot(path=str(png), full_page=True, animations="disabled")
        await browser.close()
    return png


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "full"
    out = asyncio.run(render(mode))
    print(f"{out}  ({out.stat().st_size // 1024} KB)")

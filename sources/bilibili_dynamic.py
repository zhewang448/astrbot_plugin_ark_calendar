from __future__ import annotations

from datetime import datetime
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from .http import HttpClient

CN_TZ = ZoneInfo("Asia/Shanghai")


class BilibiliDynamicSource:
    """明日方舟官方B站动态数据源（基于RSS）"""

    # 明日方舟官方B站 UID: 161775300
    # RSSHub 公共实例（已验证可用，按优先级排序）
    DEFAULT_RSSHUB_INSTANCES = [
        "https://rsshub.liumingye.cn/bilibili/user/dynamic/161775300",
        "https://rsshub.ktachibana.party/bilibili/user/dynamic/161775300",
        "https://rsshub.pseudoyu.com/bilibili/user/dynamic/161775300",
    ]
    MAX_STATE_ITEMS = 500

    def __init__(self, http: HttpClient, cache, asset_cache):
        self.http = http
        self.cache = cache
        self.asset_cache = asset_cache
        self.last_fetch_ok = True
        self.last_error = ""
        self.last_failed_instance = ""
        self.last_success_at: datetime | None = None
        self.consecutive_failures = 0
        self.rsshub_instances = self.DEFAULT_RSSHUB_INSTANCES.copy()

    def set_custom_rsshub_url(self, base_url: str) -> None:
        """设置自定义RSSHub实例URL（用户自建时）。

        Args:
            base_url: RSSHub基础URL，如 "https://rsshub.example.com"
        """
        if base_url:
            uid = "161775300"
            custom_url = f"{base_url.rstrip('/')}/bilibili/user/dynamic/{uid}"
            # 将自定义URL放在最前面
            self.rsshub_instances = [custom_url] + self.DEFAULT_RSSHUB_INSTANCES

    async def recent_dynamics(self, limit: int = 10, download_images: bool = False) -> list[dict]:
        """获取最近的B站动态列表。

        Args:
            limit: 返回的动态数量，默认10条
            download_images: 是否下载图片到本地缓存

        Returns:
            动态列表，每条包含：
            - id: 动态ID（字符串）
            - title: 标题
            - link: 动态链接
            - pub_date: 发布时间（datetime对象）
            - description_html: 动态内容（HTML格式）
            - description_text: 动态内容（纯文本）
            - images: 图片URL列表
            - cached_images: 本地缓存的图片路径列表（仅当download_images=True）
            - dynamic_type: 动态类型（video/image/text/repost）
        """
        xml = await self._fetch_rss()
        if not xml:
            return []

        soup = BeautifulSoup(xml, "xml")
        items = soup.find_all("item")

        dynamics = []
        for item in items[:limit]:
            try:
                dynamic = self._parse_item(item)
                if dynamic:
                    if download_images and dynamic["images"]:
                        dynamic["cached_images"] = await self._download_images(dynamic["images"])
                    else:
                        dynamic["cached_images"] = []

                    dynamics.append(dynamic)
            except Exception:
                # 单条动态解析失败不影响其他动态
                continue

        return dynamics

    async def _fetch_rss(self) -> str:
        """获取RSS XML内容，失败时尝试备用镜像站。"""
        for url in self.rsshub_instances:
            try:
                xml = await self.http.text(url, timeout=15)
                if xml and BeautifulSoup(xml, "xml").find("item"):
                    self.last_fetch_ok = True
                    self.last_error = ""
                    self.last_success_at = datetime.now(CN_TZ)
                    self.consecutive_failures = 0
                    return xml
            except Exception as exc:
                self.last_error = str(exc)
                self.last_failed_instance = url
                self.consecutive_failures += 1
                continue

        self.last_fetch_ok = False
        return ""

    async def hydrate_images(self, dynamic: dict) -> dict:
        """为已确认需要投递的动态下载图片。"""
        if dynamic.get("images"):
            dynamic["cached_images"] = await self._download_images(dynamic["images"])
        else:
            dynamic["cached_images"] = []
        return dynamic

    def _parse_item(self, item) -> dict | None:
        """解析单条RSS item为动态数据。"""
        title_tag = item.find("title")
        link_tag = item.find("link")
        guid_tag = item.find("guid")
        pub_date_tag = item.find("pubDate")
        description_tag = item.find("description")

        if not (title_tag and link_tag and guid_tag):
            return None

        title = title_tag.get_text(strip=True)
        link = link_tag.get_text(strip=True)
        dynamic_id = guid_tag.get_text(strip=True)

        # 解析发布时间
        pub_date = None
        if pub_date_tag:
            try:
                pub_date_str = pub_date_tag.get_text(strip=True)
                pub_date = parsedate_to_datetime(pub_date_str).astimezone(CN_TZ)
            except (ValueError, AttributeError):
                pass

        # 解析动态内容（HTML）
        description_html = ""
        if description_tag:
            # CDATA内容
            description_html = str(description_tag.string or "")

        # 提取纯文本和图片
        description_text, images = self._parse_description(description_html)

        # 判断动态类型
        dynamic_type = self._classify_dynamic(title, description_text, images)

        return {
            "id": dynamic_id,
            "title": title,
            "link": link,
            "pub_date": pub_date,
            "description_html": description_html,
            "description_text": description_text,
            "images": images,
            "dynamic_type": dynamic_type,
        }

    def _parse_description(self, html: str) -> tuple[str, list[str]]:
        """从HTML描述中提取纯文本和图片URL。"""
        if not html:
            return "", []

        soup = BeautifulSoup(html, "html.parser")

        # 提取所有图片
        images = []
        for img in soup.find_all("img"):
            src = img.get("src", "")
            if src and src.startswith("http"):
                images.append(src)

        # 提取纯文本
        text = soup.get_text(separator="\n", strip=True)

        return text, images

    def _classify_dynamic(self, title: str, text: str, images: list[str] | None = None) -> str:
        """根据标题和内容判断动态类型。"""
        combined = f"{title} {text}".lower()

        if any(keyword in combined for keyword in ["pv", "预告", "宣传片"]):
            return "video"
        elif images or any(keyword in combined for keyword in ["立绘", "时装", "皮肤"]):
            return "image"
        elif any(keyword in combined for keyword in ["转发", "@", "互动"]):
            return "repost"
        else:
            return "text"

    @staticmethod
    def format_relative_time(pub_date: datetime | None) -> str:
        """将发布时间格式化为相对时间（如"2小时前"）。"""
        if not pub_date:
            return "未知时间"

        now = datetime.now(CN_TZ)
        delta_seconds = (now - pub_date.astimezone(CN_TZ)).total_seconds()
        if delta_seconds < 0:
            return "刚刚"

        if delta_seconds > 7 * 86400:
            return pub_date.strftime("%Y-%m-%d")
        elif delta_seconds >= 86400:
            return f"{int(delta_seconds // 86400)}天前"
        elif delta_seconds >= 3600:
            hours = int(delta_seconds // 3600)
            return f"{hours}小时前"
        elif delta_seconds >= 60:
            minutes = int(delta_seconds // 60)
            return f"{minutes}分钟前"
        else:
            return "刚刚"

    def should_push(self, dynamic: dict, push_types: list[str]) -> bool:
        """判断该动态是否应该推送。

        Args:
            dynamic: 动态数据
            push_types: 允许推送的类型列表，如 ["video", "image", "text"]
                       不包含"repost"意味着过滤转发

        Returns:
            是否应该推送
        """
        if not push_types:
            return True

        return dynamic["dynamic_type"] in push_types

    async def _download_images(self, image_urls: list[str]) -> list[str]:
        """下载B站图片到本地缓存。

        Args:
            image_urls: B站图片URL列表

        Returns:
            本地缓存路径列表
        """
        cached_paths = []

        for url in image_urls:
            try:
                # B站 RSS 返回的是 http:// 链接，需要转为 https:// 才能通过 AssetCache 的安全校验
                safe_url = url.replace("http://", "https://", 1) if url.startswith("http://") else url
                local_path = await self.asset_cache.download(safe_url)
                if local_path and local_path.exists():
                    cached_paths.append(str(local_path))
            except Exception:
                # 单张图片下载失败不影响其他图片
                continue

        return cached_paths

    def save_state(self, state: dict) -> None:
        """保存动态状态到缓存。

        Args:
            state: 状态字典
        """
        dynamics = state.get("dynamics")
        if isinstance(dynamics, dict) and len(dynamics) > self.MAX_STATE_ITEMS:
            # 保留最新状态；插入顺序由首次观察动态的时间决定。
            state["dynamics"] = dict(list(dynamics.items())[-self.MAX_STATE_ITEMS:])
        self.cache.save("bilibili_dynamic_state.json", state)

    def load_state(self) -> dict:
        """加载动态状态缓存。

        Returns:
            状态字典，包含 last_update 和 dynamics
        """
        state = self.cache.load("bilibili_dynamic_state.json")
        if not isinstance(state, dict):
            return {"last_update": None, "dynamics": {}}
        return state

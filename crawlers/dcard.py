import json
import requests
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict, Optional
import time
import logging

logger = logging.getLogger(__name__)

DCARD_API = "https://www.dcard.tw/service/api/v2"
COOKIES_FILE = Path(__file__).parent.parent / "dcard_cookies.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.dcard.tw/",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8",
    "X-Requested-With": "XMLHttpRequest",
}

_session: Optional[requests.Session] = None


def _load_cookies() -> dict:
    """Load cookies from dcard_cookies.json if it exists."""
    if not COOKIES_FILE.exists():
        return {}
    try:
        raw = json.loads(COOKIES_FILE.read_text(encoding="utf-8"))
        # Support both list-of-dicts (from browser extension export) and plain dict
        if isinstance(raw, list):
            return {c["name"]: c["value"] for c in raw if "name" in c and "value" in c}
        return raw
    except Exception as e:
        logger.warning(f"讀取 dcard_cookies.json 失敗: {e}")
        return {}


def _build_session() -> requests.Session:
    sess = requests.Session()
    sess.headers.update(HEADERS)
    cookies = _load_cookies()
    if cookies:
        sess.cookies.update(cookies)
        logger.info(f"Dcard: 載入 {len(cookies)} 個 cookie")
    else:
        logger.warning(
            "Dcard: 未找到 dcard_cookies.json，可能因 Cloudflare 防護而失敗。\n"
            "  → 請在瀏覽器登入 Dcard 後，用 EditThisCookie 等擴充功能匯出 cookie\n"
            "    並儲存至專案根目錄的 dcard_cookies.json"
        )
    return sess


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = _build_session()
    return _session


def reload_cookies():
    """重新載入 cookie（cookie 更新後呼叫此函式）"""
    global _session
    _session = None


def _get(url: str, params: dict = None, retries: int = 2) -> Optional[dict]:
    for attempt in range(retries):
        try:
            resp = _get_session().get(url, params=params, timeout=15)
            if resp.status_code == 403:
                logger.warning(
                    "Dcard 403：請更新 dcard_cookies.json 中的 cookie 後重試\n"
                    "  取得方式：在瀏覽器開啟 dcard.tw → DevTools → Application → Cookies → 複製匯出"
                )
                return None
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError:
            return None
        except Exception as e:
            logger.warning(f"Dcard GET 失敗 ({attempt+1}/{retries}): {url} — {e}")
            if attempt < retries - 1:
                time.sleep(2)
    return None


def _parse_time(iso_str: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except Exception:
        return None


def _normalize_post(post: dict, forum_alias: str) -> Dict:
    post_id = str(post.get("id", ""))
    return {
        "source": "dcard",
        "board": forum_alias,
        "article_id": f"dcard_{forum_alias}_{post_id}",
        "title": post.get("title", ""),
        "content": (post.get("excerpt") or post.get("content") or "")[:2000],
        "author": post.get("anonymousSchool") or post.get("school") or "匿名",
        "url": f"https://www.dcard.tw/f/{forum_alias}/p/{post_id}",
        "comment_count": post.get("commentCount", 0),
        "reaction_count": post.get("likeCount", 0),
        "push_count": 0,
        "published_at": _parse_time(post.get("createdAt", "")),
        "crawled_at": datetime.now(timezone.utc),
    }


def crawl_forum(forum_alias: str, popular: bool = False, limit: int = 30) -> List[Dict]:
    if forum_alias == "trending":
        url = f"{DCARD_API}/posts"
        params = {"popular": "true", "limit": min(limit, 30)}
    else:
        url = f"{DCARD_API}/forums/{forum_alias}/posts"
        params = {"popular": str(popular).lower(), "limit": min(limit, 30)}

    data = _get(url, params=params)
    if not data or not isinstance(data, list):
        return []
    return [_normalize_post(p, forum_alias) for p in data]


def search_keyword(keyword: str, limit: int = 50) -> List[Dict]:
    articles = []
    url = f"{DCARD_API}/search/posts"
    after = None

    while len(articles) < limit:
        params = {"query": keyword, "limit": 20}
        if after:
            params["after"] = after

        data = _get(url, params=params)
        if not data or not isinstance(data, list) or len(data) == 0:
            break

        for post in data:
            forum = post.get("forumAlias", "unknown")
            articles.append(_normalize_post(post, forum))

        if len(data) < 20:
            break

        after = data[-1].get("id")
        time.sleep(0.5)

    return articles[:limit]


def get_post_detail(forum_alias: str, post_id: str) -> Optional[Dict]:
    url = f"{DCARD_API}/posts/{post_id}"
    data = _get(url)
    if not data:
        return None
    return _normalize_post(data, forum_alias)

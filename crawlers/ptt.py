import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone
from typing import List, Dict, Optional
import re
import time
import logging
from datetime import timedelta

logger = logging.getLogger(__name__)

PTT_BASE = "https://www.ptt.cc"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Cookie": "over18=1",  # 同意 18 歲條款
}


def _get(url: str, retries: int = 3) -> Optional[requests.Response]:
    for i in range(retries):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=10)
            resp.raise_for_status()
            return resp
        except Exception as e:
            logger.warning(f"PTT GET 失敗 ({i+1}/{retries}): {url} — {e}")
            time.sleep(2 ** i)
    return None


def _parse_push_count(push_str: str) -> int:
    """將 PTT 推文標記轉為數字（爆→100，XX→-100）"""
    push_str = (push_str or "").strip()
    if push_str == "爆":
        return 100
    if push_str.startswith("X"):
        try:
            return -int(push_str[1:]) * 10
        except ValueError:
            return -100
    try:
        return int(push_str)
    except ValueError:
        return 0


def _parse_date(date_str: str) -> Optional[datetime]:
    """解析 PTT 日期格式：'5/28'，補年份並修正跨年情況"""
    now = datetime.now(timezone.utc)
    try:
        m, d = map(int, date_str.strip().split("/"))
        dt = datetime(now.year, m, d, tzinfo=timezone.utc)
        # 若日期在未來（如現在1月、文章標示12月），往前補一年
        if dt > now + timedelta(days=1):
            dt = dt.replace(year=now.year - 1)
        return dt
    except Exception:
        pass
    return None


def crawl_board(board: str, max_pages: int = 30, days_back: int = 14) -> List[Dict]:
    """爬取指定 PTT 看板的文章列表，最多回溯 days_back 天"""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
    articles = []
    url = f"{PTT_BASE}/bbs/{board}/index.html"

    for _ in range(max_pages):
        resp = _get(url)
        if not resp:
            break

        soup = BeautifulSoup(resp.text, "lxml")

        # 找上一頁連結（繼續往回爬）
        prev_link = soup.select_one("a.btn.wide:-soup-contains('上頁')")
        if not prev_link:
            # 相容性寫法
            for a in soup.select("a.btn.wide"):
                if "上頁" in a.text:
                    prev_link = a
                    break

        page_too_old = False
        for row in soup.select("div.r-ent"):
            try:
                title_tag = row.select_one("div.title a")
                if not title_tag:
                    continue  # 已刪除文章

                push_tag = row.select_one("div.nrec span")
                date_tag = row.select_one("div.date")
                author_tag = row.select_one("div.author")
                pub = _parse_date(date_tag.text.strip()) if date_tag else None

                if pub and pub < cutoff:
                    page_too_old = True
                    continue

                href = title_tag["href"]
                article_id = href.split("/")[-1].replace(".html", "")

                articles.append({
                    "source": "ptt",
                    "board": board,
                    "article_id": f"ptt_{board}_{article_id}",
                    "title": title_tag.text.strip(),
                    "url": PTT_BASE + href,
                    "push_count": _parse_push_count(push_tag.text if push_tag else "0"),
                    "author": author_tag.text.strip() if author_tag else "",
                    "published_at": pub,
                    "comment_count": 0,
                    "reaction_count": 0,
                    "content": "",
                })
            except Exception as e:
                logger.debug(f"解析 PTT 文章列表列失敗: {e}")

        if page_too_old or not (prev_link and prev_link.get("href")):
            break
        url = PTT_BASE + prev_link["href"]
        time.sleep(0.5)

    return articles


def crawl_article_detail(article: Dict) -> Dict:
    """爬取單篇文章內文與推文數"""
    resp = _get(article["url"])
    if not resp:
        return article

    soup = BeautifulSoup(resp.text, "lxml")

    # 內文
    content_div = soup.select_one("#main-content")
    if content_div:
        # 移除 metadata 區塊
        for tag in content_div.select("div.article-metaline, div.article-metaline-right, div.push"):
            tag.decompose()
        article["content"] = content_div.get_text(separator="\n", strip=True)[:2000]

    # 計算推/噓/箭頭
    pushes = soup.select("div.push")
    push = sum(1 for p in pushes if "推" in (p.select_one("span.push-tag") or BeautifulSoup("", "lxml")).text)
    boo = sum(1 for p in pushes if "噓" in (p.select_one("span.push-tag") or BeautifulSoup("", "lxml")).text)
    article["comment_count"] = len(pushes)
    article["push_count"] = push - boo  # 淨推文數

    return article


def search_board_by_keyword(board: str, keyword: str, max_pages: int = 20, days_back: int = 14) -> List[Dict]:
    """在特定看板以關鍵字搜尋，最多回溯 days_back 天"""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
    articles = []
    url = f"{PTT_BASE}/bbs/{board}/search?q={requests.utils.quote(keyword)}"

    for _ in range(max_pages):
        resp = _get(url)
        if not resp:
            break

        soup = BeautifulSoup(resp.text, "lxml")

        page_too_old = False
        for row in soup.select("div.r-ent"):
            try:
                title_tag = row.select_one("div.title a")
                if not title_tag:
                    continue

                push_tag = row.select_one("div.nrec span")
                date_tag = row.select_one("div.date")
                author_tag = row.select_one("div.author")
                pub = _parse_date(date_tag.text.strip()) if date_tag else None

                if pub and pub < cutoff:
                    page_too_old = True
                    continue

                href = title_tag["href"]
                article_id = href.split("/")[-1].replace(".html", "")

                articles.append({
                    "source": "ptt",
                    "board": board,
                    "article_id": f"ptt_{board}_{article_id}",
                    "title": title_tag.text.strip(),
                    "url": PTT_BASE + href,
                    "push_count": _parse_push_count(push_tag.text if push_tag else "0"),
                    "author": author_tag.text.strip() if author_tag else "",
                    "published_at": pub,
                    "comment_count": 0,
                    "reaction_count": 0,
                    "content": "",
                })
            except Exception as e:
                logger.debug(f"解析 PTT 搜尋列表失敗: {e}")

        prev_link = None
        for a in soup.select("a.btn.wide"):
            if "上頁" in a.text:
                prev_link = a
                break

        if page_too_old or not (prev_link and prev_link.get("href")):
            break
        url = PTT_BASE + prev_link["href"]
        time.sleep(0.5)

    return articles

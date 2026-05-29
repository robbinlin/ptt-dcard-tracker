import logging
from datetime import datetime, timezone
from typing import List, Dict, Optional
from sqlalchemy.orm import Session

from crawlers import ptt as ptt_crawler
from crawlers import dcard as dcard_crawler
from models.database import Article, KeywordMatch, CrawlLog, get_db, SessionLocal
from services.scorer import score_articles, compute_keyword_frequency
from config import settings

logger = logging.getLogger(__name__)


def _upsert_article(db: Session, art: Dict, keywords: List[str]) -> Optional[Article]:
    """新增或更新文章，並記錄關鍵字匹配"""
    existing = db.query(Article).filter(Article.article_id == art["article_id"]).first()

    if existing:
        # 更新動態欄位
        existing.comment_count = art.get("comment_count", existing.comment_count)
        existing.reaction_count = art.get("reaction_count", existing.reaction_count)
        existing.push_count = art.get("push_count", existing.push_count)
        existing.hot_score = art.get("hot_score", existing.hot_score)
        db_art = existing
    else:
        db_art = Article(
            source=art["source"],
            board=art["board"],
            article_id=art["article_id"],
            title=art["title"],
            content=art.get("content", ""),
            author=art.get("author", ""),
            url=art["url"],
            comment_count=art.get("comment_count", 0),
            reaction_count=art.get("reaction_count", 0),
            push_count=art.get("push_count", 0),
            published_at=art.get("published_at"),
            hot_score=art.get("hot_score", 0.0),
        )
        db.add(db_art)

    db.flush()

    # 關鍵字匹配記錄
    full_text = f"{art.get('title', '')} {art.get('content', '')}"
    for kw in keywords:
        freq = compute_keyword_frequency(full_text, kw)
        if freq > 0:
            match = (
                db.query(KeywordMatch)
                .filter(KeywordMatch.article_id == db_art.id, KeywordMatch.keyword == kw)
                .first()
            )
            if match:
                match.frequency = freq
            else:
                db.add(KeywordMatch(article_id=db_art.id, keyword=kw, frequency=freq))

    return db_art


def _log_crawl(db: Session, source: str, board: str) -> CrawlLog:
    log = CrawlLog(source=source, board=board)
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


def _finish_log(db: Session, log: CrawlLog, count: int, status: str, error: str = ""):
    log.finished_at = datetime.now(timezone.utc)
    log.articles_found = count
    log.status = status
    log.error_message = error
    db.commit()


def run_ptt_crawl(keywords: List[str], boards: List[str] = None, fetch_detail: bool = False):
    """爬取 PTT 指定看板，依關鍵字搜尋並儲存"""
    if boards is None:
        boards = settings.ptt_boards

    db: Session = SessionLocal()
    try:
        for board in boards:
            log = _log_crawl(db, "ptt", board)
            try:
                all_articles: List[Dict] = []
                # 各關鍵字搜尋
                for kw in keywords:
                    arts = ptt_crawler.search_board_by_keyword(board, kw, max_pages=20, days_back=14)
                    all_articles.extend(arts)

                # 去重
                seen = {}
                for a in all_articles:
                    seen[a["article_id"]] = a
                unique_articles = list(seen.values())

                # 可選：爬取文章內文
                if fetch_detail:
                    for art in unique_articles[:20]:  # 限制避免過快
                        ptt_crawler.crawl_article_detail(art)

                # 評分
                scored = score_articles(unique_articles, keywords)

                # 儲存
                for art in scored:
                    _upsert_article(db, art, keywords)
                db.commit()

                _finish_log(db, log, len(scored), "success")
                logger.info(f"PTT [{board}] 爬取完成，共 {len(scored)} 篇")

            except Exception as e:
                db.rollback()
                _finish_log(db, log, 0, "error", str(e))
                logger.error(f"PTT [{board}] 爬取失敗: {e}")
    finally:
        db.close()


def run_dcard_crawl(keywords: List[str], forums: List[str] = None):
    """爬取 Dcard，依關鍵字搜尋並儲存"""
    if forums is None:
        forums = settings.dcard_forums

    db: Session = SessionLocal()
    try:
        # 1. 關鍵字搜尋（跨論壇）
        for kw in keywords:
            log = _log_crawl(db, "dcard", f"search:{kw}")
            try:
                arts = dcard_crawler.search_keyword(kw, limit=50)
                scored = score_articles(arts, keywords)
                for art in scored:
                    _upsert_article(db, art, keywords)
                db.commit()
                _finish_log(db, log, len(scored), "success")
                logger.info(f"Dcard 關鍵字[{kw}] 搜尋完成，共 {len(scored)} 篇")
            except Exception as e:
                db.rollback()
                _finish_log(db, log, 0, "error", str(e))
                logger.error(f"Dcard 關鍵字[{kw}] 搜尋失敗: {e}")

        # 2. 熱門論壇爬取
        for forum in forums:
            log = _log_crawl(db, "dcard", forum)
            try:
                arts = dcard_crawler.crawl_forum(forum, popular=True, limit=30)
                # 過濾含關鍵字文章
                matched = [
                    a for a in arts
                    if any(
                        kw.lower() in f"{a['title']} {a['content']}".lower()
                        for kw in keywords
                    )
                ] or arts  # 若無匹配，保留全部
                scored = score_articles(matched, keywords)
                for art in scored:
                    _upsert_article(db, art, keywords)
                db.commit()
                _finish_log(db, log, len(scored), "success")
                logger.info(f"Dcard [{forum}] 爬取完成，共 {len(scored)} 篇")
            except Exception as e:
                db.rollback()
                _finish_log(db, log, 0, "error", str(e))
                logger.error(f"Dcard [{forum}] 爬取失敗: {e}")
    finally:
        db.close()


def run_full_crawl(keywords: Optional[List[str]] = None):
    """執行完整爬取（PTT + Dcard）"""
    if keywords is None:
        keywords = settings.default_keywords
    logger.info(f"開始完整爬取，關鍵字: {keywords}")
    run_ptt_crawl(keywords)
    run_dcard_crawl(keywords)
    logger.info("完整爬取完成")

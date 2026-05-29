from fastapi import APIRouter, Depends, Query, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import or_, desc
from typing import List, Optional
from datetime import datetime, timezone
from pydantic import BaseModel

from models.database import Article, KeywordMatch, CrawlLog, get_db
from services.crawler_service import run_ptt_crawl, run_dcard_crawl, run_full_crawl
from services.scheduler import get_scheduler_status, start_scheduler, stop_scheduler
from config import settings

router = APIRouter()


# ── Schemas ────────────────────────────────────────────────────────────────


class ArticleOut(BaseModel):
    id: int
    source: str
    board: str
    title: str
    url: str
    author: str
    comment_count: int
    reaction_count: int
    push_count: int
    hot_score: float
    published_at: Optional[datetime]
    crawled_at: Optional[datetime]
    keyword_frequencies: Optional[dict] = None

    class Config:
        from_attributes = True


class CrawlRequest(BaseModel):
    keywords: Optional[List[str]] = None
    sources: Optional[List[str]] = None   # ["ptt", "dcard"] or subset
    boards: Optional[List[str]] = None
    forums: Optional[List[str]] = None


class SchedulerConfig(BaseModel):
    keywords: Optional[List[str]] = None


class KeywordConfig(BaseModel):
    keywords: List[str]


# ── 文章查詢 ────────────────────────────────────────────────────────────────


@router.get("/articles", response_model=List[ArticleOut], tags=["文章"])
def list_articles(
    keyword: Optional[str] = Query(None, description="關鍵字篩選"),
    source: Optional[str] = Query(None, description="來源：ptt / dcard"),
    board: Optional[str] = Query(None, description="看板/論壇名稱"),
    hours: int = Query(24, description="只顯示最近 N 小時的文章"),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
    db: Session = Depends(get_db),
):
    """取得熱門文章列表，依 hot_score 降序排列"""
    q = db.query(Article)

    if source:
        q = q.filter(Article.source == source)
    if board:
        q = q.filter(Article.board == board)

    if keyword:
        q = q.filter(
            or_(
                Article.title.contains(keyword),
                Article.content.contains(keyword),
            )
        )

    if hours > 0:
        cutoff = datetime.now(timezone.utc).timestamp() - hours * 3600
        # SQLite 相容寫法
        q = q.filter(Article.crawled_at >= datetime.fromtimestamp(cutoff, tz=timezone.utc))

    total = q.count()
    articles = q.order_by(desc(Article.hot_score)).offset(offset).limit(limit).all()

    result = []
    for art in articles:
        out = ArticleOut.model_validate(art)
        # 附上關鍵字頻率
        matches = db.query(KeywordMatch).filter(KeywordMatch.article_id == art.id).all()
        out.keyword_frequencies = {m.keyword: m.frequency for m in matches}
        result.append(out)

    return result


@router.get("/articles/trending", response_model=List[ArticleOut], tags=["文章"])
def trending_articles(
    limit: int = Query(20, le=100),
    hours: int = Query(24),
    db: Session = Depends(get_db),
):
    """取得綜合熱門排行（跨 PTT + Dcard）"""
    cutoff = datetime.now(timezone.utc).timestamp() - hours * 3600
    articles = (
        db.query(Article)
        .filter(Article.crawled_at >= datetime.fromtimestamp(cutoff, tz=timezone.utc))
        .order_by(desc(Article.hot_score))
        .limit(limit)
        .all()
    )
    result = []
    for art in articles:
        out = ArticleOut.model_validate(art)
        matches = db.query(KeywordMatch).filter(KeywordMatch.article_id == art.id).all()
        out.keyword_frequencies = {m.keyword: m.frequency for m in matches}
        result.append(out)
    return result


@router.get("/articles/{article_id}", response_model=ArticleOut, tags=["文章"])
def get_article(article_id: int, db: Session = Depends(get_db)):
    art = db.query(Article).filter(Article.id == article_id).first()
    if not art:
        raise HTTPException(status_code=404, detail="文章不存在")
    out = ArticleOut.model_validate(art)
    matches = db.query(KeywordMatch).filter(KeywordMatch.article_id == art.id).all()
    out.keyword_frequencies = {m.keyword: m.frequency for m in matches}
    return out


# ── 關鍵字統計 ──────────────────────────────────────────────────────────────


@router.get("/keywords/stats", tags=["關鍵字"])
def keyword_stats(
    hours: int = Query(24),
    db: Session = Depends(get_db),
):
    """各關鍵字的文章數與總頻率統計"""
    cutoff = datetime.now(timezone.utc).timestamp() - hours * 3600
    article_ids = [
        a.id for a in db.query(Article.id)
        .filter(Article.crawled_at >= datetime.fromtimestamp(cutoff, tz=timezone.utc))
        .all()
    ]
    if not article_ids:
        return []

    from sqlalchemy import func
    rows = (
        db.query(
            KeywordMatch.keyword,
            func.count(KeywordMatch.article_id).label("article_count"),
            func.sum(KeywordMatch.frequency).label("total_frequency"),
        )
        .filter(KeywordMatch.article_id.in_(article_ids))
        .group_by(KeywordMatch.keyword)
        .order_by(desc("total_frequency"))
        .all()
    )
    return [
        {"keyword": r.keyword, "article_count": r.article_count, "total_frequency": r.total_frequency}
        for r in rows
    ]


# ── 爬取控制 ────────────────────────────────────────────────────────────────


@router.post("/crawl/trigger", tags=["爬取控制"])
def trigger_crawl(req: CrawlRequest, background_tasks: BackgroundTasks):
    """手動觸發爬取"""
    keywords = req.keywords or settings.default_keywords
    sources = req.sources or ["ptt", "dcard"]

    if "ptt" in sources:
        background_tasks.add_task(run_ptt_crawl, keywords, req.boards)
    if "dcard" in sources:
        background_tasks.add_task(run_dcard_crawl, keywords, req.forums)

    return {"message": "爬取任務已在背景啟動", "keywords": keywords, "sources": sources}


@router.get("/crawl/logs", tags=["爬取控制"])
def crawl_logs(
    limit: int = Query(20, le=100),
    db: Session = Depends(get_db),
):
    """查詢最近爬取記錄"""
    logs = db.query(CrawlLog).order_by(desc(CrawlLog.started_at)).limit(limit).all()
    return [
        {
            "id": l.id,
            "source": l.source,
            "board": l.board,
            "started_at": l.started_at,
            "finished_at": l.finished_at,
            "articles_found": l.articles_found,
            "status": l.status,
            "error_message": l.error_message,
        }
        for l in logs
    ]


# ── 排程管理 ────────────────────────────────────────────────────────────────


@router.get("/scheduler/status", tags=["排程"])
def scheduler_status():
    return get_scheduler_status()


@router.post("/scheduler/start", tags=["排程"])
def scheduler_start(req: SchedulerConfig, background_tasks: BackgroundTasks):
    keywords = req.keywords or settings.default_keywords
    background_tasks.add_task(start_scheduler, keywords)
    return {"message": f"排程已啟動，每 {settings.crawl_interval_minutes} 分鐘執行一次"}


@router.post("/scheduler/stop", tags=["排程"])
def scheduler_stop():
    stop_scheduler()
    return {"message": "排程已停止"}


# ── 設定管理 ────────────────────────────────────────────────────────────────


@router.get("/config/keywords", tags=["設定"])
def get_keywords():
    return {"keywords": settings.default_keywords}


@router.put("/config/keywords", tags=["設定"])
def update_keywords(req: KeywordConfig):
    settings.default_keywords = req.keywords
    return {"message": "關鍵字已更新", "keywords": settings.default_keywords}


@router.post("/config/dcard-cookies/reload", tags=["設定"])
def reload_dcard_cookies():
    """重新從 dcard_cookies.json 載入 Dcard cookie（更新 cookie 檔後呼叫）"""
    from crawlers.dcard import reload_cookies
    reload_cookies()
    return {"message": "Dcard cookie 已重新載入"}

from fastapi import APIRouter, Depends, Query, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from sqlalchemy import or_, desc, func
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
    """取得熱門文章列表，依 hot_score 降序排列（只含目前追蹤關鍵字）"""
    current_kws = settings.default_keywords
    q = (
        db.query(Article)
        .join(KeywordMatch, KeywordMatch.article_id == Article.id)
        .filter(KeywordMatch.keyword.in_(current_kws))
        .distinct()
    )

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
    """取得綜合熱門排行（跨 PTT + Dcard，只含目前追蹤關鍵字）"""
    cutoff = datetime.now(timezone.utc).timestamp() - hours * 3600
    current_kws = settings.default_keywords
    articles = (
        db.query(Article)
        .join(KeywordMatch, KeywordMatch.article_id == Article.id)
        .filter(
            KeywordMatch.keyword.in_(current_kws),
            Article.crawled_at >= datetime.fromtimestamp(cutoff, tz=timezone.utc),
        )
        .distinct()
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
    """各關鍵字的文章數與總頻率統計（只含目前追蹤關鍵字）"""
    cutoff = datetime.now(timezone.utc).timestamp() - hours * 3600
    current_kws = settings.default_keywords
    rows = (
        db.query(
            KeywordMatch.keyword,
            func.count(KeywordMatch.article_id).label("article_count"),
            func.sum(KeywordMatch.frequency).label("total_frequency"),
        )
        .join(Article, Article.id == KeywordMatch.article_id)
        .filter(
            KeywordMatch.keyword.in_(current_kws),
            Article.crawled_at >= datetime.fromtimestamp(cutoff, tz=timezone.utc),
        )
        .group_by(KeywordMatch.keyword)
        .order_by(desc("total_frequency"))
        .all()
    )
    if not rows:
        return []
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


# ── 文字雲資料 ──────────────────────────────────────────────────────────────

_STOP_WORDS = {
    "的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都", "一", "一個",
    "上", "也", "很", "到", "說", "要", "去", "你", "會", "著", "沒有", "看", "好",
    "自己", "這", "那", "來", "他", "她", "它", "們", "跟", "與", "及", "或", "但",
    "因為", "所以", "如果", "雖然", "已經", "可以", "這個", "那個", "什麼", "怎麼",
    "Re", "re", "Fw", "fw", "討論", "問卦", "新聞", "公告", "轉錄", "分享", "請問",
}


@router.get("/wordcloud-data", tags=["文字雲"])
def wordcloud_data(
    hours: int = Query(1440, description="統計最近 N 小時"),
    limit: int = Query(80, description="最多回傳幾個詞"),
    db: Session = Depends(get_db),
):
    """從文章標題提取高頻詞，供文字雲使用"""
    import jieba
    from collections import Counter

    cutoff = datetime.fromtimestamp(
        datetime.now(timezone.utc).timestamp() - hours * 3600, tz=timezone.utc
    )
    current_kws = settings.default_keywords

    articles = (
        db.query(Article.title)
        .join(KeywordMatch, KeywordMatch.article_id == Article.id)
        .filter(
            KeywordMatch.keyword.in_(current_kws),
            Article.crawled_at >= cutoff,
        )
        .distinct()
        .all()
    )

    counter: Counter = Counter()
    for (title,) in articles:
        words = jieba.cut(title, cut_all=False)
        for w in words:
            w = w.strip()
            if len(w) >= 2 and w not in _STOP_WORDS:
                counter[w] += 1

    # 追蹤關鍵字權重加倍，確保一定出現在雲中
    kw_stats = (
        db.query(KeywordMatch.keyword, func.sum(KeywordMatch.frequency).label("freq"))
        .join(Article, Article.id == KeywordMatch.article_id)
        .filter(KeywordMatch.keyword.in_(current_kws), Article.crawled_at >= cutoff)
        .group_by(KeywordMatch.keyword)
        .all()
    )
    for row in kw_stats:
        counter[row.keyword] += row.freq * 2

    words_list = [{"text": w, "weight": c} for w, c in counter.most_common(limit)]
    return words_list


# ── 熱門議題 ────────────────────────────────────────────────────────────────


@router.get("/topics", tags=["熱門議題"])
def get_topics(
    hours: int = Query(336, description="統計最近 N 小時（預設 336 = 2 週）"),
    top_articles: int = Query(5, description="每個議題顯示前 N 篇文章"),
    db: Session = Depends(get_db),
):
    """各關鍵字的熱門議題摘要（只含目前追蹤關鍵字）"""
    cutoff = datetime.fromtimestamp(
        datetime.now(timezone.utc).timestamp() - hours * 3600, tz=timezone.utc
    )
    current_kws = settings.default_keywords

    # 關鍵字統計（限定目前關鍵字）
    rows = (
        db.query(
            KeywordMatch.keyword,
            func.count(KeywordMatch.article_id).label("article_count"),
            func.sum(KeywordMatch.frequency).label("total_frequency"),
        )
        .join(Article, Article.id == KeywordMatch.article_id)
        .filter(
            KeywordMatch.keyword.in_(current_kws),
            Article.crawled_at >= cutoff,
        )
        .group_by(KeywordMatch.keyword)
        .order_by(desc("total_frequency"))
        .all()
    )

    topics = []
    for row in rows:
        # 該關鍵字下熱門文章
        arts = (
            db.query(Article)
            .join(KeywordMatch, KeywordMatch.article_id == Article.id)
            .filter(KeywordMatch.keyword == row.keyword, Article.crawled_at >= cutoff)
            .order_by(desc(Article.hot_score))
            .limit(top_articles)
            .all()
        )
        topics.append({
            "keyword": row.keyword,
            "article_count": row.article_count,
            "total_frequency": row.total_frequency,
            "top_articles": [
                {
                    "id": a.id,
                    "title": a.title,
                    "url": a.url,
                    "source": a.source,
                    "board": a.board,
                    "hot_score": a.hot_score,
                    "published_at": a.published_at,
                }
                for a in arts
            ],
        })
    return topics


# ── Dashboard ───────────────────────────────────────────────────────────────

_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PTT / Dcard 熱門議題追蹤器</title>
<script src="https://cdn.jsdelivr.net/npm/wordcloud@1.2.2/src/wordcloud2.js"></script>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         background: #f4f6f9; color: #333; }
  header { background: #1a1a2e; color: #fff; padding: 20px 32px; }
  header h1 { font-size: 1.4rem; font-weight: 600; }
  header p { font-size: 0.85rem; opacity: 0.7; margin-top: 4px; }
  .container { max-width: 1100px; margin: 0 auto; padding: 24px 16px; }
  .card { background: #fff; border-radius: 10px; box-shadow: 0 1px 4px rgba(0,0,0,.08);
          padding: 20px; margin-bottom: 24px; }
  .card h2 { font-size: 1rem; font-weight: 600; margin-bottom: 16px;
             border-left: 3px solid #4f46e5; padding-left: 10px; }
  #wc-canvas { width: 100%; height: 400px; }
  .topics-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 16px; }
  .topic-card { border: 1px solid #e5e7eb; border-radius: 8px; padding: 14px; }
  .topic-card h3 { font-size: 1rem; margin-bottom: 6px; display: flex; align-items: center; gap: 8px; }
  .badge { background: #ede9fe; color: #5b21b6; font-size: 0.75rem;
           padding: 2px 8px; border-radius: 999px; }
  .topic-meta { font-size: 0.78rem; color: #6b7280; margin-bottom: 10px; }
  .article-list { list-style: none; }
  .article-list li { padding: 5px 0; border-top: 1px solid #f3f4f6; font-size: 0.82rem; }
  .article-list a { color: #4f46e5; text-decoration: none; }
  .article-list a:hover { text-decoration: underline; }
  .src-ptt { color: #16a34a; font-weight: 600; }
  .src-dcard { color: #d97706; font-weight: 600; }
  .score { color: #9ca3af; font-size: 0.75rem; margin-left: 4px; }
  .loading { text-align: center; padding: 40px; color: #9ca3af; }
  .stat-bar { display: flex; gap: 24px; flex-wrap: wrap; margin-bottom: 8px; }
  .stat { background: #f9fafb; border-radius: 6px; padding: 10px 16px; text-align: center; }
  .stat .num { font-size: 1.4rem; font-weight: 700; color: #4f46e5; }
  .stat .lbl { font-size: 0.75rem; color: #6b7280; }
  select { padding: 6px 10px; border: 1px solid #d1d5db; border-radius: 6px;
           font-size: 0.85rem; cursor: pointer; }
  .btn-crawl { background: #4f46e5; color: #fff; border: none; border-radius: 6px;
               padding: 8px 18px; font-size: 0.85rem; cursor: pointer; font-weight: 600;
               display: flex; align-items: center; gap: 6px; transition: background .15s; }
  .btn-crawl:hover { background: #4338ca; }
  .btn-crawl:disabled { background: #a5b4fc; cursor: not-allowed; }
  #crawl-status { font-size: 0.8rem; color: #6b7280; margin-top: 6px; min-height: 18px; }
  .kw-list { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 14px; min-height: 32px; }
  .kw-tag { background: #ede9fe; color: #5b21b6; border-radius: 999px; padding: 4px 12px;
            font-size: 0.85rem; display: flex; align-items: center; gap: 6px; }
  .kw-tag button { background: none; border: none; color: #7c3aed; cursor: pointer;
                   font-size: 1rem; line-height: 1; padding: 0; }
  .kw-tag button:hover { color: #dc2626; }
  .kw-input-row { display: flex; gap: 8px; }
  .kw-input { flex: 1; padding: 7px 12px; border: 1px solid #d1d5db; border-radius: 6px;
              font-size: 0.85rem; }
  .kw-input:focus { outline: 2px solid #4f46e5; border-color: transparent; }
  .btn-add { background: #4f46e5; color: #fff; border: none; border-radius: 6px;
             padding: 7px 16px; font-size: 0.85rem; cursor: pointer; font-weight: 600; }
  .btn-add:hover { background: #4338ca; }
  .btn-save { background: #059669; color: #fff; border: none; border-radius: 6px;
              padding: 7px 16px; font-size: 0.85rem; cursor: pointer; font-weight: 600; }
  .btn-save:hover { background: #047857; }
  .btn-save:disabled { background: #6ee7b7; cursor: not-allowed; }
  #kw-msg { font-size: 0.8rem; margin-top: 8px; min-height: 18px; }
</style>
</head>
<body>
<header>
  <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px">
    <div>
      <h1>PTT / Dcard 熱門議題追蹤器</h1>
      <p>依關鍵字分析過去 <span id="period-label">2 週</span> 的熱門文章</p>
    </div>
    <div style="text-align:right">
      <button class="btn-crawl" id="crawl-btn" onclick="triggerCrawl()">
        <span id="crawl-icon">🔄</span> 立即爬取
      </button>
      <div id="crawl-status"></div>
    </div>
  </div>
</header>
<div class="container">
  <div class="card">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
      <h2 style="border:none;padding:0;margin:0">📊 總覽</h2>
      <div style="display:flex;gap:8px;align-items:center">
        <label style="font-size:.85rem">時間範圍</label>
        <select id="hours-select" onchange="reload()">
          <option value="168">最近 1 週</option>
          <option value="336">最近 2 週</option>
          <option value="720">最近 30 天</option>
          <option value="1440" selected>最近 60 天</option>
        </select>
      </div>
    </div>
    <div class="stat-bar" id="stats"><div class="loading">載入中…</div></div>
  </div>

  <div class="card">
    <h2>⚙️ 追蹤關鍵字</h2>
    <div class="kw-list" id="kw-list"><div class="loading">載入中…</div></div>
    <div class="kw-input-row">
      <input class="kw-input" id="kw-input" placeholder="輸入新關鍵字…"
             onkeydown="if(event.key==='Enter') addKeyword()">
      <button class="btn-add" onclick="addKeyword()">＋ 新增</button>
      <button class="btn-save" id="kw-save" onclick="saveKeywords()">儲存</button>
    </div>
    <div id="kw-msg"></div>
  </div>

  <div class="card">
    <h2>☁️ 關鍵字文字雲</h2>
    <canvas id="wc-canvas"></canvas>
  </div>

  <div class="card">
    <h2>🔥 熱門議題</h2>
    <div class="topics-grid" id="topics"><div class="loading">載入中…</div></div>
  </div>
</div>

<script>
let _keywords = [];

function renderKeywords() {
  document.getElementById('kw-list').innerHTML = _keywords.length
    ? _keywords.map((kw, i) => `
        <span class="kw-tag">${kw}
          <button onclick="removeKeyword(${i})" title="刪除">×</button>
        </span>`).join('')
    : '<span style="color:#9ca3af;font-size:.85rem">尚無關鍵字</span>';
}

function removeKeyword(i) {
  _keywords.splice(i, 1);
  renderKeywords();
}

function addKeyword() {
  const input = document.getElementById('kw-input');
  const kw = input.value.trim();
  if (!kw) return;
  if (_keywords.includes(kw)) { input.value = ''; return; }
  _keywords.push(kw);
  input.value = '';
  renderKeywords();
}

async function loadKeywords() {
  const res = await fetch('/api/v1/config/keywords');
  const data = await res.json();
  _keywords = data.keywords || [];
  renderKeywords();
}

async function saveKeywords() {
  const btn = document.getElementById('kw-save');
  const msg = document.getElementById('kw-msg');
  btn.disabled = true;
  try {
    await fetch('/api/v1/config/keywords', {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({keywords: _keywords})
    });
    msg.innerHTML = '<span style="color:#059669">✅ 已儲存</span>';
    setTimeout(() => { msg.textContent = ''; }, 3000);
  } catch(e) {
    msg.innerHTML = '<span style="color:#dc2626">❌ 儲存失敗</span>';
  } finally {
    btn.disabled = false;
  }
}

async function triggerCrawl() {
  const btn = document.getElementById('crawl-btn');
  const status = document.getElementById('crawl-status');
  btn.disabled = true;
  document.getElementById('crawl-icon').textContent = '⏳';
  status.textContent = '爬取中，請稍候…';

  try {
    const res = await fetch('/api/v1/crawl/trigger', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({})
    });
    const data = await res.json();
    status.textContent = `✅ 已啟動（關鍵字：${data.keywords.join('、')}）`;
    // 等 30 秒後自動刷新資料
    setTimeout(() => { reload(); }, 30000);
  } catch(e) {
    status.textContent = '❌ 爬取失敗，請確認伺服器狀態';
  } finally {
    btn.disabled = false;
    document.getElementById('crawl-icon').textContent = '🔄';
  }
}

async function reload() {
  const hours = document.getElementById('hours-select').value;
  document.getElementById('period-label').textContent =
    hours == 168 ? '1 週' : hours == 336 ? '2 週' : hours == 720 ? '30 天' : '60 天';
  document.getElementById('stats').innerHTML = '<div class="loading">載入中…</div>';
  document.getElementById('topics').innerHTML = '<div class="loading">載入中…</div>';

  const [kwStats, topics, wcData] = await Promise.all([
    fetch(`/api/v1/keywords/stats?hours=${hours}`).then(r => r.json()),
    fetch(`/api/v1/topics?hours=${hours}&top_articles=5`).then(r => r.json()),
    fetch(`/api/v1/wordcloud-data?hours=${hours}&limit=80`).then(r => r.json()),
  ]);

  // Stats bar
  const totalArticles = topics.reduce((s, t) => s + t.article_count, 0);
  const totalFreq = kwStats.reduce((s, k) => s + k.total_frequency, 0);
  document.getElementById('stats').innerHTML = `
    <div class="stat"><div class="num">${topics.length}</div><div class="lbl">追蹤關鍵字</div></div>
    <div class="stat"><div class="num">${totalArticles}</div><div class="lbl">相關文章數</div></div>
    <div class="stat"><div class="num">${totalFreq}</div><div class="lbl">關鍵字出現次數</div></div>
  `;

  // Word cloud（標題分詞）
  const canvas = document.getElementById('wc-canvas');
  canvas.width = canvas.offsetWidth;
  canvas.height = 400;
  const maxW = Math.max(...wcData.map(w => w.weight), 1);
  const words = wcData.map(w => [w.text, Math.round(14 + (w.weight / maxW) * 66)]);
  WordCloud(canvas, {
    list: words,
    gridSize: 8,
    weightFactor: 1.8,
    fontFamily: 'sans-serif',
    color: () => `hsl(${Math.floor(Math.random()*80+200)},65%,45%)`,
    backgroundColor: '#fff',
    rotateRatio: 0.25,
    minSize: 10,
    shuffle: true,
  });

  // Topics
  if (!topics.length) {
    document.getElementById('topics').innerHTML = '<p style="color:#9ca3af">尚無資料，請先觸發爬取。</p>';
    return;
  }
  document.getElementById('topics').innerHTML = topics.map(t => `
    <div class="topic-card">
      <h3>${t.keyword} <span class="badge">${t.article_count} 篇</span></h3>
      <div class="topic-meta">出現 ${t.total_frequency} 次</div>
      <ul class="article-list">
        ${t.top_articles.map(a => `
          <li>
            <span class="src-${a.source}">[${a.source.toUpperCase()}]</span>
            <a href="${a.url}" target="_blank">${a.title.length > 36 ? a.title.slice(0,36)+'…' : a.title}</a>
            <span class="score">⭐${a.hot_score.toFixed(1)}</span>
          </li>
        `).join('')}
      </ul>
    </div>
  `).join('');
}

loadKeywords();
reload();
</script>
</body>
</html>"""


@router.get("/dashboard", response_class=HTMLResponse, tags=["Dashboard"], include_in_schema=False)
def dashboard():
    """熱門議題 Dashboard（文字雲 + 議題列表）"""
    return _DASHBOARD_HTML

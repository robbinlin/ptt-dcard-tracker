import math
from datetime import datetime, timezone
from typing import List, Dict
from config import settings


def compute_keyword_frequency(text: str, keyword: str) -> int:
    """計算關鍵字在文本中出現的次數（不區分大小寫）"""
    if not text or not keyword:
        return 0
    return text.lower().count(keyword.lower())


def time_decay_factor(published_at: datetime) -> float:
    """根據文章發布時間計算時間衰減係數（指數衰減）"""
    if not published_at:
        return 0.1
    now = datetime.now(timezone.utc)
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    hours_ago = (now - published_at).total_seconds() / 3600
    # 指數衰減：半衰期為設定的小時數
    return math.exp(-hours_ago * math.log(2) / settings.time_half_life_hours)


def normalize(values: List[float]) -> List[float]:
    """將數值正規化到 [0, 1]"""
    if not values:
        return values
    max_v = max(values) or 1
    return [v / max_v for v in values]


def score_articles(articles: List[Dict], keywords: List[str]) -> List[Dict]:
    """
    計算每篇文章的熱門分數並加入 keyword_frequencies。
    score = w_comments * norm_comments
            + w_reactions * norm_reactions
            + w_keyword * norm_kw_freq
            * time_decay
    """
    if not articles:
        return []

    # 計算各文章的關鍵字總頻率
    for art in articles:
        full_text = f"{art.get('title', '')} {art.get('content', '')}"
        total_kw = sum(compute_keyword_frequency(full_text, kw) for kw in keywords)
        art["_kw_freq"] = total_kw
        art["keyword_frequencies"] = {
            kw: compute_keyword_frequency(full_text, kw) for kw in keywords
        }

    # 正規化三個維度
    comments = normalize([a.get("comment_count", 0) for a in articles])
    reactions = normalize([a.get("reaction_count", 0) + max(a.get("push_count", 0), 0) for a in articles])
    kw_freqs = normalize([a["_kw_freq"] for a in articles])

    w_c = settings.weight_comments
    w_r = settings.weight_reactions
    w_k = settings.weight_keyword_freq

    for i, art in enumerate(articles):
        decay = time_decay_factor(art.get("published_at"))
        raw_score = (w_c * comments[i] + w_r * reactions[i] + w_k * kw_freqs[i])
        art["hot_score"] = round(raw_score * decay * 100, 4)
        del art["_kw_freq"]

    return sorted(articles, key=lambda a: a["hot_score"], reverse=True)

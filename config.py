from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    # 預設關鍵字，可透過 .env 或 API 動態修改
    default_keywords: List[str] = ["運動", "訓練", "體育", "教練"]

    # PTT 看板清單
    ptt_boards: List[str] = ["Gossiping", "Stock", "Tech_Job", "HatePolitics", "NBA"]

    # Dcard 論壇清單
    dcard_forums: List[str] = ["trending", "tech", "job", "relationship", "taiwan"]

    # 熱門評分權重
    weight_comments: float = 0.5
    weight_reactions: float = 0.3
    weight_keyword_freq: float = 0.2

    # 時間衰減半衰期（小時）
    time_half_life_hours: float = 24.0

    # 排程間隔（分鐘）
    crawl_interval_minutes: int = 60

    # 每個來源最多爬取文章數
    max_articles_per_source: int = 100

    # API 設定
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()

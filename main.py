import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from models.database import init_db
from api.routes import router
from config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 啟動時初始化資料庫
    init_db()
    logger.info("資料庫初始化完成")
    yield
    # 關閉時停止排程
    from services.scheduler import stop_scheduler
    stop_scheduler()


app = FastAPI(
    title="PTT / Dcard 熱門議題追蹤器",
    description="爬取 PTT 與 Dcard 上關於指定關鍵字的熱門文章，提供 REST API 查詢。",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api/v1")


@app.get("/", tags=["健康檢查"])
def root():
    return {
        "service": "PTT / Dcard 熱門議題追蹤器",
        "version": "1.0.0",
        "docs": "/docs",
        "default_keywords": settings.default_keywords,
    }


if __name__ == "__main__":
    import os
    import uvicorn
    port = int(os.environ.get("PORT", settings.api_port))
    uvicorn.run("main:app", host=settings.api_host, port=port, reload=False)

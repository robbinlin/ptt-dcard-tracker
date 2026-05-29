from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Text, Index
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime, timezone

DATABASE_URL = "sqlite:///./tracker.db"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Article(Base):
    __tablename__ = "articles"

    id = Column(Integer, primary_key=True, index=True)
    source = Column(String(20), nullable=False)        # "ptt" or "dcard"
    board = Column(String(100), nullable=False)
    article_id = Column(String(200), nullable=False, unique=True)
    title = Column(String(500), nullable=False)
    content = Column(Text, default="")
    author = Column(String(100), default="")
    url = Column(String(500), nullable=False)
    comment_count = Column(Integer, default=0)
    reaction_count = Column(Integer, default=0)
    push_count = Column(Integer, default=0)            # PTT 推文數
    published_at = Column(DateTime(timezone=True), nullable=True)
    crawled_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    hot_score = Column(Float, default=0.0)

    __table_args__ = (
        Index("ix_source_board", "source", "board"),
        Index("ix_published_at", "published_at"),
        Index("ix_hot_score", "hot_score"),
    )


class KeywordMatch(Base):
    __tablename__ = "keyword_matches"

    id = Column(Integer, primary_key=True, index=True)
    article_id = Column(Integer, nullable=False)
    keyword = Column(String(100), nullable=False)
    frequency = Column(Integer, default=1)

    __table_args__ = (
        Index("ix_keyword", "keyword"),
        Index("ix_article_keyword", "article_id", "keyword", unique=True),
    )


class CrawlLog(Base):
    __tablename__ = "crawl_logs"

    id = Column(Integer, primary_key=True, index=True)
    source = Column(String(20), nullable=False)
    board = Column(String(100), nullable=False)
    started_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    finished_at = Column(DateTime(timezone=True), nullable=True)
    articles_found = Column(Integer, default=0)
    status = Column(String(20), default="running")   # running / success / error
    error_message = Column(Text, default="")


def init_db():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

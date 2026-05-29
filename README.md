# PTT / Dcard 熱門議題追蹤器

爬取 PTT 與 Dcard 上的文章，依關鍵字篩選並以熱門分數排序，提供 REST API 查詢。

## 功能

- 爬取 PTT 指定看板（Gossiping、Stock、Tech_Job 等）
- 爬取 Dcard 論壇與關鍵字搜尋（需提供 cookie，詳見下方說明）
- 依留言數、按讚數、關鍵字頻率與時間衰減計算熱門分數
- 定時自動爬取（預設每 60 分鐘）
- REST API 查詢文章、關鍵字統計、爬取日誌

## 快速開始

### 1. 安裝相依套件

```bash
pip install -r requirements.txt
```

### 2. 設定環境變數

複製 `.env.example` 為 `.env` 並依需求修改：

```bash
cp .env.example .env
```

### 3. 啟動伺服器

```bash
python main.py
```

伺服器預設監聽 `http://0.0.0.0:8000`，API 文件位於 `http://localhost:8000/docs`。

## API 端點

### 文章查詢

| 方法 | 路徑 | 說明 |
|------|------|------|
| `GET` | `/api/v1/articles` | 文章列表（支援 `keyword`、`source`、`board`、`hours` 篩選） |
| `GET` | `/api/v1/articles/trending` | 跨平台熱門排行 |
| `GET` | `/api/v1/articles/{id}` | 單篇文章詳情 |

### 爬取控制

| 方法 | 路徑 | 說明 |
|------|------|------|
| `POST` | `/api/v1/crawl/trigger` | 手動觸發爬取 |
| `GET` | `/api/v1/crawl/logs` | 查詢爬取記錄 |

### 排程管理

| 方法 | 路徑 | 說明 |
|------|------|------|
| `GET` | `/api/v1/scheduler/status` | 排程狀態 |
| `POST` | `/api/v1/scheduler/start` | 啟動定時爬取 |
| `POST` | `/api/v1/scheduler/stop` | 停止定時爬取 |

### 設定

| 方法 | 路徑 | 說明 |
|------|------|------|
| `GET/PUT` | `/api/v1/config/keywords` | 查詢／更新追蹤關鍵字 |
| `POST` | `/api/v1/config/dcard-cookies/reload` | 重新載入 Dcard cookie |

### 手動觸發爬取範例

```bash
curl -X POST http://localhost:8000/api/v1/crawl/trigger \
  -H "Content-Type: application/json" \
  -d '{"keywords": ["AI", "ChatGPT"], "sources": ["ptt"]}'
```

## 熱門分數計算

```
hot_score = (w_comments × norm_comments
           + w_reactions × norm_reactions
           + w_keyword   × norm_keyword_freq) × time_decay
```

| 參數 | 預設值 | 說明 |
|------|--------|------|
| `WEIGHT_COMMENTS` | 0.5 | 留言數權重 |
| `WEIGHT_REACTIONS` | 0.3 | 按讚數權重 |
| `WEIGHT_KEYWORD_FREQ` | 0.2 | 關鍵字頻率權重 |
| `TIME_HALF_LIFE_HOURS` | 24 | 時間衰減半衰期（小時） |

## Dcard Cookie 設定

Dcard 使用 Cloudflare Bot Management 防護，需手動提供瀏覽器 cookie 才能正常爬取。未提供時爬取結果為空（不影響 PTT 功能）。

**取得 Cookie 步驟：**

1. 在 Chrome 安裝 [EditThisCookie](https://chrome.google.com/webstore/detail/editthiscookie/fngmhnnpilhplaeedifhccceomclgfbg) 擴充功能
2. 開啟並登入 [dcard.tw](https://www.dcard.tw/)
3. 點擊 EditThisCookie 圖示 → 匯出（Export）
4. 將匯出的 JSON 儲存為專案根目錄的 `dcard_cookies.json`
5. 呼叫 `POST /api/v1/config/dcard-cookies/reload` 讓伺服器載入新 cookie

> Cookie 有效期約數天至數週，過期後需重新匯出。

## 環境變數

| 變數 | 預設值 | 說明 |
|------|--------|------|
| `DEFAULT_KEYWORDS` | `["AI","ChatGPT","台灣","選舉","房價"]` | 預設追蹤關鍵字 |
| `PTT_BOARDS` | `["Gossiping","Stock","Tech_Job","HatePolitics","NBA"]` | PTT 看板清單 |
| `DCARD_FORUMS` | `["trending","tech","job","relationship","taiwan"]` | Dcard 論壇清單 |
| `CRAWL_INTERVAL_MINUTES` | `60` | 自動爬取間隔（分鐘） |
| `MAX_ARTICLES_PER_SOURCE` | `100` | 每來源最多爬取篇數 |
| `API_HOST` | `0.0.0.0` | API 監聽位址 |
| `API_PORT` | `8000` | API 監聽埠號 |

## 技術架構

- **框架**：FastAPI + Uvicorn
- **資料庫**：SQLite（SQLAlchemy ORM）
- **排程**：APScheduler
- **PTT 爬蟲**：requests + BeautifulSoup4
- **Dcard 爬蟲**：requests + cookie 注入

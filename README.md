# MACD 監測系統

依《MACD 監測系統 系統設計規格書 v1.0》開發：台灣、美國、日本三市場每日盤後計算週線 MACD，
柱體動能「首次減弱」時立即以 LINE 通知，再由量價、均線與交叉決定後續等級。系統只提供資訊，不自動下單。

介面文字一律繁體中文；英文只留在 API、資料庫與技術追蹤碼。

## 專案結構

| 路徑 | 內容 |
|---|---|
| `backend/app/engine/` | 純計算引擎：週線聚合、復權、MACD／均線、量比、訊號分類、狀態流轉、持股成本 |
| `backend/app/workers/` | 盤後管線、排程、每週摘要 |
| `backend/app/notify/` | LINE 訊息版型（Flex＋純文字）、待送佇列投遞、加密 |
| `backend/app/api/` | `/api/v1` REST API（FastAPI） |
| `backend/app/providers/` | 行情來源轉接層；目前只有虛構代號的示意資料 |
| `backend/app/i18n/zh_tw.py` | 介面文案對照表（規格 9.7） |
| `frontend/` | React + Vite + TypeScript 網站 |
| `docs/MVP計畫.md` | 範圍、架構與分批交付計畫 |

## 本地開發

```bash
cd backend
pip install -r requirements.txt
ADMIN_EMAIL=you@example.com python -m app.seed          # 建立市場、示意股票與管理員
uvicorn app.main:app --reload --port 8000              # API，文件在 /api/docs
python -m app.workers.runner once --market TW          # 手動跑一次台股盤後
python -m pytest                                        # 測試
```

開發環境的登入連結會直接顯示在登入頁（不寄信）；LINE 未設定 `LINE_CHANNEL_ACCESS_TOKEN` 時一律乾跑，只寫日誌。

## 環境變數

`DATABASE_URL`、`SECRET_KEY`、`APP_BASE_URL`、`APP_ENV`（dev／prod）、`LINE_CHANNEL_ACCESS_TOKEN`、
`LINE_CHANNEL_SECRET`、`LINE_BOT_BASIC_ID`、`LINE_LIVE`（非正式環境要真的推播時設 1）、`DATA_PROVIDER`。
密鑰不得提交到版本庫。

前端：

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173，/api 代理到 :8000
```

## 部署（Vercel）

- `vercel.json`：前端建置到 `frontend/dist`，`/api/*` 由 `api/index.py`（FastAPI）處理，其餘路徑回傳 SPA。
- 排程（Vercel Cron，UTC）：台股 06:00、日股 07:00、美股 22:00（週一至五），每週摘要週六 04:00（台北 12:00）。
- 必要環境變數：`DATABASE_URL`（或 Vercel／Neon 注入的 `POSTGRES_URL`）、`SECRET_KEY`、`CRON_SECRET`。`APP_ENV` 在 Vercel 上預設為 prod，`APP_BASE_URL` 預設為正式網域。
- 選用（之後設定）：`RESEND_API_KEY`／`EMAIL_FROM`（寄登入信）、`LINE_CHANNEL_ACCESS_TOKEN`／`LINE_CHANNEL_SECRET`／`LINE_BOT_BASIC_ID`（LINE）。
- 管理員首次登入：開啟 `/setup`，輸入 Email 與 `CRON_SECRET`；系統尚無管理員時會以該 Email 建立。尚未設定寄信時，邀請連結直接顯示在管理中心。
- Hobby 方案的 Cron 每天各跑一次，資料未齊時要等隔天補抓；需要每 15 分鐘重試請升級 Pro 後調整排程。

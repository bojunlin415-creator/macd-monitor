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

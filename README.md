# 每日看板

在自己電腦上執行的小網頁看板（台灣時間、繁體中文），一頁看完：

- **股票**：台股（證交所即時／收盤資料，上市上櫃都可）、美股（Yahoo Finance，Alpha Vantage 備援），紅漲綠跌
- **NBA**：昨天／今天／明天（台灣日期）的比分與賽程，進行中比賽排最前面（資料來源：ESPN）
- **3C 比價**：每天自動到 PChome 24h、momo購物網、Yahoo購物中心 搜尋，顯示最低價與各家價格

支援深色／淺色模式與手機寬度，網頁上可直接新增、刪除自選股與商品。

## 使用方式（Windows）

1. 安裝 [Python 3](https://www.python.org/downloads/)
2. （可選）把 `.env.example` 複製成 `.env`，填入 Alpha Vantage 免費金鑰
3. 雙擊 `啟動看板.bat`，第一次會自動安裝套件，接著瀏覽器會打開 http://localhost:8000
4. 要關閉看板，關掉黑色視窗即可

詳細說明請看 [使用說明.txt](使用說明.txt)。

## 檔案

| 檔案 | 用途 |
|---|---|
| `server.py` | 後端：定時抓資料、提供網頁與 `/api/data` |
| `static/` | 前端網頁（HTML／CSS／JS） |
| `config.json` | 自選股、3C 商品、更新頻率（網頁上的設定也會存在這裡） |
| `requirements.txt` | 需要的 Python 套件 |
| `啟動看板.bat` | 一鍵啟動 |

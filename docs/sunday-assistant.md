# 主日資料助理

此助理連接現有的兩個 WhatsApp 群，整理資料到現有主日及同工表單。

| 資料來源 | 工作 |
| --- | --- |
| 達拉斯聖道教會 | 同工不能服事的日期；保存成功後在原群回覆「收到，已更新」並列明姓名和日期 |
| 主日讲道联络群 | 當周及未來主日的講員、題目、詩歌、回應詩歌、讀經經文 |

周五 **17:00 America/Chicago**，檢查接下來主日的五個欄位。全部齊備就不提醒；有缺項則在主日內容群提醒一次。未同步、待辦、識別失敗、未核對資料或同工安排衝突會暫緩提醒，原因顯示在管理頁面。設定可調整時間。

## 連接方式及目前邊界

`services/whatsapp-bridge` 使用 whatsapp-web.js 和本機已安裝的 Chrome。
它是非官方 WhatsApp Web 客戶端，存在平台變更導致斷線及帳號受限的風險；參見
[專案說明](https://wwebjs.dev/guide/)。接入既有群需先用選定帳號的手機掃碼，再在管理頁選定兩個群 ID。群名稱只是預設提示，不會自動按相似群名發信。

首次同步最近 30 天、WhatsApp Web 可提供的消息；這些歷史消息先進待核對，不自動套用，也不向群內補發舊請假回覆。之後保存每群游標，定期補同步並接收新消息。讀取超過上限會顯示錯誤，不能把不完整同步當成已完成。Chrome 必須由一般使用者運行；不關閉 Chromium 沙箱。

首版處理文字。語音、圖片、附件及不清楚的內容進入待核對，不會假裝已讀懂。群撤回消息需要人工核對已收集資料，請用明確的新消息或既有表單更正。

## 設定和啟動

先依 [operations-runbook.md](operations-runbook.md) 準備環境。開發使用自己的 worktree 及 `scripts/dev.sh`，production 部署仍只有 `scripts/deploy.sh`。以下是程式啟動方式，不會部署、切換服務或替 production 建立 LaunchAgent。

1. 在 worktree 的 `services/whatsapp-bridge` 安裝獨立依賴：

   ```sh
   PUPPETEER_SKIP_DOWNLOAD=1 npm ci --omit=optional
   ```

2. 建立 Git 以外、權限 `600` 的設定檔。backend、web 和助理需使用相同的 `SUNDAY_ASSISTANT_SECRET`。密鑰可以用 `python -c 'import secrets; print(secrets.token_urlsafe(32))'` 在操作者終端產生，勿提交或寫入日誌。

   ```dotenv
   DATA_BASE_DIR=/absolute/path/to/data
   FULL_ARTICLE_SERVICE_URL=http://127.0.0.1:9548
   SUNDAY_ASSISTANT_SECRET=<random-secret-at-least-24-characters>
   SUNDAY_WHATSAPP_STATE_DIR=/absolute/private/path/to/whatsapp-state
   SUNDAY_WHATSAPP_PORT=8766
   SUNDAY_WHATSAPP_BRIDGE_URL=http://127.0.0.1:8766
   SUNDAY_WHATSAPP_CHROME=/Applications/Google Chrome.app/Contents/MacOS/Google Chrome
   ```

   backend 要有既有的 `OPENAI_API_KEY`，模型沿用網站的 `OPENAI_GENERATION_MODEL`；可用 `SUNDAY_ASSISTANT_MODEL` 專門指定。此助理設定沒有預設 production 密鑰。

3. 使用與 `.python-version` 相符的 `backend/.venv` 啟動：

   ```sh
   backend/.venv/bin/python scripts/run-sunday-assistant.py --config /absolute/path/to/assistant.env
   ```

   Supervisor 同時管理 Node 橋接器和 Python worker，持有獨占工作階段鎖。任一退出就停止另一個，外部服務管理器可重新啟動整對程序。若 production 日後設定 LaunchAgent，將其 WorkingDirectory 和 Python 路徑固定到實際 release，將本設定與 WhatsApp 狀態放在 release 之外；未完成此配置時不能宣稱已常駐啟用。

4. 開啟 `/admin/sunday-assistant`。用手機 WhatsApp → 設定 → 連結裝置掃碼，讀取群名單、核對帳號與兩個群，再保存群設定及啟用收集。

5. 第一輪保持「自動套用」關閉，核對更新預覽。將消息發送人 ID 對應到精確同工名字；夫妻的 ID 可對應兩人。「可信發送人」明確列出後，才對其啟用無衝突自動更新。其他消息仍進待核對。開啟周五提醒前先確認群選擇和收集結果。

新增群接口只接受 local bearer 密鑰；瀏覽器經 NextAuth 管理員/editor 權限和同源檢查代理，不暴露密鑰，也不代理 bridge ingestion 接口。服務綁定 loopback。

## 恢復和核對

- 原消息先進 SQLite，再做模型識別；資料位於 `$DATA_BASE_DIR/sunday-assistant/assistant.sqlite3`。處理失敗保留錯誤和原消息，從後台重試。
- 處理程序採 OS 鎖，每次重啟恢復 `processing` 和 `applying`。網站的主日與同工寫入共用鎖，更新前重讀資料，只修改抽取的欄位；與預覽不同的人工修改會擋下。
- 更早的消息不能覆蓋更新消息；同時不同值需核對。抽取結果的 evidence 要逐字對回原消息，姓名和詩歌要對應既有名單。
- 未確認的欄位或身份進待核對。可從既有表單補齊後略過該待辦，或在群中補充後重新整理。含疑問的提案不能直接批准。
- 請假回覆在成功寫入後加入持久化 outbox；恢復未完更新不會先回報成功。確認後的請假也會發回覆。
- WhatsApp 工作階段、入站補送及已送記錄位於 `SUNDAY_WHATSAPP_STATE_DIR`，不進 Git。狀態目錄應限該使用者存取。
- 發送前保存 outbox，發送後保存遠端消息 ID。若連線在中間失去、無法確定成功，顯示「發送結果待確認」，只查 bridge 既有結果，不盲目重送。同一主日的提醒以日期去重。
- 備份 SQLite 使用其 backup API（勿只複製啟用 WAL 時的單一檔案）；WhatsApp 狀態備份應在 supervisor 停止後進行。

## 驗證

```sh
backend/.venv/bin/python -m pytest backend/tests/test_sunday_assistant.py backend/tests/test_sunday_worker.py backend/tests/test_sunday_scripture_readers.py
cd services/whatsapp-bridge && npm test && npm run check
cd web && ./node_modules/.bin/tsc --noEmit
```

真實帳號掃碼、讀取兩個指定群及真實回覆，是需在接入時另外核對的現場驗收；模擬測試不發真實群消息。

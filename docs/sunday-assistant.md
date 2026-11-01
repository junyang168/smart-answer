# 主日資料助理

此助理連接現有的兩個 WhatsApp 群，整理資料到現有主日及同工表單。

| 資料來源 | 工作 |
| --- | --- |
| 達拉斯聖道教會 | 同工不能服事的日期；保存成功後在原群回覆「收到，已更新」並列明姓名和日期 |
| 主日讲道联络群 | 當周及未來主日的講員、題目、詩歌、回應詩歌、讀經經文，以及當周奉獻總額 |

周五 **17:00 America/Chicago**，檢查接下來主日的五個欄位。全部齊備就不提醒；有缺項則在主日內容群提醒一次。未同步、待辦、識別失敗、未核對資料或同工安排衝突會暫緩提醒，原因顯示在管理頁面。設定可調整時間。

主日資料可由群內不同同工分次提供，每次只更新明確提到的欄位，其餘已收集內容保留。奉獻寫入既有 `donationAmount`，只記錄明確主日的奉獻總額，不收集個別捐獻者資料，也不自行換匯或推算。奉獻通常在主日後統計，不列入周五對即將到來主日的準備缺項。

## 連接方式及目前邊界

`services/whatsapp-bridge` 使用 whatsapp-web.js 和本機已安裝的 Chrome。
它是非官方 WhatsApp Web 客戶端，存在平台變更導致斷線及帳號受限的風險；參見
[專案說明](https://wwebjs.dev/guide/)。接入既有群需先用選定帳號的手機掃碼，再在管理頁選定兩個群 ID。群名稱只是預設提示，不會自動按相似群名發信。

首次同步最近 30 天、WhatsApp Web 可提供的消息；這些歷史消息先進待核對，不自動套用，也不向群內補發舊請假回覆。之後保存每群游標，定期補同步並接收新消息。讀取超過上限會顯示錯誤，不能把不完整同步當成已完成。Chrome 必須由一般使用者運行；不關閉 Chromium 沙箱。

首版處理文字。語音、圖片、附件及不清楚的內容會請群內同工以文字補充，不會假裝已讀懂。群撤回消息需要核對已收集資料，請用明確的新消息或既有表單更正。

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

5. 第一輪保持「自動登記」關閉，核對更新預覽。將消息發送人 ID 對應到精確同工名字，以理解「我」；夫妻的 ID 可對應兩人，以理解「我們夫妻」。核對資料來源群後，開啟自動登記就會接收所選群不同成員的明確資料，並在需要時追問；不需逐位加入發送人白名單。舊設定中的 `trusted_senders` 保留相容，但不再是自動登記的門檻。開啟周五提醒前先確認群選擇和收集結果。

新增群接口只接受 local bearer 密鑰；瀏覽器經 NextAuth 管理員/editor 權限和同源檢查代理，不暴露密鑰，也不代理 bridge ingestion 接口。服務綁定 loopback。

## 恢復和核對

- 原消息先進 SQLite，再做模型識別；資料位於 `$DATA_BASE_DIR/sunday-assistant/assistant.sqlite3`。處理失敗保留錯誤和原消息，從後台重試。
- 處理程序採 OS 鎖，每次重啟恢復 `processing` 和 `applying`。網站的主日與同工寫入共用鎖，更新前重讀資料，只修改抽取的欄位；與預覽不同的人工修改會擋下。
- 更早的消息不能覆蓋更新消息；同時不同值需核對。抽取結果的 evidence 要逐字對回原消息，姓名和詩歌要對應既有名單。
- 新增外出區間時會合併日期，保留原有人工作業的日期；刪除人工登記日期或改寫既有崇拜內容仍需核對。自己的消息以登入帳號對應姓名，其他發送人的裝置尾碼不影響姓名對應。
- 同一發送人更正先前已成功登記的外出，可引用同群前文沿用登記對象，不必重複兩人的姓名。模型指出前文消息 ID，程式驗證該消息屬於同一發送人且已套用該同工的外出；延長日期回覆完整的新區間。其他發送人、其他群或尚未套用的前文不能作為此依據。
- 安裝時會對固定版本的 `whatsapp-web.js` 套用 MsgKey 相容修正（上游 PR #201871），支援 WhatsApp Web 將 `_serialized` 改名為 `$1` 的版本。安裝腳本核對原檔 SHA；升級套件時若原檔不同會停止，需先檢查相容性。
- 自動登記開啟後，未確認的欄位或身份會在原群提問，狀態為「已在群內提問，等待回答」。提問以 `clarify:<對話ID>:<輪次提案ID>` 去重，並保存原消息、歷次回答及當前問題；首次歷史同步和預覽模式不發問。
- 回答後模型整合該件待辦的原文及回答，日期或姓名仍不清楚就只追問剩餘問題；齊全才寫表單、回覆已更新並結束舊待辦。只允許從該件待辦原文或回答取 evidence。本人可直接回答，其他群內同工需明確引用助理的提問才能接續；其他群或未指向提問的插話不會混入。
- 未解決的對話會跨程序重啟保存；同一個剩餘問題不因相同回答重發。取消尚未完成的登記會回覆取消，不更動既有表單資料。送達結果不明時沿用 outbox 的不重送規則。也可從後台略過待辦；含疑問的提案不能直接批准。
- 請假回覆在成功寫入後加入持久化 outbox；恢復未完更新不會先回報成功。確認後的請假也會發回覆。
- WhatsApp 工作階段、入站補送及已送記錄位於 `SUNDAY_WHATSAPP_STATE_DIR`，不進 Git。狀態目錄應限該使用者存取。
- 發送前保存 outbox，發送後保存遠端消息 ID。若連線在中間失去、無法確定成功，顯示「發送結果待確認」，只查 bridge 既有結果，不盲目重送。同一主日的提醒以日期去重。
- 備份 SQLite 使用其 backup API（勿只複製啟用 WAL 時的單一檔案）；WhatsApp 狀態備份應在 supervisor 停止後進行。

## 驗證

```sh
backend/.venv/bin/python -m pytest backend/tests/test_sunday_assistant.py backend/tests/test_sunday_assistant_dialogue.py backend/tests/test_sunday_worker.py backend/tests/test_sunday_scripture_readers.py
cd services/whatsapp-bridge && npm test && npm run check
cd web && ./node_modules/.bin/tsc --noEmit
```

真實帳號掃碼、讀取兩個指定群及真實回覆，是需在接入時另外核對的現場驗收；模擬測試不發真實群消息。

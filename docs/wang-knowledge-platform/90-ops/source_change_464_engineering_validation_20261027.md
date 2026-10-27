# #464 來源變更自動接續工程驗證

> **讀者**：Developer。
> **類型**：記錄。
> **狀態**：當前。
> **與代碼對齊**：2026-10-27，本次 #464 worktree 的工程驗證；真實來源操作結果另記。
> **權威範圍**：本次隔離 SQL、合成模型、型別檢查及真實來源只讀預檢的實際結果，不批准真實 Claim 或 CVP 內容。

## 隔離驗證

使用 `backend/.venv` 的 Python 3.13、socket-only disposable PostgreSQL。
模型全部為合成回答，正常 SQL 提交、原始回應綁定及回讀使用真實程式。

- Claim management、來源影響、直接鄰居、ID intake、資格／退休、新來源接續、CVP management、
  論證回讀／觀察支持、逐字稿 CAS 與小標題相關測試：**528 passed, 35 skipped，102.69 秒**。
  跳過項為既有條件式測試。
- 啟動證明路徑修正後，runtime guard 與既有 transcript-repair 保護測試：**74 passed，1.03 秒**。
- 前端 `./node_modules/.bin/tsc --noEmit`：通過。工作樹依鎖檔執行自身 `npm ci --ignore-scripts`，
  修正複製的安裝缺模組問題；沒有修改 package 或 lockfile。
- `git diff --check`：通過。
- `python3 scripts/check-docs.py docs`：100 份文檔中，只有已知另一專案的失效錨點
  `docs/notes-to-sermon-agent/functional_spec.md:120`。

新案例涵蓋：兩段按來源順序呼叫正常 Update；來源審閱 finding 正式交回 GPT，再由 Opus 審閱；
來源交易、引用版本及人工確認原子性；晚期失敗回滾；before 原件與完整恢復投影的區別；
同 action/root 零重發接續；來源提交後外層回執遺失；第一段暫停不準備第二段；
兩批之間檔案改變時禁止下一次派送；真正 Opus + program-readback 的舊資格接續；
只讀鄰居的審閱不補成其缺失的內容資格；編輯器 CAS 失敗不歸檔、成功保存精確舊位元組。

下游測試包含新建 CVP 和維護既有 CVP：實際提交、建立新修訂並保留舊修訂、
入既有論證隊列、用合成提供者回應走正常 worker、提交觀察支持、回讀，以及零模型重發恢復。

## 《中保》只讀預檢

來源 `SRC-S_210613_2_1_7-8c320ede3e26`，《S 210613 提前2 1-7 中保》。
沒有找到登記的舊原始位元組；以完整 patched 正文和現有標題重建投影，
正文、純文字、標題結構及標題位置四種雜湊均與來源記錄吻合。
恢復檔實際 SHA 與舊原件 SHA 分開，未偽造原件。

- 改動段落：**S0049、S0050**，座標不變。
- 當前來源片段 **457** 個。
- **447** 個引文前後逐字可核對，可作機械版本同步。
- **10** 個引文不再吻合，留給正常 Claim Update；不猜測替換文字。
- 本次預檢：**0 模型呼叫、0 資料庫寫入**。
- 報告：`/var/folders/s0/899st79150z63778jn0381pr0000gn/T/wkp464-source-preflight-v780dzfi/preflight.json`。

## 啟動證明

已有 owner renewal 的 previous-runtime 綁定仍使用 staging 原路徑；staging 搬遷後，
該路徑經目錄連結指向同一實體檔案。原程式比較路徑字串而拒絕；現核對同一實體檔案與 SHA，
不同檔案副本、SHA、登入、模型、執行檔或權限漂移仍拒絕。既有證明成功復用：
`reused=true`、`new_model_calls=0`。沒有重新診斷、登入或變動原預算。

真實模型與內容寫入尚未由本工程記錄宣稱完成。

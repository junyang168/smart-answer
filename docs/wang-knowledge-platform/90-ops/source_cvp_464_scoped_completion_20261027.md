# S210613 來源更新後的 CVP 與指定論證接續

> **讀者**：Developer。
> **類型**：記錄。
> **狀態**：當前。
> **與代碼對齊**：2026-10-27，`4d7d6556`；實際 SQL 結果及原任務保存材料已核對。
> **權威範圍**：本次來源任務的 CVP 提交、負責人指定的三個新 CVP 論證完成及既有論證保留；不宣稱被排除的工作已完成，也不宣稱原文編輯入口已自動整合。

## 本次範圍與結果

沿用 `source-claim-updates-464/s210613-20261027-v1` 原 root、來源
`SRC-S_210613_2_1_7-8c320ede3e26`、原 Claim actions 及保存答覆。
九條 Claim 的當前資格全部 ready；沒有重新執行其內容更新。
原 Claim 階段結果的 SHA 保持為
`268a10728b43a95684aab6a35f848e5c75f389f9d680db50af0e7d7d7c6773c4`。
來源 SQL revision 仍為 2，正文及實體來源沒有再改動。

普通 GPT／Opus CVP 管理已完成完整審閱、SQL 提交與讀回：更新一個既有 CVP，新增三個。
之後負責人明確指定：「这个 CVP 的论证层不要碰，其他的可以继续」。
被排除者為既有「贖價／見證」CVP `CVP3-v-58b636ece7b6b5635e1bdaa4`。
原會消費全部四個 jobs 的來源 controller 已停止，未用它續跑後續清單。

以下三個新 CVP 的論證依序通過既有 GPT／Opus worker、交易及讀回：

| CVP | 內容 | Route／實例／觀察 | 實際 ChangeSet |
| --- | --- | --- | --- |
| `CVP2-v-92f4a561478aec2ff96e325a` | 舊約獻祭與赦罪依賴基督永遠有效的獻祭 | 2／2／4 | `KCS-f77300f08cae2ae70beb` |
| `CVP2-v-5ceadf614ede526711dbee2f` | 教授處理經文疑難的經驗與其聖經無誤判斷 | 1／1／1 | `KCS-5f3fa96201d660255b8d` |
| `CVP2-v-ce683eb1f3145fe51001a0db` | 基督唯一有效的中保職分與摩西中保稱號不矛盾 | 1／1／2 | `KCS-b93a6ab991aaf7a408c7` |

本次合計四條 Route、四條逐篇實例及七條觀察支持項。
最後另以保存的真實審閱、ChangeSet、當前依賴及實際後像核對三個 completed jobs，
沒有追加模型呼叫或知識寫入。

## 被排除的論證與並行程序

執行前保存被排除 CVP 的十九筆論證層記錄；最後逐項比較 payload、revision、SHA，全部相同。
本次限定接續沒有呼叫它的 job，也沒有在本次 `arguments/` 建立該 job 的輸出。

過程中，另一個 #436 程序從同一佇列獨立領取了被排除 job，輸出位於
`cvp-management-436/verify-two-arguments-483/current-source-followup`。
該程序及其本地模型子程序已暫停，核對時仍未改寫上述十九筆記錄。
其共享 queue state 因而是 running，不能寫成 queued、complete 或未曾呼叫。
已向負責人詢問是否通知 #436；未取得回覆前沒有向另一個 chat 發訊息。

限定 controller 最後仍以「被排除 job 必須 queued」作全佇列斷言，因此報錯；
這不是三個允許 jobs 的失敗。保留原 controller log，不將其重標為成功。
獨立的只讀完成核對，以三個允許 jobs 的真實批准／提交／讀回及十九筆排除資料未變為依據，
將**限定範圍**的完成事實保存於 `downstream/owner-argument-scope/result.json`。
原完整四項流程的歷史結果沒有冒充全部完成。

## 工程修正與驗證

實測暴露並修正了程式校驗回饋改變材料 SHA 卻未重綁前一候選、舊凍結 frame 的恢復、
新增連接錯誤未指出具體引用，以及已解決的格式錯誤污染後續語義修訂。
保存原錯誤、答覆、異議及輸入；只對尚未派送的新步驟補具體診斷或移除已解決的格式提示。
沒有刪除未解決的 Opus 異議，沒有將程式修復當作內容批准。

最終相關隔離 PostgreSQL 測試 **139 passed**，包括格式修正後再次收到 Opus 異議、
舊 frame、錯誤引用、來源漂移、當前資格、完成任務恢復及原呼叫保留。
CVP 原 request 的二十次 cap 不變；在全部返回後使用既有有界控制另記兩次接續，
累計二十二次，原呼叫與正常修訂上限均保留。

本次沒有 Gemini 終審、整庫備份、文章生成或部署；獨立品質審計為 `not_performed`。
流程邊界仍以[來源段落變更後更新 Claim 與 CVP](../20-knowledge/source_claim_updates_v1.md)為準。

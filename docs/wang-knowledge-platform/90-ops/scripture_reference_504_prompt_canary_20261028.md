# #504 經文引用 prompt 隔離試驗（2026-10-28）

> **讀者**：Developer。
> **類型**：記錄。
> **狀態**：當前。
> **與代碼對齊**：2026-10-28，基於 `6a8287a9` 的三份候選 prompt；尚未合併。
> **權威範圍**：本次實際模型試驗的觀察結果，不表示 #504 完成或既有資料已修復。

## 範圍

在抽取的原有逐句自檢中，增加 Claim／EvidenceStep 各自經文引用完整性核對；在審閱的原有完整性項目加入相同核對。不新增欄位或審閱輪次。不憑記憶補出處，不把整段讀經分配給每條記錄。

真實來源為《2018 NYSC 專題：馬太福音釋經（七）1》。使用目前章節計畫的第4節 S0012–S0015（119句），包含讀太20:1–16、解釋20:16、以19:30限定解釋的完整上下文。歷史資料 ID 的 P07 不是目前的第7節；沒有重編或匯入 ID。

實際呼叫 GPT `gpt-6.1-sol` high 四次、Opus `claude-opus-5-5` high 一次，每例一次，沒有內容重試。GPT 使用正式抽取 schema／章節輸入組裝，Opus 使用正式 corpus review 輸入組裝及 schema。測試原始回答沒有提示預期經文答案。

## 結果

| 樣本 | 觀察 |
| --- | --- |
| 真實段落重抽 | 產生33條候選。與舊 CL001 語義對應的 CL017、CL018 均列出太20:16及太19:30；直接產生這兩條主張的 E014、E023、E029 也均列出兩處。其餘引用隨記錄而異，並未全部填相同範圍。 |
| 合成：宣告讀20:1–16，只解釋末句 | 1條 Claim、2條 EvidenceStep 均只列20:16。 |
| 合成：只引用19:30的「有許多」 | 3條 Claim、4條 EvidenceStep 均只列19:30，未補文字相近的其他經文。 |
| 合成：明說無法確定半句的出處 | 3條 Claim、3條 EvidenceStep 的引用均為空；實質判斷仍有抽出，沒有以不抽取來規避測試。 |
| 審閱原有 `DK-16807c15404bfc4c24b2-P07-CL001` | Opus 指出 `missing_scripture_evidence`：20:16是被解釋的經文，19:30是支持解釋的平行經文。另提出立場、限定及錨點問題，整條決定為 `human_review_required`；並非整條審閱通過。 |

四個抽取回答均通過既有結構／圖關係、錨點和逐句審計校驗，審閱回答通過既有 review validator。三組現有測試共100項通過（`test_detailed_knowledge_extraction.py`、`test_codex_subscription_extraction.py`、`test_corpus_ai_review.py`）。

## 限制與保存

這是候選 prompt 在四個抽取樣本及一個審閱樣本的試驗，沒有同模型新舊 prompt 對照，不證明全庫召回率或零 regression。母本抽取 prompt 同步了同一句規則，但本次未用母本真實樣本試跑。corpus review 的這條輸入路徑沒有 EvidenceStep，故 Opus 結果只證明能發現 Claim 漏標；不能宣稱它審過未傳入的證據記錄。

模型提出的額外問題僅保存為觀察，沒有自動轉人工任務、修改 Claim 或刷新 CVP。資料庫只讀；原始來源檔案 SHA 前後相同。#504 的既有資料更新及其他修復範圍仍未完成。

本機完整請求、來源／prompt SHA、傳輸記錄、原始回答、機械校驗與評估保存於 `/tmp/wkp-504-prompt-canary-v1`；臨時執行腳本為 `/tmp/run-504-prompt-canary.py`。以下雜湊可辨識本次回答，臨時路徑不是長期保存承諾。

| 回答 | SHA-256 |
| --- | --- |
| `parallel_same_words_not_added` | `594ef750c336f34fae026c3cf26ec79441e239b5070cebc008dd830bb159320a` |
| `review_original_miss` | `677b2734d4fd1010edafa219d0cd38e43df5efb1ee38fd596cf0c477b6213210` |
| `actual_read_then_discuss` | `f2a0d090b5dd2f6e0dea4f064d7c61b333529da49d1dca3bd5f151dfad906adc` |
| `range_does_not_spread` | `eaa4e48de646bb964a48c5d7a9b0c4af44f152f98ebd975dd802f628628fb895` |
| `unknown_reference_not_guessed` | `07dab5c32bb1466f970fffdbaa280a37b113639a30c56cad9ebd646371857ccd` |

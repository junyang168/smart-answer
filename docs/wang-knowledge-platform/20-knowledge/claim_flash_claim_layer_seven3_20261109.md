# Flash Claim 層：第三講剩餘六組隔離實測

> **讀者**：Developer、評估 refresh 效果及成本的負責人。
> **類型**：記錄。
> **狀態**：當次隔離實測結果；不是正式庫完成資格。
> **與代碼對齊**：2026-11-09（核對到 `7f975760`）。
> **權威範圍**：2018 NYSC《馬太福音釋經（七）》第三講剩餘六組 Claim 任務、復用、恢復與消耗。

## 範圍與結果

先分組，再整篇完成原文及 Evidence／Observation，最後處理 Claim。
本次依原分組處理 FG002–FG007 的 37 條既有 Claim；先前 FG001 三條的檢查結果沿用，
不重審下層，不切模型。GPT 使用 Sol medium 訂閱，Jev 沿既有設定；沒有 Opus 調用。

六組分別完成表述／整體支持／經文、論證完整性及材料配對、Claim 關係、漏抽判斷。
每個任務只有自身範圍；同一份已驗證材料供各使用者引用。

- 全部 40 條既有 Claim 的檢查已記錄，原有 statement 均未改寫。
- 原有 59 條隱式支持連接經 Jev 保留，未交 GPT 重寫。
- 新補 49 條材料論證關係、33 條 Claim 關係；這是顯式連接補齊，不等於 82 條 Claim 錯誤。
- 新增兩條隔離候選，均完成自身表述／支持及必要配對檢查：
  - `DK-b4fc2c17c447-P02-CL016`：錯傳福音可能使人誤以為已信，卻因與耶穌沒有正確關係而被拒。
  - `DK-b4fc2c17c447-P04-CL013`：耶穌在被差遣以前已是神的兒子，是因為祂是兒子才被差遣。
- 第二條同時補入 Q003 的直接答案連接。沒有重審 Question 內容或擴大成問題管理。
- 共七筆 Claim 記錄有變化，包含兩筆新建；既有變更是支持連接及來源錨點。
  八筆 Evidence 僅同步 `produced_claim_ids`，一筆 Question 僅同步答案連接。
- 原文、SourceFragments、Evidence／Observation 表述保持不變。最後一組重入零模型、零內容寫入。
- 原 `DK-b4fc2c17c447-P02-CL015` 顆粒度候選維持 owner hold，沒有建立或換 ID 繞過裁定。
  保存進度只剩這個明示 hold，不把它報成批准。

## 實際恢復與修復

Claim 任務須取得既有人工來源解釋。程式按同組原文段落投影，
表述、配對、論證和漏抽都接續同一事實，寫入亦核對它仍有效。
未用人工確認的舊下層判定，只復用其完全未變的 statement／逐字片段證明；
新增解釋明記為 Claim 層上下文，不冒稱舊審閱已看過，也不能丟掉原已綁定的確認。

FG004 第一包論證答覆包含同組 Claim–Claim 候選，舊程式直接拒收。
修復將合法候選交下一個獨立關係任務：本項只查材料論證，後項照常配對，
未提供端點、自連接與越界來源仍拒收。原答覆完整復用，沒有重新調用該包。

隔離 fixture 起初漏帶先前 held CL015 的身份預留，導致第二組另一候選暫獲同號。
保留原結果及帳目，復原原有交易 checkpoint，帶回原預留，使用既有 allocator
將完全相同的已審候選分配為 CL016；關係及 owner 欄位由正常提交重建。
後續隔離 checkpoint 帶回三筆實際身份預留，沒有手改模型判斷或重叫 GPT。
這是測試 fixture 的身份交接問題，不是正式庫 ID 被覆寫。

兩項恢復均零新增模型調用。相關回歸共 455 項通過，包含人工來源投影與提交防漂移、
合法跨任務交接及獨立關係審查、既有入口、隔離 PostgreSQL、回讀與重入。
正式庫寫入為零，所有臨時 PostgreSQL 均已停止並刪除資料目錄。

## 時間與 token

| 項目 | GPT | Jev |
| --- | ---: | ---: |
| 實際調用 | 67 | 198 |
| Input tokens | 1,387,520 | 332,800 |
| Output tokens | 34,607 | 41,338 |
| 合計 tokens | 1,422,127 | 374,138 |
| 調用時間合計 | 1,233.130 秒 | 31.399 秒 |

合計 1,796,265 tokens；GPT input 內已有 199,296 cached tokens，不能再相加。
GPT reasoning 8,443 是 output 子集，也不能重複計算。
GPT 時間從各實際 dispatch 記錄至 transport exit，包含排隊；Jev 時間從開始記錄至答覆保存。
首個 GPT 開始至末個完成約 41.38 分鐘，包含修復、隔離重建及 Agent 接續，
不能把約 21.08 分鐘的調用合計冒稱整次耗時。
GPT 走訂閱；未取得逐次美元帳，Jev 亦只有 token 使用量，故不編造美元成本。

| GPT 任務 | 次數 | Input＋output tokens |
| --- | ---: | ---: |
| 論證完整性 | 11 | 295,685 |
| 材料–材料、Claim–材料、Claim–Claim 配對 | 32 | 607,994 |
| 既有 Claim 支持與經文異議處置 | 6 | 116,282 |
| 論據改動後的最終支持處置 | 4 | 64,531 |
| 漏 Claim 判斷 | 6 | 188,390 |
| 漏 Claim 關係查找 | 6 | 113,854 |
| 新 Claim 關係查找 | 2 | 35,391 |

主要消耗在補建、驗證論證圖，沒有證據顯示是既有 Claim statement 大量錯誤。
功能及層界通過，不代表成本已優化，也沒有同範圍舊流程對照可據此聲稱節省比例。
Agent 原設 60 次 dispatch 保護估算；最後七條仍屬原授權，另保存八次有界餘量，
原帳與原 intent 保留，最終 67 次。這不是重置使用者上限或聲稱取得新的逐次批准。

## 保存依據

根目錄：`/Volumes/Jun SSD/codex-output/claim-flash-claim-layer-seven3-20261109`。

- `authority.json`、`last-group-resource-reservation.json`：原清單、模型及保留帳目。
- `evidence-proof-view/`：唯讀連結原有下層證明，沒有創造新的下層審閱結果。
- `groups/`：各獨立任務的實際輸入、答覆、判定及交易收據。
- `held-id-recovery.json`、`argument-handoff-recovery.json`：兩項恢復、原 checkpoint 及復用依據。
- `completed-progress-manifest.json`、`completed-progress.json`：SHA 綁定的保存結果；只剩既有 owner hold。
- `summary.json`、`accounting.json`：最終變更與完整消耗；`events/29/objects.json` 是最後隔離後像。
- `cleanup.json`：臨時 SQL 已停止及刪除，正式庫零寫入。

本次不授予舊 Claim Management 資格，不更新 CVP，不部署，不重新裁定 CL015。

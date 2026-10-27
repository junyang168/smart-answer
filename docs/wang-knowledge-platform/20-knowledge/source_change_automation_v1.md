# 來源變更自動接續

> **讀者**：Developer。
> **類型**：流程。
> **狀態**：當前。
> **與代碼對齊**：2026-10-27，核對 `source_change_automation`、`source_change_transition`、Claim ID intake 及既有 CVP／論證執行入口；實際運行結果另記，隔離模型回答不作內容批准。
> **權威範圍**：段落座標未變的逐字稿校訂，從來源版本交接到 Claim、CVP 與論證層接續；不更動既有任務或舊來源修訂契約。

## 一、入口與邊界

```bash
PYTHONPATH=. backend/.venv/bin/python -m backend.pipeline.source_change_automation \
  --source-id SRC-ID --root /absolute/durable/task \
  --instruction '已授權的來源校訂與知識層更新方向'
```

編輯器成功核對 CAS 後、替換檔案前，保存來源原始位元組到資料目錄下的
`wang-knowledge-platform/source-versions/<SHA>.json`。這是單個來源版本保存，不是整庫備份。
入口按來源記錄的舊檔 SHA 尋找保存版本；也可用 `--before` 指定既有原件。

舊原始位元組已遺失時，可用 `--recovered-before` 指定恢復的完整來源。
程式要求正文、純文字、標題結構及標題位置四個投影與全部已登記雜湊吻合；
來源的正文相容欄位也必須吻合。恢復檔自己的實際 SHA 與失落的原始檔 SHA 分開保留。
這條 v3 證明不得套入舊 v2 原件證明，也不能用片段拼出完整來源的假象。

若有教授來源的人工核實，用 `--source-confirmation` 保存使用者的實際說明。
這是來源事實，不是 Claim 內容批准。教授錄音與投影片具有同等證據地位。
本入口支援教授投影片的文字摘錄；SVG 等視覺資產仍需既有專用取證入口。

新增、刪除、重新排序段落或改變座標，須先有明確定位映射；本入口拒絕猜測。
來源沒有任何既有 Claim 時，走來源抽取，不用 Update 假扮首次抽取。

## 二、執行

1. **版本交接。** 保存 before/current 原件、原來源及完整片段清單和執行方向。
   在現有短 SQL transaction 中核對來源、片段集合及每條 revision/SHA，登記新來源。
   只有前後都能逐字核對的引文才同步衍生版本欄位；失配引文留給 Update。
   人工來源確認同一交易登記。任何失敗全部回滾，不授予內容資格。
2. **來源影響審閱。** 使用明確的新 source-impact policy，Opus 閱讀完整 before/current
   單篇來源和所有本篇 Claim；Claim 之間只取 direct neighbours，不帶入鄰居講道的證據鏈。
   審閱對每條本篇 Claim 判定影響，並逐一說明改動段落的教導、經文與推理由哪些 Claim 承載。
   新增／遺漏的獨立判斷須留下普通 Update 的補建義務，不能只看已有錨點。
3. **按段落 Update。** 同一 Claim 歸於受影響的最早段落；先呼叫 p1 的 Claim ID batch，
   全部完成後才準備並呼叫 p2。後批讀取前批提交後的 SQL。未失配的引文不等於 Claim 無影響，
   前一步的語義及覆蓋 finding 一起交給 GPT。仍有有效歷史內容證明、且 Opus 確認無影響者可接續資格；
   沒有歷史證明不憑 unaffected 補發資格，若已有 CVP 消費需要資格，則安排正常 Update。
4. **核對當前資格。** 再讀全部本次目標、新建 Claim，以及本篇既有 CVP 成員。
   後批若使前批資格失效，保持未完成，不另開 action 或增加預算重審。
5. **CVP 與論證。** 對受影響成員呼叫既有 CVP management，保留正常 GPT 提議、Opus 復核、
   短交易及回讀。退休 Claim 不可留下 active 成員連接。按受影響 CVP 入既有論證隊列，
   逐一消費固定 job，使用原 worker 的認領、續租、修改次數、提交和回讀機制。
   只入隊、只提交 CVP 或只完成 Claim，都不算整個來源操作完成。

## 三、恢復與完成

同一 root 固定原方向、來源、段落計畫、子 action、policy 和 budget。
已完成來源交接查真實 ChangeSet；已完成的 Update 從 SQL 和當前資格核對，不重發模型。
已提交論證先回讀原 job，不因路線基準改變而重建 job。未知回答、額度、hold、分歧及預算耗盡
保持原任務狀態；不以新的 root、次數或「工程恢復」消除它們。

只有 Claim 當前資格、CVP 操作及所有必要論證 job 的實際回讀都通過，外層才顯示 completed。
其他結果顯示 paused，保存具體 stage 與原結果；人工問題仍走
[Claim Management 的人工返回](./claim_management_2_design.md#91-人工核实的返回)。
純機械恢復不代表 Opus 作過內容批准，模型審閱也不冒充人工確認。

新入口不導入舊 campaign 的 Gemini、整庫備份或 SSD 規則，不生成文章或部署。
既有 action 的來源、原始判定及呼叫預算不升級為本契約。

# 投影片文字與圖表證據抽取 v1

> **讀者**：Developer、Solution architect
> **類型**：規範
> **狀態**：當前
> **與代碼對齊**：2026-10-27（核對到 4161b6b6）
> **權威範圍**：轉錄 pilot 的可見文字、圖表候選、SVG 派生與論證抽取來源包；不批准文庫來源、Claim 或 Canonical Viewpoint。

## 1. 文字來自哪裡

確定投影片事件、保存原尺寸圖片後，再用視覺模型抽取可見文字。輸入只有圖片與固定 asset ID，不加入 ASR、經文標準譯文或一般神學知識。原圖 SHA 與影片 SHA 是來源身份，逐步顯示和返回事件保留各自時間；完全相同的原圖只抽取一次，但不合併出現次數。

投影片文字插在原圖附近的獨立區塊，明示「機器辨識，非口述」。口述段落與其位置、時間、文字完全保留。沒有 slide 的時段維持[空白輸出規則](./slide_visibility_output_v1.md)，不從前圖填補文字或圖像。投影片上有而未說出的內容可以成為視覺候選證據，不得被改標為教授口述。

每個文字區域保存原字串、閱讀順序、顏色、0–1000 相對座標及不確定標記。繁中、英文、希臘文及希伯來文不做拼寫或 Unicode 正規化；看不清、裁掉或被遮擋的部分記錄問題，不補全。這是辨識約束，並不保證模型一定忠實；仍需原圖核對。

## 2. 圖表先結構化，再產生 SVG

只有真正可見的圖表才產生圖形，不把列表、縮排、對比陳述或隱含論證自行畫成教授的 diagram。模型分別交回文字區域、矩形／橢圓／線／折線／多邊形，以及有實際連接線支持的節點關係。箭頭方向、顏色與不確定標記另存；不輸出「支持」「反駁」「因果」等神學或論證判斷。

程式驗證座標、封閉欄位、唯一 ID、端點與連接線引用，並以白名單圖元產生 SVG。文字經 XML escaping；不接受任意模型 SVG、script、外連圖片、事件處理器或 foreignObject。希伯來文按首個強方向字元處理，混合中文標籤不因包含希伯來文就整行倒轉。

SVG 保留可編輯文字與圖元，但字型、線寬和字距屬近似轉繪。它的身份是 `model_reconstruction`，原圖一直保留。既有 `source_projection.visual_source_blocks` 可讀取其 XML 字面事實，**可解析不等於忠實於教授原圖**，也不產生教授原件的來源 attestation。

## 3. 論證層的取材包

`visual-argument-source-packet.v1` 含以下分開的材料：

| 材料 | 來源錨點 | 狀態 |
| --- | --- | --- |
| 口述段落 | 原圖文稿 SHA、段落 ID 與原時間 | 原樣複製，不提升原有品質狀態 |
| 投影片文字 | 影片 SHA、圖片 SHA、區域 ID 與座標 | 機器辨識候選，非口述 |
| 圖形與連接線 | 同一原圖的圖元 ID、幾何座標與箭頭 | 可見幾何候選，非論證結論 |
| 出現紀錄 | 事件 ID、畫面時間、擷取時間與段落位置 | 保留多次出現 |

所有視覺區域及圖元都進取材清單，後續抽取應以這份來源清單核對是否遺漏，而不能只看已生成的 Claim。此 pilot 只產生候選來源包；正式 extraction runner 尚未直接接收此格式，SQL／文庫寫入數為零，`visual_source_attestations` 為空。

正式接入前仍需沿用[架構的來源忠實與獨立複核原則](../00-overview/solution_architecture.md)，保留原圖、OCR 和 SVG 的派生鏈。不能把模型重繪 SVG 直接套成「教授顯示或繪製的原始 SVG」；解析器的 literal facts 只是對轉繪檔的描述，不是對原片的獨立驗證。

## 4. 執行與續跑

入口 [transcript_audio_pilot.py](../../../backend/pipeline/transcript_audio_pilot.py) 新增 `visual` 階段，已有逐字稿時只追加視覺處理：

```bash
backend/.venv/bin/python -m backend.pipeline.transcript_audio_pilot visual \
  --out output/transcript-pilot/s210307-first15m \
  --slides <同片的換頁報告目錄> --visual-provider gemini
```

完整 `run` 可加 `--extract-visuals`。原音訊轉錄的續跑預設不新增 OCR 費用。模型抽取與組合也可分別使用 [slide_visual_evidence.py](../../../backend/pipeline/slide_visual_evidence.py) 與 [visual_transcript_packet.py](../../../backend/pipeline/visual_transcript_packet.py)。

單次最多 32 張已確認原圖、每批最多三張，預設每批兩張、最多 20 次生成嘗試、US$3 保守預留額；每個輸出目錄各自記帳，獨立抽樣另有紀錄。原始請求、回應、拒絕與未知狀態均保留。結果未知時不重送；完整快取可重放，圖片或設定漂移則停止。

Gemini 使用 JSON mode 加完整 schema 提示及本機封閉結構驗證；OpenAI 使用 strict JSON schema。供應商結構化輸出或本機結構檢查均不能保證 OCR 正確。抽取結果與來源包保存 `unreviewed_model_candidate`／`candidate_not_ingested`，不因兩模型一致就冒充人工批准。

獨立抽樣只讀相同原圖，不讀初稿答案。`compare_sample` 保存精確文字差異與兩份來源 SHA，不自動修字；圖文稿顯示差異提醒。抽樣不是全片的忠實性複核，也沒有繞過正式文庫的複核程序。

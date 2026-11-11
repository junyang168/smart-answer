# 音訊轉錄與投影片組合試跑（2026-10-27）

> **讀者**：Developer、Solution architect
> **類型**：記錄
> **狀態**：當前
> **與代碼對齊**：2026-10-27（核對到 c539d3ce）
> **權威範圍**：S 210307 前 15 分鐘的轉錄、中文對齊、標點分段、原圖插入實測；不代表人工校對完成或正式 ingestion 品質。

## 1. 結果與交付

同一原片的前 900 秒已串成 32 段文字與 24 次原畫面，包含 17 次逐步顯示與 7 次新頁。游標和控制列變動的排除沿用[投影片換頁辨識試跑](./slide_change_pilot_20261027.md)。沒有覆寫既有逐字稿，也沒有部署。

[私人圖文試稿](https://chatgpt.com/space/page_d789bf6a89648191957ff6539322bae3) 已上傳 24 張原尺寸 PNG 和核對用 MP3。上傳後核對圖片 SHA，讀回 Page 確認 24 個原生圖片區塊與全部 32 段文字。Page 是遠端可用的交付；本機 HTML 僅供檢查，沒有把 localhost 當成交付網址。

試稿仍有 12 處啟發式疑詞標記及 4 個切段接縫需要回聽。這些不是已證實的轉錄錯誤，也不是錯誤總數。沒有完成逐字人工基準、時間精度基準或希臘文／希伯來文辨識評測。

## 2. 實際執行的流程

1. FFmpeg 從原片抽取 16 kHz、單聲道 PCM16 音訊，以原影片零點保留音軌偏移；音軌較晚開始時補前導靜音。非零影片起點直接停止，避免猜測時間差。
2. 在約三分鐘附近尋找低能量區間切段；不刪靜音。五段的核心邊界為 0、180350、361600、542500、722600、900000 ms，完整覆蓋 900 秒。每個內部邊界兩側各保留兩秒，實際送出 916 秒音訊。
3. `gpt-transcribe` 逐段轉錄，提示以繁體中文為主、保留英文與實際說出的希臘文／希伯來文。語言提示為 `zh-tw,en,el,he`；沒有把投影片文字放進 ASR prompt。
4. 本機 `Qwen/Qwen3-ForcedAligner-0.6B-hf` 對齊中文，固定 revision `c07281df297b9905d24a508279258cccf987a064`。隔離 runtime 使用 torch 2.14.1、transformers 5.21.0、MPS float16。
5. 使用原始 timestamp class argmax（每格 80 ms），跳過 SDK 的插值／時間修補。以原始 Unicode 位置對回 ASR；重疊區依聲學時間選核心歸屬，被排除的前後文字仍保存在原始回應及歸屬清單。
6. `gpt-6.1-sol` 只回傳標點操作與段落邊界。程式拒絕改字、刪重複、改經文章節、切開外文或數字。第一份計畫有一個錯誤 offset，程式攔下後僅修復一次；兩次回應均保留。最終 44 個操作、32 段，原文字範圍完整依序覆蓋，沒有字詞增刪。
7. 將原投影片按畫面時間掛到涵蓋該時間或下一個完整段落，保留畫面時間、擷取時間及距離。沒有拆句來迎合換頁，也沒有把圖片文字補入口述。最初畫面出現於約 1 秒，首次口述約 40 秒，因此該圖片與段落的 39 秒差距明確保留。

## 3. 時間與品質界限

| 對齊結果分類 | 數量 | 解讀 |
| --- | ---: | --- |
| 中文可用字時間預測 | 2015 | 僅通過有效性檢查，未量測準確度 |
| 無效或零長度 | 158 | 不補造時間 |
| 非單調 | 64 | 不插值修補 |
| 外文僅段落時間 | 27 | 保留文字，不宣稱外文字級對齊 |

32 段均能取到中文聲學錨點，因此顯示段落時間；這不代表段內所有字都有可靠時間。Qwen 模型運算合計 16.46 秒，不包含初次下載、載入及其他步驟，不是整條流程耗時。

ASR 可能把熟悉的經文、人名或外文辨識錯誤。此試跑保留原始結果，只標示待回聽候選，沒有用經文常識或 OCR 靜默修字。重疊段落的聲學歸屬仍需核對，不能當成無損拼接已獲人工確認。換頁時間沿用候選偵測器，仍可能受短暫游標變動影響事件起點；「游標不是新頁」不等於換頁時刻已達幀級準確。

## 4. 成本、續跑與追溯

五次 ASR 估算 US$0.0687；兩次標點計畫合計估算上界 US$0.09112，本輪約 US$0.15982，不含上一輪換頁辨識約 US$0.23。ASR 依每分鐘 US$0.0045 計算；費率參考 [OpenAI pricing](https://developers.openai.com/api/docs/pricing)，不是帳單確認。

付費呼叫限制為 16 次及 US$3 的保守預留額。每次先保存 request 與未知結果狀態，再保存原始 response；timeout 等結果不明時禁止自動重送，SDK retries 為零。完整續跑後仍是 7 個付費請求，沒有新增 ASR 或格式化請求。續跑的 token-count 查詢與模型快取載入仍可能需要網路。

原片 SHA 為 `43443ed0c1c0c25cc25378379fca26cfc53e1aafaedee1f4b8f0fab20419fe72`。各階段以來源、文字、圖片、模型設定與產物 SHA 綁定；API 金鑰不寫入產物。原音訊、ASR、模型回應和圖文全文放在被忽略的 `output/transcript-pilot/s210307-first15m/`；Git 僅保存[實測摘要](../../../backend/tests/fixtures/transcript_pilot_summary.json)，不提交教授逐字稿全文。

## 5. 重跑與驗證

入口是 [transcript_audio_pilot.py](../../../backend/pipeline/transcript_audio_pilot.py)，選填的本機對齊套件以[獨立 requirements](../../../backend/requirements-transcript-alignment.txt) 安裝，沒有改動共用 backend venv：

```bash
backend/.venv/bin/python -m venv .venv-align
.venv-align/bin/python -m pip install -r backend/requirements-transcript-alignment.txt
backend/.venv/bin/python -m backend.pipeline.transcript_audio_pilot run \
  --video '/Volumes/Jun SSD/data/video/S 210307.mp4' \
  --out output/transcript-pilot/s210307-first15m \
  --slides ../wkp-470-slide-change-pilot/output/slide-pilot/s210307-first15m
```

`--slides` 指向同一原片的換頁試跑目錄，正式付費前會核對來源 SHA。需 FFmpeg、FFprobe、OpenAI 金鑰及足夠磁碟；首跑需下載對齊模型。預設使用 Apple MPS，可指定 `--device cpu`；CPU 未做本次效能評測。

28 個 backend 測試通過，涵蓋原有換頁測試及新增的音軌延遲、切段覆蓋、PCM 運算、外文組合字、原始時間保留、標點限制、重複口述保留、圖片位置、預算與快取完整性。另有前端 `tsc --noEmit` 通過。這些驗證程式行為，不能替代音訊回聽。

本輪修正了續跑時對 venv Python 使用 `resolve()` 造成套件遺失的問題，改為保留 venv 入口路徑並加入回歸測試。原始請求和結果仍完整保存。

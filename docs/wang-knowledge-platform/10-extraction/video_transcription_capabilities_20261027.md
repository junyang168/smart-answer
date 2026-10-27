# 錄像轉錄與投影片模型能力查證記錄 2026 10 27

> **讀者**：Solution architect、Developer。
> **類型**：記錄。
> **狀態**：當前。
> **與代碼對齊**：不適用；記錄官方能力查證及本機閱讀結果，沒有新管線實作成績。
> **權威範圍**：僅描述 2026-10-27 本輪查證；流程規則見 [錄像轉錄與投影片時間軸流程](./video_transcription_pipeline_v1.md)。

## 1 需求與查證範圍

本輪對話確認：正文以繁體中文為主，夾少量英文、聖經希臘文及希伯來文；對齊只要求中文；轉錄後需要標點與分段；換頁辨識是投影片擷取的主要困難；投影片希望按時間自動嵌入文字。

本輪讀取官方文件、現有程式、既有截圖及少量錄像影格。沒有安裝或執行候選新模型，沒有呼叫付費模型重轉錄，也沒有量測新流程的準確率、速度或費用。官方語言清單不能證明教授的口音、聖經原文讀法及中外文混讀已達可用品質。

## 2 官方模型能力

| 項目 | 當日查證結果 | 對方案的影響 |
| --- | --- | --- |
| OpenAI `gpt-transcribe` | 檔案轉錄支援 `prompt`、`keywords`、`languages`；檔案上限 25 MB | 可試講道背景及相關術語提示，實作仍須驗證每段輸出與完整性 |
| OpenAI 時間戳相容性 | 遷移文件未把字詞／段落時間戳、SRT／VTT 列為新模型可直接取代的能力 | 方案不依賴 `gpt-transcribe` 原生時間戳；另做對齊 |
| OpenAI 舊轉錄模型 | 官方列出 `whisper-1` 與 GPT-4o 轉錄系列於 2027-02-26 停用 | 不把換成 GPT-4o 系列當長期升級終點 |
| Gemini 3.5 Transcribe | 穩定版模型支援語碼切換與詞級時間戳；詞級時間戳與自訂詞彙互斥，啟用時間戳可能降低辨識品質 | 分開測試原生時間戳與有詞彙提示的路線，不在同一請求混用不相容功能 |
| Gemini 轉錄模式 | `verbatim` 保留口述；`smart` 會處理重複、口誤、自我修正與格式，且不相容時間戳 | 轉錄比較使用 `verbatim`；標點、分段獨立處理 |
| Gemini 轉錄長度與語言 | 一般檔案最長一小時，時間戳／說話人功能最長三十分鐘；列出華語簡體、英文、希臘文及希伯來文 | 仍須繁體呈現與真實混讀驗證，不能把語言清單當成聖經原文精度證明 |
| Qwen3-ForcedAligner-0.6B | 可接外部音訊／文字對齊，列出中文及英文等十一種語言，單次最長五分鐘；對齊清單沒有希臘文／希伯來文 | 滿足中文候選範圍；外文詞保留所在短句的時間精度，繁體適配仍需試驗 |
| WhisperX | 預設對齊模型清單有中文、英文、希臘文與希伯來文，但載入及對齊按語言處理 | 列作替代候選；不能推定同句混讀可自動可靠對齊 |
| Gemini 3.8 Flash、4 Argon | 官方列為穩定模型，接收影像／影片並支援結構化輸出 | 可試版面、前後內容分類與密集小字判讀，未取得本語料成績 |
| Gemini 影片輸入 | 預設 static 模式約 1 FPS，可調採樣；agentic 模式自行選取時間與模態 | 都不能單獨證明完整換頁召回，需本地掃描與保留集標注 |
| GPT-6.1 Sol | 官方列出文字／影像輸入與結構化輸出 | 適合作格式操作的候選模型，不能直接聽音訊核實字詞 |

## 3 官方來源

以下來源在本輪實際讀取；它們支持能力與相容性敘述，不支持教授錄音上的品質、費用節省或全庫完整性：

- [OpenAI File transcription](https://developers.openai.com/api/docs/guides/speech-to-text)。
- [OpenAI 遷移相容性說明](https://developers.openai.com/cookbook/examples/migrating_from_whisper_to_gpt_transcribe#8-check-compatibility-before-switching)。
- [OpenAI 轉錄模型停用公告](https://developers.openai.com/api/docs/deprecations#2026-08-26-transcription-models)。
- [Gemini 3.5 Transcribe 模型與限制](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-transcribe)。
- [Gemini Audio transcription 設定與模式](https://ai.google.dev/gemini-api/docs/transcribe)。
- [Qwen3-ASR 與 ForcedAligner 官方說明](https://github.com/QwenLM/Qwen3-ASR)。
- [WhisperX 官方對齊實作](https://github.com/m-bain/whisperX/blob/main/whisperx/alignment.py)。
- [Gemini 3.8 Flash 模型](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash)。
- [Gemini 4 Argon 模型](https://ai.google.dev/gemini-api/docs/models/gemini-4-argon)。
- [Gemini Video understanding](https://ai.google.dev/gemini-api/docs/video-understanding)。
- [GPT-6.1 Sol 模型](https://developers.openai.com/api/docs/models/gpt-6.1-sol)。
- [Silero VAD 官方專案](https://github.com/snakers4/silero-vad)。
- [PySceneDetect 偵測方法](https://www.scenedetect.com/docs/latest/api/detectors.html)。

## 4 既有程式與錄像觀察

舊工具位於本機 `church/` 與 `screen_detector/`，不在本 PR 的程式碼範圍。初次閱讀的 Smart Answer 主 checkout 為 `af9e2eabf2cb82e8f25bc86a6e663d7b55d7a675`；設計 worktree 從較新的 `12cbb6dcd87c34b563b6118fd7354f0d115e009d` 建立，已補讀其 AGENTS 與架構變動。下表描述本輪觀察，不表示完整系統審計。

| 位置 | 觀察 |
| --- | --- |
| `church/audio_transcriber.py` | 固定使用 `whisper-1` 與 SRT，完整轉錄後才保存；切段的整數平方 RMS 音量計算可溢位 |
| 同檔 SRT 解析 | 解析錯誤會繼續下一段；用三個合成區塊重現返回索引 1、3，沒有整體失敗 |
| `church/processing_pipeline.py` | 前段返回 `False` 時未阻止後段；用不執行 API 的替身 processor 重現轉錄失敗後仍呼叫校訂 |
| `church/processor_correct_transcribe.py` | 以約四萬字為批次讓文字模型校訂；既有差異檢查呼叫已註解，索引只取模型返回的首尾 |
| `church/slide_detector.py` | 用 scene 變化擷取後以整張圖平均藍色過濾；影格與時間分開抽取，數量不同仍 zip 截短 |
| `screen_detector/process_slide_changes.py` | 以投影片區域相似度找變化；部分分支以 OCR 有文字為保留條件，無法保存所有純圖狀態 |
| `S 210307.mp4` 抽樣 | 第 1、60、180 秒樣本顯示同一頁內容但游標／選框不同；第 240、300 秒樣本為播放模式及另一頁／內容版本，說明版面與內容狀態須分開判定 |
| 現有編輯器 | 已能手動插圖片 Markdown；閱讀頁會把段落文字合成 Markdown，尚無本設計的獨立圖文區塊流程 |

索引區間檢查讀取外接磁碟的 177 組原始與校訂稿副本，計得 112 組存在未覆蓋原始索引、11 組存在重疊、2 組超出原始索引範圍。這不是內容刪除統計：原因可能包含模型索引錯誤、版本不配對、人工編輯或來源本身有過渡語；未逐一回聽確認。這些數字不得作為新管線品質或正式庫現況的分母。

合成音量案例用 `int16` 陣列 `[10000, -10000, 10000, -10000]` 套既有平方／平均／平方根公式得到 `NaN`，先轉浮點後得到 `10000.0`。該重現證明公式有型別風險，不證明每一份舊錄像都已切錯。

## 5 待試驗事項

尚需以分離的調參／保留樣本及完整單講驗證：繁體中文與混讀辨識、原文詞形、對齊副本映射、內容狀態漏檢、快速頁面、圖片選取及插圖邊界、恢復續跑、每小時成本和人工校對時間。模型可用性與帳號權限也尚未以實際呼叫驗證。

依此形成的流程、初始參數與擬定門檻集中於 [流程設計](./video_transcription_pipeline_v1.md)，不把本次查證變成永久模型版本保證。

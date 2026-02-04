# 分階段實作 Prompts

以下是分階段實作的 prompt，每個階段都是獨立可測試的。完成一個階段後，測試確認沒問題，再進入下一個階段。

---

## Phase 1：專案骨架 + EPUB 解析 + 基本分段

```
請幫我實作 EPUB 智能翻譯系統的第一階段。請參考 CLAUDE.md、ARCHITECTURE.md、REQUIREMENTS.md、SPECIFICATION.md 了解完整設計。

這個階段的目標是：建立專案基礎結構，能夠讀取 EPUB 檔案、提取文字內容、進行智能分段。

請完成以下工作：

1. 建立 Python 專案結構
   - 建立 pyproject.toml 或 requirements.txt（依賴：ebooklib, beautifulsoup4, click, lxml）
   - 建立 src/core/、src/epub/、src/glossary/、src/ui/ 目錄結構
   - 建立 data/、output/ 目錄

2. 實作 EPUB 解析器（src/epub/extractor.py）
   - 使用 ebooklib 讀取 EPUB 檔案
   - 提取所有章節的 HTML 內容
   - 使用 BeautifulSoup 從 HTML 中提取純文字，同時保留結構標記（標題、段落、程式碼區塊、列表等）
   - 提取書籍 metadata（書名、作者等）
   - 提取目錄結構

3. 實作智能分段處理器（src/core/segment_processor.py）
   - 將提取的內容切分成 300-500 字的段落（可配置）
   - 在句號、問號、感嘆號等自然斷句處分段
   - 程式碼區塊（<pre>, <code>）保持完整，獨立成段
   - 標題（<h1>-<h6>）獨立成段
   - 列表保持完整性
   - 每個段落記錄來源章節和位置資訊

4. 實作基本 CLI 入口（translate.py）
   - 使用 Click 框架
   - `python translate.py init <epub_file>` 指令：
     - 讀取 EPUB
     - 執行分段
     - 將專案資訊存成 JSON 檔案（存到 data/projects/ 目錄）
     - 顯示書籍資訊和分段統計

5. 建立專案資料結構（src/core/project_manager.py）
   - Project JSON 格式包含：id, name, source_path, created_at, settings, progress, segments
   - 每個 Segment 包含：id, chapter_id, position, original, translated, status, type, metadata
   - 支援 save/load 專案

驗收標準：
- 能用 `python translate.py init test.epub` 初始化一個專案
- 專案 JSON 檔案正確包含所有分段
- 分段大小在設定範圍內
- 程式碼區塊和標題正確獨立成段
- 顯示分段統計資訊（總段數、各章段數等）

請先閱讀所有規格文件，然後開始實作。實作完成後請告訴我如何測試。
```

---

## Phase 2：Claude Code 翻譯引擎 + 進度追蹤

```
請幫我實作 EPUB 智能翻譯系統的第二階段。請先閱讀所有規格文件和已完成的程式碼，了解目前的狀態。

這個階段的目標是：整合 Claude Code CLI 進行翻譯，實現逐段翻譯和斷點續傳。

請完成以下工作：

1. 實作 Claude Code 翻譯介面（src/core/translation_engine.py）
   - 使用 subprocess 調用 claude CLI：
     ```python
     # 使用 -p 參數進行 pipe 翻譯
     result = subprocess.run(
         ['claude', '-p', prompt],
         input=text,
         capture_output=True, text=True, timeout=120
     )
     ```
   - 建立翻譯 prompt 模板，包含：
     - 翻譯成繁體中文的指令
     - 術語表（如果有的話）
     - 保留原文術語在括號內的要求
     - 符合台灣中文閱讀習慣的要求
     - 針對不同內容類型（一般段落、程式碼、標題）的特殊指令
   - 重試機制（最多 3 次）
   - 錯誤處理和日誌記錄

2. 實作翻譯進度追蹤
   - 每翻譯完一個段落，立即更新專案 JSON
   - 記錄每段的翻譯狀態：pending → translating → completed / error
   - 記錄翻譯時間戳

3. 實作 CLI 翻譯指令
   - `python translate.py start` ：從頭開始翻譯（跳過已完成的段落）
   - `python translate.py resume` ：從上次中斷處繼續
   - 翻譯過程中顯示進度（當前段落 / 總段落數）
   - 支援 Ctrl+C 安全中斷（完成當前段落後停止）
   - `python translate.py status` ：顯示翻譯進度統計

4. 程式碼區塊的特殊處理
   - 程式碼區塊只翻譯註釋，保留程式碼本體
   - 在 prompt 中明確指示 Claude 區分程式碼和註釋

驗收標準：
- 能用 `python translate.py start` 開始翻譯
- 翻譯過程中按 Ctrl+C 可以安全中斷
- 用 `python translate.py resume` 可以從中斷處繼續
- `python translate.py status` 顯示正確的進度
- 翻譯結果存在專案 JSON 中
- 程式碼區塊的程式碼本體沒有被翻譯
```

---

## Phase 3：術語表管理

```
請幫我實作 EPUB 智能翻譯系統的第三階段。請先閱讀所有規格文件和已完成的程式碼。

這個階段的目標是：實作術語表管理，確保整本書的術語翻譯一致性。

請完成以下工作：

1. 實作術語管理器（src/glossary/glossary_manager.py）
   - 術語表儲存為 JSON 檔案（data/glossary/）
   - 每個術語項目包含：term, translation, category, frequency, contexts
   - 支援 CRUD 操作
   - 支援匯入/匯出術語表

2. 實作術語自動擷取
   - 在翻譯前，先用 Claude Code 掃描全書內容，提取專業術語
   - CLI 指令：`python translate.py extract-terms`
   - 使用 prompt 讓 Claude 識別技術術語、專有名詞、重要概念
   - 輸出格式為 JSON

3. 實作台灣術語查詢
   - 對擷取的術語，使用 Claude Code 查詢台灣慣用翻譯
   - prompt 中要求使用台灣的技術社群常用譯法
   - 例如：machine learning → 機器學習（不是机器学习）
   - 將查詢結果更新到術語表

4. 修改翻譯引擎整合術語表
   - 翻譯每個段落時，在 prompt 中帶入相關術語表
   - 只帶入該段落中出現的術語（不要帶入全部術語表）
   - 翻譯後檢查術語是否正確套用

5. 術語表 CLI 指令
   - `python translate.py glossary show` ：顯示當前術語表
   - `python translate.py glossary add <term> <translation>` ：手動新增
   - `python translate.py glossary export` ：匯出
   - `python translate.py glossary import <file>` ：匯入

驗收標準：
- 能自動擷取書中的技術術語
- 術語表包含台灣慣用翻譯
- 翻譯時術語保持一致
- 術語格式為：機器學習（machine learning）
- 可以手動管理術語表
```

---

## Phase 4：EPUB 重建 + 多格式輸出

```
請幫我實作 EPUB 智能翻譯系統的第四階段。請先閱讀所有規格文件和已完成的程式碼。

這個階段的目標是：將翻譯完成的內容重建為 EPUB 檔案，支援多種輸出格式。

請完成以下工作：

1. 實作 EPUB 建構器（src/epub/builder.py）
   - 使用 ebooklib 建立新的 EPUB 檔案
   - 保留原書的 CSS 樣式
   - 保留原書的圖片和多媒體
   - 重建目錄結構（TOC）
   - 保留原書的 metadata 並加上翻譯資訊

2. 實作三種輸出格式
   - chinese-only：純中文，取代原文
   - bilingual-horizontal：左右並排，使用 CSS table 或 flexbox
     ```html
     <div class="bilingual-row">
       <div class="original">English text...</div>
       <div class="translated">中文翻譯...</div>
     </div>
     ```
   - bilingual-vertical：上下排列
     ```html
     <div class="bilingual-block">
       <div class="original">English text...</div>
       <div class="translated">中文翻譯...</div>
     </div>
     ```

3. 為雙語格式設計 CSS
   - 原文用較淺的顏色或較小的字體
   - 譯文用正常顏色和字體
   - 響應式設計，適配不同閱讀器
   - 確保在 Calibre 中正常顯示

4. 實作 CLI 匯出指令
   - `python translate.py export --format chinese-only`
   - `python translate.py export --format bilingual-horizontal`
   - `python translate.py export --format bilingual-vertical`
   - 輸出到 output/ 目錄
   - 檔名包含格式資訊

5. 處理特殊元素的格式還原
   - 程式碼區塊恢復 <pre><code> 格式
   - 標題恢復正確的 heading 層級
   - 列表恢復正確的 HTML 結構
   - 圖片保留原始引用

驗收標準：
- 能生成三種格式的 EPUB 檔案
- 在 Calibre 中能正確顯示
- 雙語格式排版美觀
- CSS 樣式正確套用
- 原書的圖片和格式被保留
```

---

## Phase 5：GUI 校對介面

```
請幫我實作 EPUB 智能翻譯系統的第五階段。請先閱讀所有規格文件和已完成的程式碼。

這個階段的目標是：建立 PyQt5 圖形化校對介面，讓我可以方便地檢視和編輯翻譯結果。

請完成以下工作：

1. 實作主視窗（src/ui/main_window.py）
   - 使用 PyQt5
   - 檔案選擇：開啟 EPUB / 載入專案
   - 進度顯示：進度條 + 數字統計
   - 翻譯控制：開始 / 暫停 / 繼續 按鈕
   - 匯出按鈕：選擇格式後匯出
   - 狀態列：顯示當前操作狀態

2. 實作校對編輯器（src/ui/review_editor.py）
   - 左右分割視圖（可拖動調整比例）：
     - 左側：原文（唯讀）
     - 右側：譯文（可編輯）
   - 上方工具列：
     - 切換顯示模式（並排 / 上下 / 僅原文 / 僅譯文）
     - 段落導航（上一段 / 下一段 / 跳轉到指定段落）
     - 儲存按鈕
   - 下方狀態：當前段落編號 / 總段數 / 翻譯狀態
   - 段落列表側欄：
     - 顯示所有段落
     - 用顏色標記狀態（已翻譯 / 待校對 / 已校對 / 錯誤）
     - 點擊跳轉

3. 實作術語表面板
   - 可展開/收合的側邊面板
   - 顯示當前段落中用到的術語
   - 可以直接編輯術語翻譯
   - 修改術語後提示重新翻譯受影響的段落

4. 實作 GUI 啟動入口（gui_launcher.py）
   - 啟動主視窗
   - 處理命令行參數（可選直接開啟專案）

5. 連接 GUI 和後端邏輯
   - 翻譯任務在背景執行緒中運行
   - 即時更新進度條
   - 編輯後自動儲存到專案 JSON

驗收標準：
- `python gui_launcher.py` 可以啟動 GUI
- 能載入專案並顯示翻譯結果
- 可以逐段校對翻譯
- 可以直接編輯譯文並儲存
- 可以從 GUI 啟動翻譯任務
- 進度即時更新
- 可以從 GUI 匯出 EPUB

需要額外安裝：pip install PyQt5
```

---

## Phase 6：品質提升 + 完善

```
請幫我實作 EPUB 智能翻譯系統的第六階段（最終階段）。請先閱讀所有規格文件和已完成的程式碼。

這個階段的目標是：提升整體品質、完善細節、增加穩定性。

請完成以下工作：

1. 翻譯品質提升
   - 在翻譯 prompt 中加入上下文：翻譯每個段落時，附帶前一段和後一段的原文作為上下文
   - 數學公式保護：識別 LaTeX 公式（$...$, $$...$$, \[...\]）並保留不翻譯
   - 表格處理：翻譯表格中的文字內容，保留表格結構
   - 註腳處理：翻譯註腳內容，保持對應關係

2. 錯誤處理強化
   - Claude Code 調用失敗時的重試策略（指數退避）
   - EPUB 解析錯誤的友善提示
   - 翻譯結果的基本驗證（檢查是否為空、是否包含亂碼）
   - 操作日誌記錄到檔案（data/logs/）

3. 設定檔系統（config.json）
   - 分段大小設定
   - Claude Code 超時設定
   - 翻譯 prompt 自定義
   - 輸出格式偏好
   - 自動儲存間隔

4. 搜尋與取代功能
   - 在 GUI 中增加搜尋功能（Ctrl+F）
   - 支援在所有譯文中搜尋
   - 批量取代功能（適合統一修改術語）

5. 一致性檢查
   - CLI 指令：`python translate.py check`
   - 檢查所有已翻譯段落中的術語是否與術語表一致
   - 輸出不一致的段落列表
   - 提供一鍵重新翻譯不一致段落的選項

驗收標準：
- 數學公式、表格、註腳正確處理
- Claude Code 失敗時能自動重試
- 設定檔生效
- 搜尋取代功能正常
- 一致性檢查能發現不一致的術語
```

---

## 使用方式

1. 將 Phase 1 的 prompt 貼到 Claude Code
2. 測試所有功能，確認沒問題
3. 如果需要調整，跟 Claude Code 討論修改
4. 確認 OK 後，進入 Phase 2
5. 依此類推

每個階段之間可以隨時調整需求。如果某個階段的功能需要修改，直接在該階段跟 Claude Code 溝通即可。

### 測試用 EPUB

建議準備一本小型的英文 EPUB 電子書（3-5 章）作為測試。可以從 Project Gutenberg (gutenberg.org) 下載免費的英文電子書。

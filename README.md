# EPUB 智能翻譯系統

把英文 EPUB 電子書翻譯成繁體中文。翻譯透過命令列模型後端（Claude Code CLI 或 GitHub Copilot CLI）進行，支援術語表、書籍簡介、譯文結構檢查與失敗重試、三種輸出格式，以及 Web 審閱介面與 CLI 兩種操作方式。

## 系統需求

- **Python 3.9+**
- 至少一個模型後端（見下方「模型後端」）：
  - **[Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code/overview)**（`claude`，預設後端），需登入
  - **GitHub Copilot CLI**（`copilot`），需登入（`copilot login`）

## 安裝

```bash
git clone <repo-url> epub-translator
cd epub-translator
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt   # 執行測試需要（測試用 ebooklib 產生範例 EPUB）

python3 -m pytest tests/ -q           # 確認安裝
```

## 模型後端

| 後端 | 指令 | 模型 | 登入 |
|---|---|---|---|
| `claude-cli`（預設） | `claude -p` | `sonnet`（預設）、`opus`、`haiku` | `claude /login` |
| `copilot-cli` | `copilot -p` | `claude-sonnet-5.5`（預設）、`gpt-6.2-sol`、`gemini-4-carbon`、`claude-haiku-4.5` | `copilot login` |

- 後端與模型存在專案裡：CLI 用 `start --backend/-b`、`--model/-m` 設定（之後的執行沿用），Web 在「翻譯」頁的模型選單切換，翻譯中切換會從下一段開始生效。
- 每次呼叫都以一句翻譯用的系統提示取代 CLI 內建的提示，並且不給模型任何工具；子程序在系統暫存目錄執行。
- **Copilot 後端的需求與風險**：需要支援 `--experimental` 與 `--plugin-dir` 的 Copilot CLI（以 1.0.114 驗證）；系統提示是由 `src/llm/copilot_plugin/` 的擴充功能替換的，這依賴未公開的實驗性介面，每次呼叫都會檢查替換是否生效，無法確認時整個翻譯停止（不會改用約 2000 token 的內建提示繼續）。Copilot 更新後可能需要調整後端；它的使用量上限與登入錯誤訊息是依文件推測的。

## 使用方式

### Web 介面

```bash
python3 -m flask --app src.web.server:create_app run --port 3000
```

開啟 http://localhost:3000：

1. **首頁** — 專案列表；「新增專案」上傳 EPUB
2. **總覽** — 各章節進度
3. **術語表** — 新增、編輯、批次刪除、自動提取；人物可填性別與備註
4. **翻譯** — 選擇後端與模型、worker 數、書籍簡介；背景翻譯並即時顯示進度（SSE），可暫停、繼續，可重試失敗段落
5. **審閱** — 原文譯文對照編輯與核可、單段重新翻譯、只顯示有問題的段落
6. **搜尋** — 以關鍵字找段落並重新翻譯選取的段落
7. **匯出** — 選擇格式，產生並下載 EPUB

### CLI

```bash
python3 translate.py init book.epub                 # 建立專案 projects/book/（複製 EPUB、分段）
python3 translate.py status                         # 進度
python3 translate.py brief set "奇幻小說，語氣輕鬆，給青少年讀者"   # 書籍簡介（空字串移除）
python3 translate.py glossary extract               # 自動提取術語
python3 translate.py glossary add "Mira Vale" "米拉·維爾" -c person -g 女 -n "主角的姐姐"
python3 translate.py glossary show
python3 translate.py start                          # 翻譯待翻譯段落（可中斷，再執行即繼續）
python3 translate.py start -b copilot-cli -m claude-haiku-4.5 -w 4
python3 translate.py start --retry-failed           # 連同先前失敗的段落一起翻譯
python3 translate.py validate projects/book         # 檢查已存的譯文（唯讀）
python3 translate.py export -f bilingual-horizontal # 輸出到 projects/book/output/
python3 translate.py review                         # 開啟審閱頁（預設 port 5000）
```

- 多數指令以 `-p/--project <專案目錄>` 指定專案；不指定時取 `projects/` 下找到的第一個專案，有多個專案時請明確指定。
- `glossary import/export <json>` 匯入、匯出術語表。
- 各指令的完整選項：`python3 translate.py <指令> --help`。

## 翻譯流程重點

- **分段**：章節切成區塊（段落、標題、清單、表格、程式碼等），區塊不會被拆開；一段約 300–500 個英文單字，表格與程式碼各自一段。新專案的標題與其後內容合為一段，沒有註解行的程式碼原樣保留、不送模型（較早建立的專案沿用舊規則：標題自成一段）。
- **保護與檢查**：送給模型的 HTML 屬性以編號取代，回覆再還原；每段回覆都要通過結構檢查（區塊與標籤與原文一致、程式碼只能改註解）。未通過會附上錯誤重問一次，必要時把段落拆成兩半，每段最多 4 次呼叫；仍失敗則標為 `failed`，原譯文保留，診斷寫在 `log/failed/`，可用 `start --retry-failed` 重試。品質問題（長度比例、未翻譯、術語未套用）只警告，顯示在審閱頁。
- **術語表**：提示中附上術語（人物的性別與備註也一併附上）。自動提取分批進行，同一術語有不同譯法時再用一次呼叫統一；Web 只加入術語表沒有的術語，CLI 的 `glossary extract` 也會更新既有術語的譯法（保留性別與備註）。
- **書籍簡介**：類型、語氣、讀者對象，放在每段提示的最前面（上限 2000 字）。
- **暫停與錯誤**：使用量上限時暫停，依後端給的時間（否則 300 秒）自動繼續；登入過期或後端無法啟動時停下等使用者處理（CLI 直接結束，exit 1）。暫停會中止進行中的呼叫，那些段落回到待翻譯。連續 5 段發生非預期錯誤時整個翻譯停止。
- **存檔**：翻譯結果每 10 秒或每 20 段寫入一次 `project.json`，暫停、停止、結束或失敗時一定寫入；當機最多遺失最後一個區間的結果。同一個專案同時只能有一個寫入者（`project.lock`），CLI 翻譯中時 Web 無法修改該專案。

## 專案格式與遷移

新專案是 schema 2（段落記錄所屬區塊範圍與指紋）。舊專案（schema 1）可以翻譯，但匯出與 `validate` 前要先遷移一次：

```bash
python3 translate.py migrate projects/book --dry-run   # 只報告
python3 translate.py migrate projects/book             # 全部章節對得上才寫入
```

遷移前的檔案保留為 `project.schema1.json`。

## 輸出格式

| 格式 | 說明 |
|---|---|
| `chinese-only` | 純繁體中文（Web 預設） |
| `bilingual-horizontal` | 左右對照（CLI 預設） |
| `bilingual-vertical` | 上下對照 |

匯出以專案自己的 `source.epub` 為底，只改動有譯文的章節、OPF、NCX、導覽文件（雙語格式另加一個樣式表），其餘檔案原樣複製。未通過檢查的段落以原文輸出，匯出報告會列出原因。

## 專案資料

```
projects/<name>/              # 不進版控
├── project.json              # 專案狀態與所有段落
├── project.lock              # 寫入鎖
├── glossary.json             # 術語表
├── source.epub               # 原書副本
├── log/                      # Web 翻譯日誌 translation_*.log；failed/ 為失敗段落的診斷
└── output/                   # 匯出的 EPUB
```

## 測試

```bash
python3 -m pytest tests/ -q -p no:cacheprovider -rfw
```

突變測試（需要 Python 3.10+ 的環境安裝 `requirements-dev.txt`）：`scripts/verify/mutation.sh main`。`scripts/verify/` 其他腳本見 [CLAUDE.md](CLAUDE.md)。

## 授權

MIT License

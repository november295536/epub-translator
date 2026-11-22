# EPUB 智能翻譯系統

把英文 EPUB 電子書翻譯成繁體中文。翻譯透過命令列模型後端（Claude Code CLI 或 GitHub Copilot CLI）進行，支援術語表、書籍簡介、譯文結構檢查與失敗重試、三種輸出格式，以及 Web 審閱介面與 CLI 兩種操作方式。

## 系統需求

- **Python 3.9+**，macOS 或 Linux（用到 `fcntl` 與 POSIX 程序群組，原生 Windows 無法執行）
- 至少一個模型後端（見下方「模型後端」）：
  - **[Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code/overview)**（`claude`，預設後端），需登入
  - **GitHub Copilot CLI**（`copilot`），需登入（`copilot login`）

## 安裝

```bash
git clone "$REPO_URL" epub-translator   # REPO_URL：本專案的 git 網址
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

- 後端與模型存在專案裡，新專案預設 `claude-cli`。CLI 用 `model set -b/-m` 只儲存設定（`model show` 顯示目前的設定與可用模型），或用 `start --backend/-b`、`--model/-m` 設定後直接開始翻譯；之後的執行沿用。Web 在「翻譯」頁的模型選單切換（只儲存設定），翻譯中切換會從下一段開始生效。換成另一個後端且不指定模型時用該後端的預設模型（指定的是專案目前的後端時不改動模型）；模型必須屬於要使用的後端，否則拒絕且不改動專案。`glossary extract` 使用專案目前的後端。
- **推理強度與呼叫逾時**：也存在專案裡，與模型一樣設定（CLI `model set -e/--effort`、`-t/--timeout`，`model show` 顯示目前的值與模型可用的推理強度；Web「翻譯」頁模型選單旁的「推理強度」與「呼叫逾時（秒）」），翻譯中變更從下一段開始生效，翻譯與術語提取都使用。
  - 推理強度：`sonnet`、`opus`、`haiku`、`claude-sonnet-5.5`、`gpt-6.2-sol` 可選 `low`、`medium`、`high`、`xhigh`、`max`；`gemini-4-carbon` 可選 `low`、`medium`、`high`；`claude-haiku-4.5` 不可設定（不傳任何值）。預設 `low`。模型不接受的強度會被拒絕且不改動專案；換模型時若原本的強度新模型不接受，會改回預設並在確認訊息中說明。Claude 後端以 `--effort` 傳入（並忽略環境變數 `CLAUDE_CODE_EFFORT_LEVEL`），Copilot 後端以 `--reasoning-effort` 傳入；Copilot 拒絕強度時停下（Web 暫停、CLI 結束），不儲存結果。
  - 呼叫逾時：預設 180 秒，可設 30–3600 的整數秒。
  - 風險：較高的強度在較慢的模型（Gemini）上可能超過逾時，請調高逾時或降低強度；各模型可用的強度取自 CLI 本身的檢查（Claude Code 2.1.330、Copilot CLI 1.0.114），CLI 更新後可能改變。
- **只有 Copilot 時**：建立專案後先執行 `python3 translate.py model set -b copilot-cli`（或在 Web「翻譯」頁選一個 Copilot 模型），再提取術語或翻譯。
- 每次呼叫都以一句翻譯用的系統提示取代 CLI 內建的提示，並且不給模型任何工具；子程序在系統暫存目錄執行。
- **Copilot 後端的需求與風險**：需要支援 `--experimental` 與 `--plugin-dir` 的 Copilot CLI（以 1.0.114 驗證）；系統提示由 `src/llm/copilot_plugin/` 的擴充功能替換，這依賴未公開的實驗性介面。每次呼叫結束後都會檢查替換是否生效，無法確認時不儲存結果並停下（Web 暫停、CLI 結束），不會改用約 2000 token 的內建提示繼續。Copilot 更新後可能需要調整後端；使用量上限的訊息取自 Copilot 文件，登入錯誤的判斷是推測的。

## 使用方式

### Web 介面

```bash
python3 -m flask --app src.web.server:create_app run --port 3000
```

開啟 http://localhost:3000：

1. **首頁** — 專案列表；「新增專案」上傳 EPUB。同名專案若沒有在翻譯，會被取代（`project.json`、`source.epub` 重建，`log/failed/` 清除；`glossary.json` 保留），CLI 的 `init` 則拒絕已存在的專案
2. **總覽** — 各章節進度
3. **術語表** — 新增、編輯、批次刪除、自動提取；人物可填性別與備註
4. **翻譯** — 選擇後端與模型、推理強度、呼叫逾時、worker 數、書籍簡介；背景翻譯並即時顯示進度（SSE），可暫停、繼續，可重試失敗段落
5. **審閱** — 原文譯文對照編輯與核可、單段重新翻譯、只顯示有問題的段落
6. **搜尋** — 以關鍵字找段落並重新翻譯選取的段落
7. **匯出** — 選擇格式，產生並下載 EPUB

### CLI

範例（`projects/book` 是一個專案目錄）：

```bash
python3 translate.py init book.epub                 # 建立專案 projects/book/（複製 EPUB、分段）
python3 translate.py status                         # 進度
python3 translate.py brief set "奇幻小說，語氣輕鬆，給青少年讀者"   # 書籍簡介（空字串移除）
python3 translate.py model show                     # 目前的後端、模型、推理強度、逾時與可用選項
python3 translate.py model set -b copilot-cli       # 只設定後端（與模型 -m），不開始翻譯
python3 translate.py model set -e high -t 300       # 推理強度與呼叫逾時（秒）
python3 translate.py glossary extract               # 自動提取術語
python3 translate.py glossary add "Mira Vale" "米拉·維爾" -c person -g 女 -n "主角的姐姐"
python3 translate.py glossary show
python3 translate.py start                          # 翻譯待翻譯段落（Ctrl-C 中斷，再執行即繼續）
python3 translate.py start --retry-failed           # 連同 failed 的段落一起翻譯
python3 translate.py validate projects/book         # 檢查已存的譯文（唯讀）
python3 translate.py validate projects/book --mark-failed   # 未通過結構檢查的段落標為 failed（保留原譯文）
python3 translate.py export -f bilingual-horizontal # 輸出到 projects/book/output/
python3 translate.py review                         # 開啟審閱頁（預設 port 5000）
```

- 另一種 `start`：`python3 translate.py start -b copilot-cli -m claude-haiku-4.5 -w 4`（改用 Copilot、4 個 worker）。
- `init`、`validate`、`migrate` 的參數是路徑；其他指令用 `-p/--project <專案目錄>`，不指定時取 `projects/` 下找到的第一個專案，有多個專案時請明確指定。
- `glossary import/export <json>` 匯入、匯出術語表。
- 各指令的完整選項：`python3 translate.py <指令> --help`。

## 翻譯流程重點

- **分段**：章節切成區塊（段落、標題、清單、表格、程式碼等），區塊不會被拆開；一段約 300–500 個英文單字（單一區塊較大時例外），表格與程式碼各自一段。新專案的標題與其後第一個文字區塊一定合為一段（可能因此超過 500 字；後面接程式碼、表格或章末時標題才單獨成段），沒有註解行的程式碼原樣保留、不送模型；較早建立的專案沿用舊規則：標題自成一段。
- **保護與檢查**：送給模型的 HTML 屬性以編號取代（`alt`、`title`、`aria-label` 的值照樣給模型翻譯），回覆再還原；回覆要通過結構檢查（區塊與標籤與原文一致、程式碼只能改註解）。未通過會附上錯誤重問一次，必要時把段落拆成兩半，每次翻譯一段最多 4 次呼叫（每次呼叫的逾時見上方，預設 180 秒）。品質問題（長度比例、未翻譯、術語未套用）只警告，顯示在審閱頁。
- **翻譯失敗**：診斷寫在 `log/failed/`。待翻譯的段落變成 `failed`，可用 `start --retry-failed`（或翻譯頁的重試按鈕）重試；已翻譯或已審閱的段落重新翻譯失敗時，保留原譯文與狀態，只記下問題，要再用審閱頁或搜尋頁重新翻譯。已存的譯文若未通過檢查（例如遷移後），用 `validate --mark-failed` 標為 `failed` 再 `start --retry-failed`。
- **術語表**：文字段落的提示中附上術語（含人物的性別與備註）；術語超過 150 筆時只附上該段出現的。自動提取分批進行（逾時與推理強度同翻譯），同一術語有不同譯法時再呼叫統一；使用量上限或登入錯誤會中斷提取，已完成批次的術語仍會加入。Web 只加入術語表沒有的術語；CLI 的 `glossary extract` 也會以新結果取代既有術語的譯法與類別（保留性別與備註）。
- **書籍簡介**：類型、語氣、讀者對象，放在每個文字段落提示的最前面（上限 2000 字）。程式碼段落的提示不含簡介、術語與前後文。
- **暫停與錯誤**：Web 的暫停（按鈕或使用量上限）會中止進行中的呼叫，那些段落回到待翻譯。使用量上限時 Web 暫停後自動繼續；CLI 停止派出新段落後等待再繼續，已在進行中的呼叫會跑完。等待時間依後端給的值，兩個內建後端都不提供，所以是 300 秒。登入過期或後端無法啟動時，Web 暫停等使用者處理後按繼續，CLI 結束（exit 1）。連續 5 段發生非預期錯誤時整個翻譯停止。
- **存檔**：每有一個結果（Web 另外約每秒）檢查一次，距上次存檔滿 10 秒或累積 20 段就寫入 `project.json`；暫停、停止、結束或失敗時會嘗試寫入，寫不進去的段落會列出。這不是時間保證：例如 CLI 單 worker 在等一次很長的呼叫時，前一段的結果還在記憶體裡。
- **鎖**：`project.lock` 保護 `project.json`：CLI 翻譯、Web 翻譯、遷移、匯出、`validate --mark-failed` 與 Web 上的段落編輯、模型與簡介設定都要取得它，拿不到就拒絕（Web 回 409）。術語表（`glossary.json`）不受鎖保護：翻譯中仍可編輯，但進行中的翻譯在開始時就讀好了術語表，修改要到下一次開始翻譯才生效；兩處同時修改術語表時後寫的會覆蓋先寫的。

## 專案格式與遷移

新專案是 schema 2（段落記錄所屬區塊範圍與指紋）。舊專案（schema 1）可以翻譯，但匯出與 `validate` 前要先遷移一次：

```bash
python3 translate.py migrate projects/book --dry-run   # 只報告：先看合併說明
python3 translate.py migrate projects/book             # 全部章節對得上才改寫 project.json
```

- 需要原書：專案的 `source.epub`，沒有時用 `project.json` 記錄的路徑；遷移後會把原書複製成 `source.epub`。
- 遷移前的 `project.json` 保留為 `project.schema1.json`；這個檔案已存在、或專案被鎖住時拒絕執行。
- 幾個舊段落合併成一段時，只要其中一段是待翻譯、失敗或譯文為空，合併後的段落就不保留譯文（回到待翻譯；舊譯文仍在 `project.schema1.json`）。dry run 的報告會列出這些段落。

## 輸出格式

| 格式 | 說明 |
|---|---|
| `chinese-only` | 純繁體中文（Web 預設） |
| `bilingual-horizontal` | 左右對照（CLI 預設） |
| `bilingual-vertical` | 上下對照 |

匯出以原書為底，只改動有譯文的章節、OPF、NCX、導覽文件（雙語格式另加一個樣式表），其餘檔案的內容原樣複製（ZIP 本身重新寫過）。章節的區塊與記錄不符、或段落未通過檢查時以原文輸出，匯出報告會列出原因；`validate` 通過不代表匯出一定用到該譯文。

匯出讀的是專案目錄裡的 `source.epub`（建立專案或遷移時寫入），所以搬移或改名專案目錄後仍可匯出。讀到的檔案必須與建立專案或遷移時記錄的雜湊值相同，否則拒絕匯出。

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

突變測試：`scripts/verify/mutation.sh main`，需要一個 Python 3.10+ 的環境，安裝 `requirements.txt` 與 `requirements-dev.txt`；與 `main` 相比沒有可突變的變更行時以 exit 2 結束。`scripts/verify/` 其他腳本見 [CLAUDE.md](CLAUDE.md)。

## 疑難排解

- 要停下翻譯：Web 按「暫停」（等頁面顯示已暫停，結果會在約一秒內存檔），CLI 在執行的終端按 Ctrl-C（會停掉進行中的呼叫並存檔）。不要直接砍掉 Web 伺服器：還沒存檔的結果會遺失。
- 呼叫卡住時先暫停或 Ctrl-C；仍有殘留的 `claude -p`／`copilot -p` 程序時，用 `pgrep -fl 'claude -p'` 找出、確認是這個專案的那一個 PID 再 `kill <PID>`。不要用 `pkill -f`：會砍到其他工作階段的程序。程序列表的參數裡有書的內容，不要貼到公開的地方。
- 「專案被鎖住」（Web 409、CLI 訊息）：另一個程序（CLI 翻譯、Web 翻譯、匯出）正在使用 `project.lock`。
- 匯出或 `validate` 拒絕舊格式專案：先執行 `migrate`。
- 匯出顯示「無法讀取來源 EPUB」：專案目錄裡的 `source.epub` 不見了（被刪除或改名），`project.json` 記錄的原書路徑也讀不到；把原書放回專案目錄並命名為 `source.epub`。雜湊值不符表示那個檔案不是建立專案或遷移時記錄的那本書。
- Copilot 停在「無法確認 Copilot CLI 已套用翻譯用的系統提示」：外掛沒有載入或 CLI 改了介面；確認 `copilot --help` 有 `--experimental` 與 `--plugin-dir`。
- 呼叫逾時（段落以 failed 結束、紀錄寫「timed out」）：用 `model set -t` 或翻譯頁調高逾時，或降低推理強度。
- Copilot 停在「Copilot CLI 不接受此模型的推理強度設定」：改選這個模型可用的推理強度（`model show` 列出）或換模型。
- Web 翻譯日誌：`projects/<name>/log/translation_*.log`。

## 授權

GNU Affero General Public License v3.0（`AGPL-3.0-only`），全文見 [`LICENSE`](LICENSE)。修改後散布，或架成網路服務供他人使用，都必須依同一授權公開完整原始碼。

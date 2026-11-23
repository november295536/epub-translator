# EPUB 智能翻譯系統

把英文 EPUB 電子書翻譯成繁體中文。翻譯透過模型後端（Claude Code CLI、GitHub Copilot CLI，或以 Copilot CLI 為執行環境的 Copilot SDK）進行，支援術語表、書籍簡介、譯文結構檢查與失敗重試、三種輸出格式，以及 Web 審閱介面與 CLI 兩種操作方式。

## 系統需求

- **Python 3.12+**，macOS 或 Linux（用到 `fcntl` 與 POSIX 程序群組，原生 Windows 無法執行）
- 至少一個模型後端（見下方「模型後端」）：
  - **[Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code/overview)**（`claude`，預設後端），需登入
  - **GitHub Copilot CLI**（`copilot`），需登入（`copilot login`）；`copilot-cli` 與 `copilot-sdk` 兩個後端都用它（Copilot SDK 的 Python 套件 `microsoft-copilot-sdk` 已列在 `requirements.txt`）

## 安裝

```bash
git clone "$REPO_URL" epub-translator   # REPO_URL：本專案的 git 網址
cd epub-translator
python3.12 -m venv venv               # 或更新的版本，例如 python3.14
source venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt   # 執行測試與突變測試需要（ebooklib 產生範例 EPUB、mutmut）

python3 -m pytest tests/ -q           # 確認安裝
```

用比 3.12 舊的 Python 執行 `translate.py` 或 Web 介面時，會直接顯示「需要 Python 3.12 以上版本」並結束。

**重建既有的虛擬環境**（例如原本以 Python 3.9 建立）：虛擬環境綁定建立它的直譯器，無法就地升級，刪掉重建即可（`projects/` 不受影響）：

```bash
deactivate 2>/dev/null; rm -rf venv
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
python3 --version                     # 應為 3.12 以上
```

## 模型後端

| 後端 | 指令 | 模型 | 登入 |
|---|---|---|---|
| `claude-cli`（預設） | `claude -p` | `sonnet`（預設）、`opus`、`haiku` | `claude /login` |
| `copilot-cli` | `copilot -p` | Claude：`claude-sonnet-5.5`（預設）、`claude-sonnet-5`、`claude-opus-5.5`（15x）、`claude-haiku-5.5`、`claude-haiku-4.5`（0.33x）；GPT：`gpt-6.2-sol`、`gpt-6.2-astra`、`gpt-6.1-sol`、`gpt-6.1-terra`、`gpt-6-sol`、`gpt-6-luna`、`gpt-6-astra`、`gpt-5.6-sol`、`gpt-5.6-luna`；Gemini：`gemini-4-carbon`、`gemini-4-argon`、`gemini-4-hydrogen`、`gemini-3.8-flash`（14x）；Grok：`grok-4.7`、`grok-4.6`（未標示者 1x，為 Copilot 計算的 premium request 倍數） | `copilot login` |
| `copilot-sdk` | Copilot SDK（Python），執行環境為已安裝的 `copilot` | 與 `copilot-cli` 相同（同一份清單、同一個預設） | `copilot login`（同一個帳號） |

- 後端與模型存在專案裡，新專案預設 `claude-cli`。CLI 用 `model set -b/-m` 只儲存設定（`model show` 顯示目前的設定與可用模型），或用 `start --backend/-b`、`--model/-m` 設定後直接開始翻譯；之後的執行沿用。Web 在「翻譯」頁的模型選單切換（只儲存設定），翻譯中切換會從下一段開始生效。換成另一個後端且不指定模型時用該後端的預設模型（指定的是專案目前的後端時不改動模型）；模型必須屬於要使用的後端，否則拒絕且不改動專案。`glossary extract` 使用專案目前的後端。
- **推理強度與呼叫逾時**：也存在專案裡，與模型一樣設定（CLI `model set -e/--effort`、`-t/--timeout`，`model show` 顯示目前的值與模型可用的推理強度；Web「翻譯」頁模型選單旁的「推理強度」與「呼叫逾時（秒）」），翻譯中變更從下一段開始生效，翻譯與術語提取都使用。
  - 推理強度：Claude 後端的三個模型與 Copilot 的 Claude 5/5.5 系列、GPT 系列可選 `low`、`medium`、`high`、`xhigh`、`max`，其中 `gpt-6-sol`、`gpt-6-luna`、`gpt-5.6-sol`、`gpt-5.6-luna` 另有 `none`；Gemini 系列可選 `low`、`medium`、`high`；Grok 系列可選 `low`、`medium`、`high`、`xhigh`；`claude-haiku-4.5` 不可設定（不傳任何值）。`model show` 與翻譯頁列出目前模型可選的強度。預設 `low`。專案檔裡的強度是已知的強度（`none`、`low`、`medium`、`high`、`xhigh`、`max`）但不在目前模型的清單中（手動修改，或清單變短）時，呼叫與頁面都改用預設，檔案裡的值保留到換模型為止；不是已知強度的值（例如 `"bogus"`）會讓專案無法載入，請修正或刪除 `project.json` 的 `effort`。模型不接受的強度會被拒絕且不改動專案；換模型（或後端）時若原本的強度新模型不接受，會改回預設並在確認訊息中說明；只改逾時或強度不會清掉它。Claude 後端以 `--effort` 傳入（並忽略環境變數 `CLAUDE_CODE_EFFORT_LEVEL`），Copilot CLI 後端以 `--reasoning-effort` 傳入，Copilot SDK 後端以工作階段的 `reasoning_effort` 傳入；Copilot 拒絕強度時停下（Web 暫停、CLI 結束），不儲存結果。
  - 呼叫逾時：預設 180 秒，可設 30–3600 的整數秒。
  - 風險：較高的強度在較慢的模型（Gemini）上可能超過逾時，請調高逾時或降低強度；Copilot 的模型清單、倍數與各模型可用的強度取自目前的帳號與 CLI 本身的檢查（Claude Code 2.1.330、Copilot CLI 1.0.114），CLI 或 Copilot 更新後可能改變：Copilot 不再接受某個強度時翻譯會停下並顯示 CLI 的訊息；Claude Code 若不再接受清單中的某個強度，只會警告並以它自己的預設強度繼續，翻譯不會停下。
- **只有 Copilot 時**：建立專案後先執行 `python3 translate.py model set -b copilot-cli`（或 `-b copilot-sdk`；或在 Web「翻譯」頁選一個 Copilot 模型），再提取術語或翻譯。
- 每次呼叫都以一句翻譯用的系統提示取代 CLI 內建的提示，並且不給模型任何工具；子程序在系統暫存目錄執行。
- **Copilot 後端的需求與風險**：需要支援 `--experimental` 與 `--plugin-dir` 的 Copilot CLI（以 1.0.114 驗證）；系統提示由 `src/llm/copilot_plugin/` 的擴充功能替換，這依賴未公開的實驗性介面。每次呼叫結束後都會檢查替換是否生效，無法確認時不儲存結果並停下（Web 暫停、CLI 結束），不會改用約 2000 token 的內建提示繼續。Copilot 更新後可能需要調整後端；使用量上限的訊息取自 Copilot 文件，登入錯誤的判斷是推測的。Copilot CLI 會把每次呼叫（含提示與回覆）存成 `~/.copilot/session-state/` 下的一個工作階段，這個後端不會刪除它們；它也會把每次呼叫的提示與回覆寫進跨工作階段的搜尋資料庫 `~/.copilot/session-store.db`，Copilot CLI 1.0.114 沒有可以逐次呼叫關閉它的選項（見 `known-issues.md`）。翻譯真正的書時建議用 `copilot-sdk` 後端。
- **Copilot SDK 後端（`copilot-sdk`）**：透過 Copilot 的 Python SDK 呼叫，以已安裝的 `copilot` 程式作為執行環境（不會另外下載）。每個 Python 程序（一次 CLI 翻譯、一個 Web 伺服器）只啟動一個 `copilot --headless --stdio` 程序，所有 worker 共用，程序結束時一併停止；它意外結束時，下一次呼叫會再啟動一個。每次呼叫都是一個新的工作階段：系統提示換成翻譯用的那一句，不給工具，不啟動內建的 MCP 伺服器、技能與自訂指示，不寫入跨工作階段的搜尋資料庫（`~/.copilot/session-store.db`，預設會收錄提示與回覆），結束後刪除該工作階段（`~/.copilot/session-state/<id>/`，裡面有提示與回覆），逾時、暫停或程式結束時也是（程式結束前會等進行中的呼叫中止並刪除，最多約 15 秒，再等執行環境停止，最多約 20 秒，合計最多約 35 秒）。逾時或暫停時會中止（abort）進行中的那一輪：實測兩次在模型呼叫中途中止的呼叫，SDK 都沒有回報任何用量；只逾時而不中止的話，那一輪會繼續跑完並計費。回覆因長度上限被截斷時標為 truncated，被內容過濾時視為拒答。使用 Copilot CLI 已登入的帳號；沒有登入時停下並提示執行 `copilot login`。
  - 需求與風險：以 `microsoft-copilot-sdk` 2.0.1 與 Copilot CLI 1.0.114 驗證；SDK 的部分欄位（例如每次呼叫的費用倍數）標示為實驗性，更新後可能改變。不接受的模型與推理強度的訊息是實際觀察到的；使用量上限、登入過期與拒答的判斷沿用 Copilot CLI 後端的規則，未實際觀察。不要設定 `COPILOT_HOME`：換了目錄時執行環境會改用 GitHub CLI 的另一個帳號，且不會報錯。CLI 翻譯中按 Ctrl-C 時，執行環境也會收到中斷而先結束，當時進行中的呼叫的工作階段資料夾會留在 `~/.copilot/session-state/`；在執行 Web 伺服器的終端機按 Ctrl-C 結束伺服器時，若有翻譯正在進行也一樣，所以請先在 Web 上暫停，並在畫面顯示已暫停後再等約 15 秒（進行中的呼叫在背景中止並刪除工作階段）才結束伺服器（Web 的暫停、正常結束與因錯誤停止都不會留下）；程序被強制結束（kill）時也會留下，執行環境（`copilot --headless`）在呼叫進行中結束或被 kill 時，那些呼叫的資料夾也會留下。某個工作階段刪除失敗時會記一筆警告（只有工作階段 id，不含內容）：CLI 印在終端機，Web 在伺服器的輸出與專案的 `log/translation_*.log`。執行環境自己的日誌在 `~/.copilot/logs/`。兩個 Copilot 後端的工作階段資料夾與 `session-store.db` 都是本機上的檔案。

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
8. **統計** — 讀取專案的事件紀錄：依後端／模型／推理強度的呼叫次數、首次即通過、需重試或分割、最終失敗、耗時中位數與 p90、tokens，問題代碼依次數排列，以及每次執行（開始、結束、耗時、已翻譯／失敗、暫停）。翻譯進行中也可以看；按「重新整理」更新

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
python3 translate.py stats                          # 事件紀錄的統計（--json、--run ID、--since 2026-11-01）
```

- 另一種 `start`：`python3 translate.py start -b copilot-cli -m claude-haiku-4.5 -w 4`（改用 Copilot、4 個 worker）。
- `init`、`validate`、`migrate` 的參數是路徑；其他指令用 `-p/--project <專案目錄>`，不指定時取 `projects/` 下找到的第一個專案，有多個專案時請明確指定。
- `glossary import/export <json>` 匯入、匯出術語表。
- 各指令的完整選項：`python3 translate.py <指令> --help`。

## 翻譯流程重點

- **分段**：章節切成區塊（段落、標題、清單、表格、程式碼等），區塊不會被拆開；一段約 300–500 個英文單字（單一區塊較大時例外），表格與程式碼各自一段。新專案的標題與其後第一個文字區塊一定合為一段（可能因此超過 500 字；後面接程式碼、表格或章末時標題才單獨成段），沒有註解行的程式碼原樣保留、不送模型；較早建立的專案沿用舊規則：標題自成一段。
- **保護與檢查**：送給模型的 HTML 屬性以編號取代（`alt`、`title`、`aria-label` 的值照樣給模型翻譯），回覆再還原；回覆要通過結構檢查（區塊與標籤與原文一致、程式碼只能改註解）。未通過會附上錯誤重問一次，必要時把段落拆成兩半，每次翻譯一段最多 4 次呼叫（每次呼叫的逾時見上方，預設 180 秒）。品質問題（長度比例、未翻譯、術語未套用）只警告，顯示在審閱頁。
- **翻譯失敗**：診斷寫在 `log/failed/`。待翻譯的段落變成 `failed`，可用 `start --retry-failed`（或翻譯頁的重試按鈕）重試；已翻譯或已審閱的段落重新翻譯失敗時，保留原譯文與狀態，只記下問題，要再用審閱頁或搜尋頁重新翻譯。已存的譯文若未通過檢查（例如遷移後），用 `validate --mark-failed` 標為 `failed` 再 `start --retry-failed`。
- **術語表**：文字段落的提示中附上術語（含人物的性別與備註）。人名只在術語表有列出時照術語表的寫法（中文譯名，或譯名就填英文原文；術語表列全名而文中只寫姓或名時，用譯名中對應的部分，無法對應時保留英文），沒有列出的（知名人物也一樣）保留英文原文；稱謂、頭銜照常翻譯（Miss X → X 小姐）。術語表超過 150 筆時，只寫姓或名的段落不會帶到全名的條目（見 `known-issues.md`），常這樣出現的人物可把姓或名另外加成術語。術語超過 150 筆時只附上該段出現的。自動提取分批進行（逾時與推理強度同翻譯），同一術語有不同譯法時再呼叫統一；使用量上限或登入錯誤會中斷提取，已完成批次的術語仍會加入。Web 只加入術語表沒有的術語；CLI 的 `glossary extract` 也會以新結果取代既有術語的譯法與類別（保留性別與備註）。
- **書籍簡介**：類型、語氣、讀者對象，放在每個文字段落提示的最前面（上限 2000 字）。程式碼段落的提示不含簡介、術語與前後文。
- **暫停與錯誤**：Web 的暫停（按鈕或使用量上限）會中止進行中的呼叫，那些段落回到待翻譯。使用量上限時 Web 暫停後自動繼續；CLI 停止派出新段落後等待再繼續，已在進行中的呼叫會跑完。等待時間依後端給的值，內建的後端都不提供，所以是 300 秒。登入過期或後端無法啟動時，Web 暫停等使用者處理後按繼續，CLI 結束（exit 1）。連續 5 段發生非預期錯誤時整個翻譯停止。
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
├── log/                      # events.jsonl 事件紀錄；Web 翻譯日誌 translation_*.log；failed/ 為失敗段落的診斷
└── output/                   # 匯出的 EPUB
logs/                         # 與 projects/ 同一層，不進版控：程序紀錄 web.log、cli.log
```

### 紀錄

- **程序紀錄** `logs/web.log`（Web 伺服器）、`logs/cli.log`（每個 CLI 指令），放在專案資料夾（`projects/`）的上一層，除了終端機的輸出之外持續保留。每次啟動先寫一行版本（git commit）、Python 版本、指令與 PID，接著是所有 `src.*` 的紀錄、Web 的請求行，以及請求、CLI 指令或執行緒中未處理例外的完整 traceback；CLI 指令結束時寫 `end: exit N`。每個檔案滿 5 MB 輪替，保留 5 個舊檔。這個檔案可能含書名、術語等內容，只留在本機。
- **事件紀錄** `projects/<name>/log/events.jsonl`：每行一個 JSON 物件。每次模型呼叫一行（翻譯請求、附錯誤重問、拆半、單段重新翻譯、術語提取批次、術語統一），記錄時間、執行 ID、呼叫種類、段落 ID、後端、模型、推理強度、逾時、耗時、tokens（後端有回報時；`claude-cli` 不回報）、結果（accepted、rejected 與問題代碼、timeout、cancelled、rate_limit、auth、backend_unavailable、refusal、truncated、transient、error、unexpected）與該段的第幾次呼叫；另有執行的開始、暫停／繼續與原因、使用量上限的等待、結束與計數，以及每段的最終結果（translated、failed、dropped：翻譯期間被編輯）。**不含書的文字**（原文、譯文、提示、回覆、術語、錯誤訊息都不記，只記代碼、次數、字元數與耗時），所以這是可以交給開發者分析的檔案（段落 ID 的例外見 `known-issues.md`）。寫入失敗只記一則警告，不影響翻譯。
- `translate.py stats` 與 Web 的「統計」頁讀的就是事件紀錄（同一個函式計算）；無法解析的行（例如程序在寫入中途結束）會略過並列出數量。

## 測試

```bash
python3 -m pytest tests/ -q -p no:cacheprovider -rfw
```

突變測試：在專案的虛擬環境（已安裝 `requirements.txt` 與 `requirements-dev.txt`）執行 `scripts/verify/mutation.sh main`；與 `main` 相比沒有可突變的變更行時以 exit 2 結束。`scripts/verify/` 其他腳本見 [CLAUDE.md](CLAUDE.md)。

## 疑難排解

- 要停下翻譯：Web 按「暫停」（等頁面顯示已暫停，結果會在約一秒內存檔），CLI 在執行的終端按 Ctrl-C（會停掉進行中的呼叫並存檔）。不要直接砍掉 Web 伺服器：還沒存檔的結果會遺失。
- 呼叫卡住時先暫停或 Ctrl-C；仍有殘留的 `claude -p`／`copilot -p`（Copilot SDK 後端：`copilot --headless`）程序時，用 `pgrep -fl 'claude -p'` 找出、確認是這個專案的那一個 PID 再 `kill <PID>`（kill 掉 `copilot --headless` 時，當時進行中的呼叫的工作階段資料夾會留在 `~/.copilot/session-state/`）。不要用 `pkill -f`：會砍到其他工作階段的程序。程序列表的參數裡有書的內容，不要貼到公開的地方。
- 「專案被鎖住」（Web 409、CLI 訊息）：另一個程序（CLI 翻譯、Web 翻譯、匯出）正在使用 `project.lock`。
- 匯出或 `validate` 拒絕舊格式專案：先執行 `migrate`。
- 匯出顯示「無法讀取來源 EPUB」：專案目錄裡的 `source.epub` 不見了（被刪除或改名），`project.json` 記錄的原書路徑也讀不到；把原書放回專案目錄並命名為 `source.epub`。雜湊值不符表示那個檔案不是建立專案或遷移時記錄的那本書。
- Copilot 停在「無法確認 Copilot CLI 已套用翻譯用的系統提示」：外掛沒有載入或 CLI 改了介面；確認 `copilot --help` 有 `--experimental` 與 `--plugin-dir`。
- 呼叫逾時（紀錄寫「timed out」）：逾時的呼叫會在該段的呼叫次數內重試，重試成功時段落不會失敗、也不會寫失敗診斷檔（Web 翻譯日誌仍會留下一則警告）；用完次數時，待翻譯的段落以 failed 結束，已翻譯或已審閱的段落保留原譯文與狀態、只記下問題；術語提取的呼叫逾時則記錄警告、不重試：該批次不提出術語（裁決呼叫逾時則各術語保留最常見的譯法）。常逾時就用 `model set -t` 或翻譯頁調高逾時，或降低推理強度。
- Copilot 停在「Copilot CLI 不接受此模型的推理強度設定」：改選這個模型可用的推理強度（`model show` 列出）或換模型。
- Copilot SDK 停在「找不到 Copilot SDK」：在專案的虛擬環境執行 `pip install -r requirements.txt`；「找不到 copilot 程式」：安裝 GitHub Copilot CLI 並 `copilot login`；「無法啟動 Copilot SDK 的執行環境」：確認 `copilot --version` 能執行。
- Web 翻譯日誌：`projects/<name>/log/translation_*.log`；程序紀錄（含請求與錯誤的 traceback）：`logs/web.log`、`logs/cli.log`；要回顧某次翻譯發生了什麼，先看 `translate.py stats`（或 Web 的「統計」頁），再依時間對照程序紀錄。

## 授權

MIT License

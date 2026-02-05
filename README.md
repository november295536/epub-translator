# EPUB 智能翻譯系統

使用 Claude Code CLI 將英文 EPUB 電子書翻譯成繁體中文，支援術語管理、雙語對照輸出、Web 審閱介面。

## 系統需求

- **[Python 3.9+](https://www.python.org/downloads/)**
- **[Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code/overview)** — 翻譯引擎（需要 [Anthropic](https://console.anthropic.com/) 帳號，Claude Pro/Max 訂閱或 API credits）

## 從零開始設置

### 1. 安裝 Claude Code CLI

請參考官方文件安裝並完成登入認證：https://docs.anthropic.com/en/docs/claude-code/overview

### 2. 安裝 Python 依賴

```bash
git clone <repo-url> epub-translator
cd epub-translator

# 建議使用虛擬環境
python3 -m venv venv
source venv/bin/activate    # macOS / Linux

# 安裝依賴
pip install -r requirements.txt
```

### 3. 驗證安裝

```bash
# 確認 Claude CLI 可用
claude -p "Hello"

# 執行測試
python3 -m pytest tests/ -v
```

## 使用方式

### Web 介面（推薦）

```bash
python3 -m flask --app src.web.server:create_app run --port 5001
```

> macOS 的 port 5000 預設被 AirPlay 佔用，建議使用 5001 或其他 port。

開啟瀏覽器 http://localhost:5001，即可進行完整流程：

1. **首頁** — 查看所有專案，點「新增專案」上傳 EPUB
2. **總覽** — 各章節進度統計
3. **術語表** — 新增/刪除術語，或自動提取
4. **翻譯** — 啟動背景翻譯，即時 SSE 進度推送，可暫停/繼續
5. **審閱** — 原文譯文左右對照，即時編輯，一鍵通過
6. **匯出** — 選擇格式，產生並下載 EPUB

### CLI 模式

```bash
# 初始化專案（從 EPUB 提取章節並分段）
python3 translate.py init book.epub

# 查看翻譯進度
python3 translate.py status

# 術語管理
python3 translate.py glossary extract          # 用 Claude 自動提取術語
python3 translate.py glossary show             # 查看術語表
python3 translate.py glossary add "API" "應用程式介面" -c programming

# 開始翻譯（可中斷，再次執行自動從中斷處繼續）
python3 translate.py start

# 開啟 Web 審閱介面
python3 translate.py review

# 匯出 EPUB
python3 translate.py export --format bilingual-horizontal
```

## 輸出格式

| 格式                   | 說明             |
| ---------------------- | ---------------- |
| `chinese-only`         | 純繁體中文       |
| `bilingual-horizontal` | 左右對照（預設） |
| `bilingual-vertical`   | 上下對照         |

## 專案結構

```
epub-translator/
├── translate.py              # CLI 入口
├── requirements.txt
├── projects/                 # 翻譯專案資料（自動產生，不進版控）
│   └── <book-name>/
│       ├── project.json      # 專案狀態與所有段落
│       ├── glossary.json     # 術語表
│       └── output/           # 匯出的 EPUB
├── src/
│   ├── core/
│   │   ├── project.py        # 專案管理（JSON 持久化）
│   │   ├── segmenter.py      # 智能分段（300-500 字）
│   │   └── translator.py     # Claude CLI 翻譯引擎
│   ├── epub/
│   │   ├── extractor.py      # EPUB 解析
│   │   └── builder.py        # EPUB 重建輸出
│   ├── glossary/
│   │   ├── glossary_manager.py  # 術語表 CRUD
│   │   └── term_extractor.py    # 自動術語提取
│   └── web/
│       ├── server.py         # Flask app factory
│       ├── tasks.py          # 背景任務（翻譯、提取）
│       ├── blueprints/       # API 路由
│       ├── templates/        # HTML 頁面（Alpine.js）
│       └── static/           # CSS
└── tests/                    # pytest 測試
```

## 測試

```bash
python3 -m pytest tests/ -v
```

## 授權

GNU Affero General Public License v3.0（`AGPL-3.0-only`），全文見 [`LICENSE`](LICENSE)。修改後散布，或架成網路服務供他人使用，都必須依同一授權公開完整原始碼。

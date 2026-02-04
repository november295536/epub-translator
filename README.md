# EPUB 智能翻譯系統

使用 Claude Code 將英文 EPUB 電子書翻譯成繁體中文的智能工具，支援雙語對照輸出。

## 特色功能

- 🔄 **智能分段翻譯** - 自動將內容分成適當大小的段落，支援斷點續傳
- 📚 **雙語對照** - 可選擇並排或上下對照的雙語輸出格式
- 🎯 **術語一致性** - 自動管理專業術語，確保整本書翻譯一致
- 🖥️ **雙介面支援** - 提供 GUI 圖形介面和 CLI 命令行介面
- ✏️ **人工校對** - 完整的校對編輯功能，支援即時預覽
- 🔧 **格式保真** - 完整保留原始 EPUB 的格式、樣式和排版

## 系統需求

- Python 3.8 或更高版本
- Claude Code CLI 工具
- Git（用於版本控制）
- 10GB+ 可用硬碟空間

## 快速開始

### 安裝

```bash
# 複製專案
git clone https://github.com/yourusername/epub-translator.git
cd epub-translator

# 建立虛擬環境
python -m venv venv
source venv/bin/activate  # macOS/Linux
# 或
venv\Scripts\activate  # Windows

# 安裝依賴
pip install -r requirements.txt
```

### GUI 模式使用

```bash
# 啟動圖形介面
python gui_launcher.py
```

1. 點擊「開啟 EPUB」選擇要翻譯的檔案
2. 調整分段大小等設定（可選）
3. 點擊「開始翻譯」
4. 在校對介面中檢視和編輯翻譯結果
5. 選擇輸出格式並匯出

### CLI 模式使用

```bash
# 初始化新專案
python translate.py init mybook.epub

# 開始翻譯
python translate.py start

# 如果中斷，可以繼續翻譯
python translate.py resume

# 進入校對模式
python translate.py review

# 輸出最終檔案
python translate.py export --format bilingual-horizontal
```

## 輸出格式選項

- **純中文** - 只包含翻譯後的中文內容
- **雙語並排** - 左邊原文，右邊譯文
- **雙語上下** - 上方原文，下方譯文
- **切換模式** - 讀者可自行切換顯示模式

## 專案結構

```
epub-translator/
├── src/              # 核心程式碼
├── gui/              # 圖形介面
├── data/             # 資料檔案
├── docs/             # 文件
└── tests/            # 測試檔案
```

## 設定選項

編輯 `config.json` 來自訂設定：

```json
{
  "segment_size": 400,
  "auto_save_interval": 300,
  "claude_timeout": 30,
  "output_formats": ["bilingual-horizontal", "chinese-only"],
  "glossary_path": "./data/glossary/default.json"
}
```

## 進階功能

### 術語表管理

```bash
# 匯出術語表
python translate.py glossary export --output my_glossary.json

# 匯入術語表
python translate.py glossary import --input my_glossary.json

# 檢查一致性
python translate.py glossary check
```

### 批次處理

雖然主要設計為單書處理，但可以使用腳本批次處理：

```bash
# 使用提供的批次處理腳本
./batch_translate.sh book1.epub book2.epub book3.epub
```

## 常見問題

**Q: 翻譯速度很慢怎麼辦？**
A: 這是正常的，因為我們優先考慮品質。每個段落都會經過 Claude Code 仔細翻譯。

**Q: 可以自訂翻譯風格嗎？**
A: 可以在設定中調整翻譯提示詞，或在術語表中預設特定翻譯。

**Q: 支援其他語言嗎？**
A: 目前專注於英翻中，但架構支援擴展到其他語言。

## 授權

GNU Affero General Public License v3.0（`AGPL-3.0-only`），全文見 [`LICENSE`](LICENSE)。修改後散布，或架成網路服務供他人使用，都必須依同一授權公開完整原始碼。

## 貢獻

歡迎提交 Issue 和 Pull Request！

## 聯絡方式

如有問題或建議，請在 GitHub 上開 Issue。
# 系統架構文件

## 整體架構

```
┌─────────────────────────────────────────────────────────┐
│                     使用者介面層                          │
│  ┌─────────────┐                    ┌────────────────┐  │
│  │   GUI 介面   │                    │   CLI 介面     │  │
│  │  (PyQt5)    │                    │  (Click)       │  │
│  └─────────────┘                    └────────────────┘  │
└─────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────┐
│                      業務邏輯層                          │
│  ┌─────────────┐  ┌─────────────┐  ┌────────────────┐  │
│  │ 專案管理器   │  │  翻譯引擎   │  │   校對管理器   │  │
│  └─────────────┘  └─────────────┘  └────────────────┘  │
│  ┌─────────────┐  ┌─────────────┐  ┌────────────────┐  │
│  │ 分段處理器   │  │ 術語管理器   │  │   進度追蹤器   │  │
│  └─────────────┘  └─────────────┘  └────────────────┘  │
└─────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────┐
│                      資料存取層                          │
│  ┌─────────────┐  ┌─────────────┐  ┌────────────────┐  │
│  │ EPUB 解析器  │  │ 檔案系統    │  │   資料庫      │  │
│  │ (ebooklib)  │  │   存取      │  │  (SQLite)     │  │
│  └─────────────┘  └─────────────┘  └────────────────┘  │
└─────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────┐
│                      外部整合層                          │
│  ┌─────────────┐  ┌─────────────┐  ┌────────────────┐  │
│  │ Claude Code  │  │    Git      │  │   Web 搜尋    │  │
│  │    CLI      │  │  版本控制    │  │  (術語查詢)   │  │
│  └─────────────┘  └─────────────┘  └────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

## 模組設計

### 1. 核心模組 (src/core/)

#### ProjectManager
```python
class ProjectManager:
    def create_project(epub_path: str) -> Project
    def load_project(project_id: str) -> Project
    def save_project(project: Project) -> None
    def list_projects() -> List[Project]
```

#### TranslationEngine
```python
class TranslationEngine:
    def translate_segment(text: str, glossary: Dict) -> str
    def batch_translate(segments: List[str]) -> List[str]
    def validate_translation(original: str, translated: str) -> bool
```

#### SegmentProcessor
```python
class SegmentProcessor:
    def split_content(content: str, size: int) -> List[Segment]
    def merge_segments(segments: List[Segment]) -> str
    def detect_special_content(segment: str) -> ContentType
```

### 2. EPUB 處理模組 (src/epub/)

#### EPUBExtractor
```python
class EPUBExtractor:
    def extract(epub_path: str) -> EPUBContent
    def get_chapters() -> List[Chapter]
    def extract_images() -> List[Image]
    def extract_styles() -> str
```

#### EPUBBuilder
```python
class EPUBBuilder:
    def create_bilingual_epub(content: BilingualContent) -> None
    def apply_styles(epub: EPUB, styles: str) -> None
    def add_navigation(epub: EPUB, toc: TOC) -> None
```

### 3. 術語管理模組 (src/glossary/)

#### GlossaryManager
```python
class GlossaryManager:
    def extract_terms(text: str) -> List[Term]
    def search_taiwan_term(term: str) -> str
    def update_glossary(term: str, translation: str) -> None
    def get_glossary() -> Dict[str, str]
    def check_consistency(text: str) -> List[Issue]
```

### 4. 介面模組 (src/ui/)

#### GUI 元件
```python
class MainWindow(QMainWindow):
    def __init__(self)
    def load_epub(self)
    def start_translation(self)
    def show_progress(self)

class ReviewEditor(QWidget):
    def display_segment(self, segment: Segment)
    def save_changes(self)
    def switch_view_mode(self)
```

#### CLI 指令
```python
@click.group()
def cli():
    pass

@cli.command()
def init(epub_path: str):
    """初始化翻譯專案"""

@cli.command()
def translate(project_id: str):
    """執行翻譯"""
```

## 資料流程

### 翻譯流程
```
EPUB 檔案
    ↓
[解壓與解析]
    ↓
HTML 內容 → [分段處理] → 文字段落
    ↓                      ↓
[術語擷取] ←─────────────┘
    ↓
[Claude Code 翻譯]
    ↓
[格式還原]
    ↓
[人工校對]
    ↓
[EPUB 重建]
    ↓
輸出檔案
```

### 資料結構

#### Project
```json
{
  "id": "uuid",
  "name": "書名",
  "source_path": "/path/to/original.epub",
  "created_at": "2025-01-19T10:00:00",
  "settings": {
    "segment_size": 400,
    "target_language": "zh-TW",
    "output_format": "bilingual_horizontal"
  },
  "progress": {
    "total_segments": 500,
    "completed_segments": 250,
    "current_segment": 251
  }
}
```

#### Segment
```json
{
  "id": "seg_001",
  "chapter_id": "ch_01",
  "position": 1,
  "original": "Original text...",
  "translated": "翻譯文字...",
  "status": "completed",
  "type": "paragraph",
  "metadata": {
    "has_code": false,
    "has_formula": false,
    "word_count": 387
  }
}
```

#### Glossary Entry
```json
{
  "term": "machine learning",
  "translation": "機器學習",
  "category": "AI",
  "frequency": 45,
  "contexts": ["ch01_p03", "ch02_p15"],
  "last_updated": "2025-01-19T10:00:00"
}
```

## Claude Code 整合

### 呼叫介面
```python
class ClaudeInterface:
    def __init__(self):
        self.timeout = 30
        self.max_retries = 3
    
    def translate(self, text: str, context: dict) -> str:
        prompt = self._build_prompt(text, context)
        result = self._call_claude(prompt)
        return self._parse_result(result)
    
    def _call_claude(self, prompt: str) -> str:
        cmd = f'echo "{prompt}" | claude -p "翻譯指令"'
        return subprocess.run(cmd, shell=True, capture_output=True)
```

### 提示詞模板
```python
TRANSLATION_PROMPT = """
請將以下{content_type}內容翻譯成繁體中文。

術語表：
{glossary}

要求：
1. 使用台灣的慣用語和術語
2. 保留原文術語在括號內
3. 符合中文閱讀習慣
4. {special_requirements}

內容：
{text}
"""
```

## 錯誤處理

### 錯誤類型
1. EPUB 解析錯誤
2. Claude Code 呼叫失敗
3. 檔案系統錯誤
4. 資料庫錯誤
5. 網路連線錯誤

### 處理策略
```python
class ErrorHandler:
    def handle_translation_error(self, error: Exception, segment: Segment):
        # 記錄錯誤
        self.log_error(error, segment)
        # 標記段落狀態
        segment.status = "error"
        # 嘗試恢復
        if self.can_retry(error):
            return self.retry_translation(segment)
        # 通知使用者
        self.notify_user(error)
```

## 效能優化

### 快取機制
- 翻譯結果快取
- 術語查詢快取
- EPUB 解析快取

### 並行處理
- 多段落預載入
- 背景術語查詢
- 異步檔案操作

### 資源管理
- 記憶體使用監控
- 臨時檔案清理
- 連線池管理
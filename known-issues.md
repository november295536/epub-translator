# Known Issues

## 匯出進行中重新建立或刪除同名專案

**影響**：在同一個 Web 伺服器裡，某個專案正在匯出時，以相同名稱建立專案（取代舊專案）或刪除該專案不會被拒絕。`project.lock` 由同一個程序的各執行緒共用，而建立與刪除只檢查翻譯工作、不檢查匯出，所以匯出可能讀到已被取代的 `source.epub` 或 `project.json`。跨程序（CLI 匯出與 Web，或兩個伺服器）時檔案鎖會擋下；`main` 也有同樣的空窗。

**暫時做法**：等匯出完成再重新建立或刪除專案；若在匯出期間做了這些操作，丟棄那次匯出的結果並重新匯出。

## 較新的 Python 修補版本上，遷移舊格式專案可能拒絕含 `<iframe>`、`<textarea>` 等元素的章節

**影響**：`translate.py migrate` 用 Beautiful Soup 的 `html.parser` 取出舊段落的文字，再與 lxml 讀到的章節文字比對。較新的 Python 修補版本把某些元素的內容當成原始文字：子標記與註解變成字面文字（`<iframe>A <b>B</b></iframe>` 得到 `A <b>B</b>`，lxml 得到 `A B`），`<iframe>`、`<noembed>`、`<noframes>`、`<xmp>`、`<plaintext>` 裡的字元參照也不解開（`A &amp; B`），於是這一章「text differs」，遷移不寫入任何資料。依 CPython 原始碼（`Lib/html/parser.py`），`<title>`、`<textarea>` 從 3.12.12、3.13.6、3.14.0 起這樣處理，`<iframe>`、`<noembed>`、`<noframes>`、`<xmp>`、`<plaintext>` 從 3.12.13、3.13.10、3.14.1 起；實測遷移：3.12.7 全部通過，3.14.7 全部被拒；3.13.7 只比對了解析結果，`<title>`／`<textarea>` 的子標記與 lxml 不一致。

**暫時做法**：遷移被拒、而章節裡有上述元素時，改用 3.12.11 以前或 3.13.5 以前的修補版本建立的虛擬環境執行 `migrate`（沒有不受影響的 3.14 版本）；只有遷移需要，遷移後的專案不再經過 `html.parser`。

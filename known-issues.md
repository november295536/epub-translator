# Known Issues

## 匯出進行中重新建立或刪除同名專案

**影響**：在同一個 Web 伺服器裡，某個專案正在匯出時，以相同名稱建立專案（取代舊專案）或刪除該專案不會被拒絕。`project.lock` 由同一個程序的各執行緒共用，而建立與刪除只檢查翻譯工作、不檢查匯出，所以匯出可能讀到已被取代的 `source.epub` 或 `project.json`。跨程序（CLI 匯出與 Web，或兩個伺服器）時檔案鎖會擋下；`main` 也有同樣的空窗。

**暫時做法**：等匯出完成再重新建立或刪除專案；若在匯出期間做了這些操作，丟棄那次匯出的結果並重新匯出。

## 較新的 Python 修補版本上，遷移舊格式專案可能拒絕含 `<iframe>` 等元素的章節

**影響**：`translate.py migrate` 用 Beautiful Soup 的 `html.parser` 取出舊段落的文字，再與 lxml 讀到的章節文字比對。較新的 Python 修補版本把 `<iframe>`、`<noembed>`、`<noframes>`、`<xmp>` 的內容當成原始文字，不解開字元參照：`<iframe src="media.xhtml">A &amp; B</iframe>` 得到 `A &amp; B`，lxml 得到 `A & B`，於是這一章「text differs」，遷移不寫入任何資料。實測：Python 3.14.7 拒絕，3.12.7 與 3.13.7 可以遷移。依 CPython 原始碼（`Lib/html/parser.py`），3.12.13 起、3.13.10 起、3.14.1 起都是新的行為，所以這不限於 3.14。

**暫時做法**：遷移被拒、而章節裡有上述元素時，改用這個改變之前的修補版本（3.12.12 以前、3.13.9 以前）的虛擬環境執行 `migrate`；只有遷移需要，遷移後的專案不再經過 `html.parser`。

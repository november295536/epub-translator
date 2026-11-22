# Known Issues

## 匯出進行中重新建立或刪除同名專案

**影響**：在同一個 Web 伺服器裡，某個專案正在匯出時，以相同名稱建立專案（取代舊專案）或刪除該專案不會被拒絕。`project.lock` 由同一個程序的各執行緒共用，而建立與刪除只檢查翻譯工作、不檢查匯出，所以匯出可能讀到已被取代的 `source.epub` 或 `project.json`。跨程序（CLI 匯出與 Web，或兩個伺服器）時檔案鎖會擋下；`main` 也有同樣的空窗。

**暫時做法**：等匯出完成再重新建立或刪除專案；若在匯出期間做了這些操作，丟棄那次匯出的結果並重新匯出。

## 較新的 Python 修補版本上，遷移舊格式專案可能拒絕含 `<iframe>`、`<textarea>` 等元素的章節

**影響**：`translate.py migrate` 用 Beautiful Soup 的 `html.parser` 取出舊段落的文字，再與 lxml 讀到的章節文字比對。較新的 Python 修補版本把某些元素的內容當成原始文字：子標記與註解變成字面文字（`<iframe>A <b>B</b></iframe>` 得到 `A <b>B</b>`，lxml 得到 `A B`），`<iframe>`、`<noembed>`、`<noframes>`、`<xmp>`、`<plaintext>` 裡的字元參照也不解開（`A &amp; B`），於是這一章「text differs」，遷移不寫入任何資料。依 CPython 原始碼（`Lib/html/parser.py`），`<title>`、`<textarea>` 從 3.12.12、3.13.6、3.14.0 起這樣處理，`<iframe>`、`<noembed>`、`<noframes>`、`<xmp>`、`<plaintext>` 從 3.12.13、3.13.10、3.14.1 起；實測遷移：3.12.7 全部通過，3.14.7 全部被拒；3.13.7 只比對了解析結果，`<title>`／`<textarea>` 的子標記與 lxml 不一致。

**暫時做法**：遷移被拒、而章節裡有上述元素時，改用 3.12.11 以前或 3.13.5 以前的修補版本建立的虛擬環境執行 `migrate`（沒有不受影響的 3.14 版本）；只有遷移需要，遷移後的專案不再經過 `html.parser`。

## `copilot-cli` 後端把每次呼叫寫進 Copilot 的跨工作階段資料庫

**影響**：用 `copilot-cli` 後端時，Copilot CLI 會把每次呼叫的提示與回覆（也就是書的原文與譯文）寫進 `~/.copilot/session-store.db`，供 Copilot 搜尋與列出過去的工作階段；工作階段資料夾 `~/.copilot/session-state/<id>/` 也留著。Copilot CLI 1.0.114 沒有能逐次呼叫關閉它的旗標、環境變數或設定：`copilot --help`、`copilot help config`、`copilot help environment` 都沒有；執行環境有 `enableSessionStore` 這個工作階段選項，但只有 SDK 建立工作階段時能傳，`-p` 不行，擴充功能在加入工作階段時傳 `state: { enableCrossSessionStore: false }` 也無效（實測一次呼叫，資料庫仍多了一筆）。這兩個位置都是本機上的檔案，不會因此上傳。

**暫時做法**：翻譯真正的書時用 `copilot-sdk` 後端，它不寫入這個資料庫，且每次呼叫後刪除工作階段資料夾。已經用 `copilot-cli` 翻譯過的，可在 Copilot CLI 裡用 `/session delete` 或 `/session prune` 刪除那些工作階段（是否一併移除資料庫中的紀錄未驗證）。

## 術語表超過 150 筆時，只用姓或名稱呼的人物不會帶到該段的提示

**影響**：術語表不超過 150 筆時，每段提示都附上整份術語表；超過時只附上該段原文中出現的術語，而「出現」是指整個術語（`term_pattern`：從詞首開始、全部字詞都在）。人物通常以全名列入術語表（`Sabel Tarrowmere`），文中卻常只寫姓或名（`Miss Tarrowmere`、`Sabel's`）；這樣的段落找不到該條目，提示裡就沒有這個人，依規則 5 名字保留英文原文，於是同一人在別段是中文、在這段是英文。品質檢查的「術語未套用」也一樣只看整個術語，不會提醒。

**暫時做法**：常只用姓或名出現的人物，另外把姓或名也加成術語（例如 `Tarrowmere → 塔洛米爾`、`Sabel → 薩貝爾`，類別 person）；或在審閱頁搜尋該姓名，修改仍是英文的段落。

# Known Issues

## Sentence-split 跨 segment 時 block_count 錯誤

**狀態**：部分修復
**影響**：segment 與源 HTML 元素錯位，導致 EPUB 建構時原文/譯文對不上

**已修復**：含 HTML tag 的 block（如 content div `<div class="lsl">...</div>`）不再做 sentence-split，避免破壞 tag 結構。這些 block 會作為原子單位處理。

**未修復**：純文字 NavigableString 的 sentence-split 跨 segment 問題仍存在（實務上罕見），需要同時滿足以下條件：

1. pending buffer 累積了 < 300 字（未達 `MIN_WORDS`，無法獨立 flush）
2. 下一個 block 很大，加上去會超過 500 字（`MAX_WORDS`）
3. 觸發 sentence-split 分支，將該 block 的句子逐一加入 pending

# 处理校验

- 文件数：**92**
- 总页数：**712**
- 原文字层保留：**60**
- 离线 OCR：**32**（Tesseract 5.5 eng + RapidOCR 中文）
- 全部文件均可用 PyMuPDF/PDF.js 提取文字；最少 788 字符，最多 47,663 字符。
- 格式：**80 份 2016+ 现代题型**；**12 份 2014–2015 旧题型**（文字层完整，但 AK-CET4 当前按现代编号解析，导入旧卷时需人工核对）。
- 2023.06 的 3 份中文命名 PDF 已重新 OCR，修复了段落 I 缺失和 `22.6% farmers` 被识别成 `22.6armers` 的问题。

校验命令（在本地处理环境）：

```text
python - <<'PY'
import json, os, fitz
repo = r"F:\AI\ak-cet4\cet4-papers"
m = json.load(open(os.path.join(repo, "manifest.json"), encoding="utf-8"))
for x in m:
    d = fitz.open(os.path.join(repo, x["output"]))
    assert len(d) == x["output_pages"]
    text = "".join(p.get_text() for p in d)
    assert len(text.strip()) >= 50, x["output"]
print("OK", len(m))
PY
```

已知限制：OCR 是离线自动识别，个别字符/标点可能有误；旧版 2014–2015 题型编号与 2016+ 不同。`manifest.json` 中保留了每份文件的 `mode`、`format`、`output_chars`、`output_pages` 便于复核。
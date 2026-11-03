# -*- coding: utf-8 -*-
"""统一文档入口：extract_documents(path) -> [{page, text, source_type}]"""
from __future__ import annotations

import html as html_mod
import re
from pathlib import Path

import numpy as np

TEXT_EXT = {".txt", ".md", ".markdown"}
HTML_EXT = {".html", ".htm"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}


def _strip_html(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>", "\n", raw)
    raw = re.sub(r"(?i)</p>", "\n", raw)
    raw = re.sub(r"<[^>]+>", "", raw)
    return html_mod.unescape(raw)


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "gb18030", "utf-16"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _page_image(page, dpi: int = 200):
    pix = page.get_pixmap(dpi=dpi)
    arr = np.frombuffer(pix.samples, dtype=np.uint8)
    arr = arr.reshape(pix.height, pix.width, pix.n)
    if pix.n == 4:
        arr = arr[:, :, :3]
    return arr


def ocr_page(page) -> tuple[str, list[dict]]:
    """扫描页 OCR：返回整页文字和行框（坐标按 200dpi 渲染）。"""
    from .ocr import join_lines, ocr_lines, normalize_ocr

    lines = ocr_lines(_page_image(page))
    for ln in lines:
        ln["text"] = normalize_ocr(ln["text"])
    text, _ = join_lines(lines)
    return text, lines


def extract_documents(path: Path) -> list[dict]:
    suffix = path.suffix.lower()

    if suffix == ".docx":
        from docx import Document
        doc = Document(str(path))
        return [{"page": i + 1, "text": p.text, "source_type": "docx"}
                for i, p in enumerate(doc.paragraphs)]

    if suffix == ".pdf":
        import pymupdf as fitz
        out: list[dict] = []
        with fitz.open(str(path)) as doc:
            for i, page in enumerate(doc):
                text = page.get_text("text")
                if len(text.strip()) < 20:          # 没有文本层 → 扫描件
                    text, _lines = ocr_page(page)
                    out.append({"page": i + 1, "text": text, "source_type": "pdf-scan"})
                else:
                    out.append({"page": i + 1, "text": text, "source_type": "pdf-text"})
        return out

    if suffix in TEXT_EXT:
        return [{"page": 1, "text": _decode(path.read_bytes()), "source_type": "text"}]

    if suffix in HTML_EXT:
        return [{"page": 1, "text": _strip_html(_decode(path.read_bytes())),
                 "source_type": "html"}]

    if suffix in IMAGE_EXT:
        from .ocr import join_lines, ocr_lines, normalize_ocr
        lines = ocr_lines(str(path))
        for ln in lines:
            ln["text"] = normalize_ocr(ln["text"])
        text, _ = join_lines(lines)
        return [{"page": 1, "text": text, "source_type": "image-ocr"}]

    if suffix == ".doc":
        raise RuntimeError("旧版 .doc 需要先转成 .docx：用 Word 打开另存为 .docx，"
                           "或安装 LibreOffice 后 soffice --convert-to docx")

    raise RuntimeError(f"不支持的文件类型：{suffix}")

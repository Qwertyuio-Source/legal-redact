# -*- coding: utf-8 -*-
"""PDF 通道：文本型走文字层，扫描型走 OCR；输出可读版 docx + 涂抹版 PDF。"""
from __future__ import annotations

from pathlib import Path

import pymupdf as fitz
from docx import Document
from docx.oxml.ns import qn
from docx.shared import Pt

from . import detectors
from . import types as T
from .engine import Issue, Redactor
from .extract import ocr_page

DPI = 200
SCALE = 72.0 / DPI


def _write_docx(pages: list[str], out_path: Path) -> None:
    d = Document()
    style = d.styles["Normal"]
    style.font.name = "宋体"
    style.font.size = Pt(12)
    style.element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")
    for i, text in enumerate(pages):
        if i:
            d.add_paragraph("")
        for line in text.splitlines():
            d.add_paragraph(line)
    d.save(str(out_path))


def _blackout_text_page(page, br) -> int:
    """有文字层：按原文检索、涂黑并真删。"""
    hits = 0
    seen: set[str] = set()
    for start, end, _rep, _kind, _eid in br.spans:
        original = br.text[start:end]
        if not original or original in seen:
            continue
        seen.add(original)
        rects = page.search_for(original) or []
        if not rects:
            for piece in original.splitlines():
                piece = piece.strip()
                if len(piece) >= 2:
                    rects.extend(page.search_for(piece))
        for rect in rects:
            page.add_redact_annot(rect, fill=(0, 0, 0))
            hits += 1
    return hits


def _blackout_scan_page(page, br, line_spans) -> int:
    """扫描页：没有文字层，按 OCR 行框 + 字符比例定位，只涂实体那几个字。"""
    hits = 0
    for start, end, _rep, _kind, _eid in br.spans:
        for lstart, lend, line in line_spans:
            if not (start < lend and lstart < end):
                continue
            text = line["text"]
            n = max(len(text), 1)
            i0 = max(start, lstart) - lstart
            i1 = min(end, lend) - lstart
            xs = [p[0] for p in line["box"]]
            ys = [p[1] for p in line["box"]]
            left, right = min(xs), max(xs)
            top, bottom = min(ys), max(ys)
            width = right - left
            x0 = left + width * (i0 / n)
            x1 = left + width * (i1 / n)
            if x1 - x0 < 1:
                continue
            rect = fitz.Rect(x0 * SCALE, top * SCALE, x1 * SCALE, bottom * SCALE)
            page.draw_rect(rect, color=(0, 0, 0), fill=(0, 0, 0))
            hits += 1
    return hits


def process_pdf(path: Path, out_dir: Path, redactor: Redactor, name: str) -> dict:
    doc = fitz.open(str(path))
    texts: list[str] = []
    metas: list[dict] = []
    for page in doc:
        raw = page.get_text("text")
        if len(raw.strip()) < 20:                     # 扫描件：没有文本层
            text, lines = ocr_page(page)
            texts.append(text)
            metas.append({"scan": True, "lines": lines})
        else:
            texts.append(raw)
            metas.append({"scan": False})

    results = redactor.prepare(texts)

    docx_path = out_dir / f"{name}.脱敏.docx"
    _write_docx([br.redacted for br in results], docx_path)

    hits = 0
    for page, meta, br in zip(doc, metas, results):
        if meta["scan"]:
            from .ocr import join_lines
            _text, line_spans = join_lines(meta["lines"])
            hits += _blackout_scan_page(page, br, line_spans)
        else:
            hits += _blackout_text_page(page, br)
        page.apply_redactions()
    pdf_path = out_dir / f"{name}.涂抹版.pdf"
    doc.save(str(pdf_path))
    doc.close()

    # 涂抹后再扫一遍，防漏
    leftovers = [i for i in redactor.issues if i.kind == "leftover"]
    with fitz.open(str(pdf_path)) as check:
        for pi, page in enumerate(check):
            text = page.get_text("text")
            for sp in detectors.detect(text):
                if sp.text in redactor.renderer.replacements or "某" in sp.text or "×" in sp.text:
                    continue
                if sp.kind in (T.ID_CARD, T.PHONE, T.EMAIL, T.LANDLINE, T.USCC, T.BANK_ACCOUNT):
                    continue
                leftovers.append(Issue("leftover", sp.text,
                                       f"涂抹后 PDF 第 {pi + 1} 页仍有残留（{sp.rule}）", pi + 1))

    lines = ["原文  →  脱敏后", "=" * 50]
    for br in results:
        if br.spans:
            lines.append("原文：" + " ".join(br.text.split())[:200])
            lines.append("脱敏：" + " ".join(br.redacted.split())[:200])
            lines.append("")
    (out_dir / f"{name}.对照.txt").write_text("\n".join(lines), encoding="utf-8")

    return {
        "file": path, "out": docx_path, "out2": pdf_path, "mapping": None,
        "rows": redactor.rows, "leftovers": leftovers,
        "candidates": [i for i in redactor.issues if i.kind == "candidate"],
        "changed": sum(1 for br in results if br.spans),
        "pages": len(texts), "blank_hits": hits, "redactor": redactor,
    }

# -*- coding: utf-8 -*-
"""文档读写：docx 保留原格式替换，txt/md 直读直写。"""
from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.text.paragraph import Paragraph


# ---------- 读取 ----------

def _iter_table_paras(table, seen: set[int]):
    for row in table.rows:
        for cell in row.cells:
            if id(cell._tc) in seen:
                continue
            seen.add(id(cell._tc))
            for p in cell.paragraphs:
                yield p
            for t in cell.tables:
                yield from _iter_table_paras(t, seen)


def iter_paragraphs(doc) -> list[Paragraph]:
    seen: set[int] = set()
    paras: list[Paragraph] = list(doc.paragraphs)
    for table in doc.tables:
        paras.extend(_iter_table_paras(table, seen))
    for section in doc.sections:
        for part in (section.header, section.footer,
                     section.first_page_header, section.first_page_footer,
                     section.even_page_header, section.even_page_footer):
            try:
                paras.extend(part.paragraphs)
            except Exception:
                continue
    return paras


def read_text_file(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gb18030", "utf-16"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


# ---------- 写回 ----------

def rewrite_paragraph(p: Paragraph, spans: list[tuple[int, int, str, str]]) -> None:
    """按 span 改段落文字，尽量保住每个 run 的格式。"""
    runs = list(p.runs)
    if not runs or not spans:
        return

    concat = "".join(r.text for r in runs)
    precise = concat == p.text
    if not precise:
        # 超链接/域导致位置对不上：整段塞进第一个 run，文字正确、格式会统一
        text = p.text
        for sp in sorted(spans, key=lambda s: -s[0]):
            start, end, rep = sp[0], sp[1], sp[2]
            text = text[:start] + rep + text[end:]
        runs[0].text = text
        for r in runs[1:]:
            r.text = ""
        return

    chars: list[tuple[int, str]] = []
    for ri, r in enumerate(runs):
        for ch in r.text:
            chars.append((ri, ch))

    out: list[tuple[int, str]] = []
    pos = 0
    for sp in sorted(spans, key=lambda s: s[0]):
        start, end, rep = sp[0], sp[1], sp[2]
        out.extend(chars[pos:start])
        ri = chars[start][0] if start < len(chars) else 0
        out.append((ri, rep))
        pos = end
    out.extend(chars[pos:])

    buf: list[list[str]] = [[] for _ in runs]
    for ri, seg in out:
        buf[ri].append(seg)
    for ri, r in enumerate(runs):
        r.text = "".join(buf[ri])


def short_preview(text: str, limit: int = 90) -> str:
    t = " ".join(text.split())
    return t if len(t) <= limit else t[:limit] + "…"

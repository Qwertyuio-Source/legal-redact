# -*- coding: utf-8 -*-
"""docx 的「附加部件」脱敏：批注 / 脚注 / 尾注 / 页眉 / 页脚。

python-docx 只建模正文段落，这些部件里的文字它不改；但保存时会原样保留，
所以正文处理完，再把整包当 zip 过一遍，直接改这些部件里的 <w:t> 文本。
同一个 Redactor 实例 → 同一实体在全文档是同一个替身。
"""
from __future__ import annotations

import html
import re
import shutil
import zipfile
from pathlib import Path

from .engine import Redactor

# 需要处理的部件（header1.xml / footer2.xml 这种带编号的也算）
EXTRA_PART_RE = re.compile(
    r"^word/(?:comments|commentsExtended|footnotes|endnotes|header\d*|footer\d*)\.xml$"
)

# <w:t ...>文本</w:t>；xml:space="preserve" 的空格要保留
W_T_RE = re.compile(r"(<w:t(?:\s[^>]*)?>)(.*?)(</w:t>)", re.S)


def _unescape(s: str) -> str:
    return html.unescape(s)


def _escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def list_extra_parts(path) -> list[str]:
    """列出这个 docx 里包含文字的可改部件。"""
    try:
        with zipfile.ZipFile(path) as z:
            return [n for n in z.namelist() if EXTRA_PART_RE.match(n)]
    except zipfile.BadZipFile:
        return []


def collect_extra_texts(path) -> list[str]:
    """按部件顺序取出所有附加部件的文字，供一次性 prepare。"""
    out: list[str] = []
    try:
        with zipfile.ZipFile(path) as z:
            for name in sorted(n for n in z.namelist() if EXTRA_PART_RE.match(n)):
                xml = z.read(name).decode("utf-8", "replace")
                out.extend(_unescape(m.group(2)) for m in W_T_RE.finditer(xml))
    except zipfile.BadZipFile:
        pass
    return out


def apply_extra_texts(src, dst, redacted_texts: list[str]) -> dict:
    """用已经算好的脱敏结果回写附加部件（顺序必须和 collect_extra_texts 一致）。"""
    src, dst = Path(src), Path(dst)
    with zipfile.ZipFile(src) as z:
        extra = sorted(n for n in z.namelist() if EXTRA_PART_RE.match(n))
        if not extra:
            shutil.copyfile(src, dst)
            return {"parts": [], "changed": 0, "texts": []}

        new_xml: dict[str, str] = {}
        detail = []
        idx = 0
        for name in extra:
            xml = z.read(name).decode("utf-8", "replace")
            matches = list(W_T_RE.finditer(xml))
            out = []
            pos = 0
            for m in matches:
                after = redacted_texts[idx] if idx < len(redacted_texts) else _unescape(m.group(2))
                idx += 1
                before = _unescape(m.group(2))
                if after != before:
                    detail.append((name, before, after))
                out.append(xml[pos:m.start()])
                out.append(m.group(1) + _escape(after) + m.group(3))
                pos = m.end()
            out.append(xml[pos:])
            new_xml[name] = "".join(out)

        with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as out_zip:
            for item in z.infolist():
                data = z.read(item.filename)
                if item.filename in new_xml:
                    data = new_xml[item.filename].encode("utf-8")
                out_zip.writestr(item, data)

    return {"parts": extra, "changed": len(detail), "texts": detail}


def redact_docx_extras(src, dst, redactor: Redactor) -> dict:
    """把 src 的附加部件脱敏后写到 dst（其它部件原样拷贝）。

    返回 {"parts": [...], "changed": N, "texts": [(part, before, after), ...]}
    出错时抛出，由调用方决定是否回退。
    """
    src, dst = Path(src), Path(dst)
    with zipfile.ZipFile(src) as z:
        names = z.namelist()
        extra = [n for n in names if EXTRA_PART_RE.match(n)]
        if not extra:
            shutil.copyfile(src, dst)
            return {"parts": [], "changed": 0, "texts": []}

        # 1) 收集所有附加部件的文本（按部件顺序，保证 prepare 结果对得上）
        per_part: dict[str, list[tuple[str, str]]] = {}
        flat: list[str] = []
        for name in extra:
            xml = z.read(name).decode("utf-8", "replace")
            spans = [(m.group(1), m.group(2), m.group(3)) for m in W_T_RE.finditer(xml)]
            texts = [_unescape(m.group(2)) for m in W_T_RE.finditer(xml)]
            per_part[name] = (xml, spans, texts)
            flat.extend(texts)

        # 2) 一次性交给引擎（同一实例，实体口径和正文一致）
        results = redactor.prepare(flat)

        # 3) 回写
        changed = 0
        detail = []
        new_xml: dict[str, str] = {}
        idx = 0
        for name in extra:
            xml, spans, texts = per_part[name]
            out = []
            pos = 0
            for (open_tag, raw, close_tag) in spans:
                br = results[idx]
                idx += 1
                before = _unescape(raw)
                after = br.redacted
                if after != before:
                    changed += 1
                    detail.append((name, before, after))
                start = xml.index(open_tag + raw + close_tag, pos)
                out.append(xml[pos:start])
                out.append(open_tag + _escape(after) + close_tag)
                pos = start + len(open_tag) + len(raw) + len(close_tag)
            out.append(xml[pos:])
            new_xml[name] = "".join(out)

        # 4) 重新打包：改过的部件换内容，其它逐字节拷贝
        with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as out_zip:
            for item in z.infolist():
                data = z.read(item.filename)
                if item.filename in new_xml:
                    data = new_xml[item.filename].encode("utf-8")
                out_zip.writestr(item, data)

    return {"parts": extra, "changed": changed, "texts": detail}

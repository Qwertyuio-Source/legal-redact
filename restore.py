# -*- coding: utf-8 -*-
"""把脱敏后的文书还原回去（需要配套的映射表）。

用法：
    python restore.py out/某某.脱敏.docx out/某某.映射表.json [-o 还原目录]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from docx import Document

from legalredact import docio


def _spans_for(text: str, pairs: list[tuple[str, str]]):
    """在 text 里找出所有『替身』，换成原文。"""
    spans: list[tuple[int, int, str, str]] = []
    taken: list[tuple[int, int]] = []
    # 长替身优先，避免「某某某公司」被「某某某公司二」啃掉一半
    for rep, orig in sorted(pairs, key=lambda kv: -len(kv[0])):
        start = 0
        while True:
            idx = text.find(rep, start)
            if idx < 0:
                break
            end = idx + len(rep)
            if not any(idx < e and s < end for s, e in taken):
                spans.append((idx, end, orig, "restore"))
                taken.append((idx, end))
            start = end
    return spans


def restore_file(path: Path, mapping_path: Path, out_dir: Path) -> Path:
    data = json.loads(mapping_path.read_text(encoding="utf-8"))
    pairs = [(e["replacement"], e["original"]) for e in data.get("entities", [])]
    blocks = data.get("blocks") or []
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{path.stem}.还原{path.suffix}"

    if path.suffix.lower() == ".docx":
        doc = Document(str(path))
        paras = docio.iter_paragraphs(doc)
        if blocks:
            # 按「脱敏后文本」里的位置精确还原，同一替身对应多个原文也不会串
            by_index = {b["index"]: b["spans"] for b in blocks}
            for i, p in enumerate(paras):
                spans = by_index.get(i)
                if not spans:
                    continue
                spans = [(s[0], s[1], s[3], "restore") for s in spans
                         if s[1] <= len(p.text)]
                if spans:
                    docio.rewrite_paragraph(p, spans)
        else:
            for p in paras:
                spans = _spans_for(p.text, pairs)
                if spans:
                    docio.rewrite_paragraph(p, spans)
        doc.save(str(out_path))
    else:
        text = docio.read_text_file(path)
        if blocks:
            spans = [s for b in blocks for s in b["spans"]]
            spans = sorted([(s[0], s[1], s[3]) for s in spans], key=lambda x: -x[0])
            for start, end, orig in spans:
                if end <= len(text):
                    text = text[:start] + orig + text[end:]
        else:
            for rep, orig in sorted(pairs, key=lambda kv: -len(kv[0])):
                text = text.replace(rep, orig)
        out_path.write_text(text, encoding="utf-8")

    return out_path


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="把脱敏文书还原")
    ap.add_argument("file")
    ap.add_argument("mapping")
    ap.add_argument("-o", "--out", default="restored")
    args = ap.parse_args(argv)
    out = restore_file(Path(args.file), Path(args.mapping), Path(args.out))
    print(f"已还原：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

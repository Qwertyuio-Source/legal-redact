# -*- coding: utf-8 -*-
"""法律文书脱敏 - 命令行入口。

用法：
    python redact.py 文书目录 [-o 输出目录]
    python redact.py a.docx b.txt
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from docx import Document

from legalredact import docio
from legalredact import types as T
from legalredact.engine import Redactor
from legalredact.pdfio import process_pdf

SUPPORTED = {".docx", ".xlsx", ".txt", ".md", ".markdown", ".pdf",
             ".html", ".htm", ".png", ".jpg", ".jpeg"}
KIND_CN = T.TYPE_CN

# 由 main() 统一设置，供各处理函数创建 Redactor
OPTIONS = {"mode": "mask", "mask_style": "surname", "redact_len": "fixed"}


def new_redactor(cfg: dict) -> Redactor:
    return Redactor(cfg, mode=OPTIONS["mode"], mask_style=OPTIONS["mask_style"],
                    redact_len=OPTIONS["redact_len"])


def collect_files(inputs: list[str]) -> list[Path]:
    files: list[Path] = []
    for raw in inputs:
        p = Path(raw)
        if p.is_dir():
            for child in sorted(p.rglob("*")):
                if child.suffix.lower() in SUPPORTED:
                    files.append(child)
        elif p.is_file():
            files.append(p)
        else:
            print(f"[跳过] 找不到：{raw}")
    return files


def apply_to_text(text: str, spans) -> str:
    for start, end, rep in sorted(((s[0], s[1], s[2]) for s in spans), key=lambda s: -s[0]):
        text = text[:start] + rep + text[end:]
    return text


def process_xlsx(path: Path, out_dir: Path, cfg: dict, name: str) -> dict:
    """Excel：只改单元格文本，其余 XML 一个字节不动（图片/图表/样式都在）。"""
    from legalredact.xlsxraw import redact_xlsx_auto

    redactor = new_redactor(cfg)
    out_path = out_dir / f"{name}.脱敏.xlsx"
    res = redact_xlsx_auto(str(path), str(out_path), redactor)

    changed = len({(c[0], c[1], c[2]) for c in res.get("cells", [])})
    preview_lines = ["原文  →  脱敏后", "=" * 60]
    for sheet, row, col, before, after in res.get("cells", [])[:200]:
        if before == after:
            continue
        preview_lines.append(f"[{sheet}!R{row}C{col}] {before[:120]}")
        preview_lines.append(f"  → {after[:120]}")
    (out_dir / f"{name}.对照.txt").write_text("\n".join(preview_lines), encoding="utf-8")

    return _finish(path, out_dir, redactor, out_path, changed, name)


def process_docx(path: Path, out_dir: Path, cfg: dict, name: str) -> dict:
    doc = Document(str(path))
    paras = docio.iter_paragraphs(doc)
    texts = [p.text for p in paras]

    # 批注/脚注/页眉页脚里的文字一起准备，保证同一个实体是同一个替身
    from legalredact.docxraw import apply_extra_texts, collect_extra_texts
    extra_texts = collect_extra_texts(path)

    redactor = new_redactor(cfg)
    results = redactor.prepare(texts + extra_texts)

    changed = 0
    for p, br in zip(paras, results[:len(texts)]):
        if br.spans:
            docio.rewrite_paragraph(p, br.spans)
            changed += 1

    out_path = out_dir / f"{name}.脱敏.docx"
    tmp_path = out_dir / f".{name}.body.tmp.docx"
    doc.save(str(tmp_path))
    # 附加部件用同一批 prepare 结果回写
    extra_res = apply_extra_texts(tmp_path, out_path, [br.redacted for br in results[len(texts):]])
    tmp_path.unlink(missing_ok=True)

    preview_lines = ["原文  →  脱敏后", "=" * 60]
    for br in results:
        if not br.spans:
            continue
        new = apply_to_text(br.text, br.spans)
        if docio.short_preview(br.text) == docio.short_preview(new):
            continue
        preview_lines.append("原文：" + docio.short_preview(br.text, 160))
        preview_lines.append("脱敏：" + docio.short_preview(new, 160))
        preview_lines.append("")
    (out_dir / f"{name}.对照.txt").write_text("\n".join(preview_lines), encoding="utf-8")

    return _finish(path, out_dir, redactor, out_path, changed, name)


def process_text(path: Path, out_dir: Path, cfg: dict, name: str) -> dict:
    text = docio.read_text_file(path)
    redactor = new_redactor(cfg)
    results = redactor.prepare([text])
    new_text = apply_to_text(text, results[0].spans)

    out_path = out_dir / f"{name}.脱敏{path.suffix}"
    out_path.write_text(new_text, encoding="utf-8")

    preview_lines = ["原文  →  脱敏后", "=" * 60]
    for br in results:
        if not br.spans:
            continue
        new = apply_to_text(br.text, br.spans)
        preview_lines.append("原文：" + docio.short_preview(br.text, 160))
        preview_lines.append("脱敏：" + docio.short_preview(new, 160))
        preview_lines.append("")
    (out_dir / f"{name}.对照.txt").write_text("\n".join(preview_lines), encoding="utf-8")

    return _finish(path, out_dir, redactor, out_path, 1 if results[0].spans else 0, name)


def _blackout_image(src: Path, dst: Path, br) -> Path | None:
    """图片：OCR 行框 + 字符比例定位，只涂实体那几个字。"""
    try:
        from PIL import Image, ImageDraw
        from legalredact.ocr import join_lines, ocr_lines, normalize_ocr
    except ImportError:
        return None
    lines = ocr_lines(str(src))
    for ln in lines:
        ln["text"] = normalize_ocr(ln["text"])
    _text, line_spans = join_lines(lines)
    img = Image.open(src).convert("RGB")
    draw = ImageDraw.Draw(img)
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
            width = right - left
            x0 = left + width * (i0 / n)
            x1 = left + width * (i1 / n)
            if x1 - x0 < 1:
                continue
            draw.rectangle([x0, min(ys), x1, max(ys)], fill=(0, 0, 0))
    img.save(dst)
    return dst


def process_generic(path: Path, out_dir: Path, cfg: dict, name: str) -> dict:
    from legalredact.extract import extract_documents

    is_image = path.suffix.lower() in {".png", ".jpg", ".jpeg"}
    items = None
    if is_image:
        # 图片：OCR 一次，文字和框共用，省掉重复识别
        from legalredact.imgio import read_image
        items = read_image(path)
        texts = ["\n".join(it["text"] for it in items)]
    else:
        pages = extract_documents(path)
        texts = [p["text"] for p in pages]

    redactor = new_redactor(cfg)
    results = redactor.prepare(texts)

    out_path = out_dir / f"{name}.脱敏.txt"
    out_path.write_text("\n\n".join(br.redacted for br in results), encoding="utf-8")

    if is_image and results:
        from legalredact.imgio import blackout_prepared
        blackout_prepared(path, out_dir / f"{name}.涂抹版{path.suffix}", items, results[0])

    lines = ["原文  →  脱敏后", "=" * 50]
    for br in results:
        if br.spans:
            lines.append("原文：" + " ".join(br.text.split())[:200])
            lines.append("脱敏：" + " ".join(br.redacted.split())[:200])
            lines.append("")
    (out_dir / f"{name}.对照.txt").write_text("\n".join(lines), encoding="utf-8")

    mapping_path = out_dir / f"{name}.映射表.json"
    mapping_path.write_text(json.dumps(redactor.mapping(), ensure_ascii=False, indent=2),
                            encoding="utf-8")
    for row in redactor.rows:
        row["文件"] = path.name
    return {
        "file": path, "out": out_path, "mapping": mapping_path, "rows": redactor.rows,
        "leftovers": [i for i in redactor.issues if i.kind == "leftover"],
        "candidates": [i for i in redactor.issues if i.kind == "candidate"],
        "changed": sum(1 for br in results if br.spans),
    }


def _finish(path: Path, out_dir: Path, redactor: Redactor, out_path: Path,
            changed: int, name: str) -> dict:
    mapping_path = out_dir / f"{name}.映射表.json"
    mapping_path.write_text(
        json.dumps(redactor.mapping(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for row in redactor.rows:
        row["文件"] = path.name
    return {
        "file": path,
        "out": out_path,
        "mapping": mapping_path,
        "rows": redactor.rows,
        "leftovers": [i for i in redactor.issues if i.kind == "leftover"],
        "candidates": [i for i in redactor.issues if i.kind == "candidate"],
        "changed": changed,
    }


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(description="法律文书脱敏工具")
    ap.add_argument("inputs", nargs="+", help="文件或目录")
    ap.add_argument("-o", "--out", default="out", help="输出目录（默认 out）")
    ap.add_argument("-c", "--config", default=None, help="配置文件 config.json")
    ap.add_argument("--mode", default="mask", choices=["mask", "role", "redact"],
                    help="脱敏模式：mask 掩码（默认）/ role 角色化 / redact 涂黑")
    ap.add_argument("--mask-style", default="surname", choices=["surname", "full"],
                    help="mask 模式下姓名风格：surname 保留姓氏 / full 全掩码")
    ap.add_argument("--redact-len", default="fixed", choices=["fixed", "same"],
                    help="redact 模式下黑块长度：fixed 固定 / same 等长")
    args = ap.parse_args(argv)

    OPTIONS["mode"] = args.mode
    OPTIONS["mask_style"] = args.mask_style
    OPTIONS["redact_len"] = args.redact_len

    cfg: dict = {}
    if args.config and Path(args.config).exists():
        cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))

    files = collect_files(args.inputs)
    if not files:
        print("没有可处理的文件（支持 .docx / .txt / .md）")
        return 1

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    all_rows: list[dict] = []
    all_left: list[dict] = []
    all_cand: list[dict] = []
    results = []
    used_names: set[str] = set()

    for f in files:
        suffix = f.suffix.lower()
        stem = f.stem
        # 同名不同格式（.docx / .pdf / .txt）不要互相覆盖：带上扩展名区分
        name = stem
        if name in used_names:
            tag = suffix.lstrip(".") or "file"
            name = f"{stem}-{tag}"
            n = 2
            while name in used_names:
                name = f"{stem}-{tag}-{n}"
                n += 1
        used_names.add(name)
        if suffix == ".docx":
            res = process_docx(f, out_dir, cfg, name)
        elif suffix == ".xlsx":
            res = process_xlsx(f, out_dir, cfg, name)
        elif suffix == ".pdf":
            res = process_pdf(f, out_dir, new_redactor(cfg), name)
            mapping_path = out_dir / f"{name}.映射表.json"
            mapping_path.write_text(
                json.dumps(res["redactor"].mapping(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            res["mapping"] = mapping_path
            for row in res["rows"]:
                row["文件"] = f.name
        elif suffix in {".html", ".htm", ".png", ".jpg", ".jpeg"}:
            res = process_generic(f, out_dir, cfg, name)
        elif suffix in SUPPORTED:
            res = process_text(f, out_dir, cfg, name)
        else:
            print(f"[跳过] 暂不支持：{f.name}（先手工另存为 docx 或 txt）")
            continue
        results.append(res)
        all_rows.extend(res["rows"])
        all_left.extend({"文件": f.name, "残留": i.text, "说明": i.note} for i in res["leftovers"])
        all_cand.extend({"文件": f.name, "待确认": i.text, "说明": i.note} for i in res["candidates"])

    report = out_dir / "脱敏明细.csv"
    with report.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=["文件", "段落", "类型", "原文", "替换为", "规则", "位置"])
        w.writeheader()
        for r in all_rows:
            w.writerow({
                "文件": r["文件"], "段落": r["block"],
                "类型": KIND_CN.get(r["type"], r["type"]),
                "原文": r["original"], "替换为": r["replacement"],
                "规则": r["rule"], "位置": r["start"],
            })

    with (out_dir / "漏脱检查.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=["文件", "残留", "说明"])
        w.writeheader()
        for r in all_left:
            w.writerow(r)

    with (out_dir / "人工复核清单.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=["文件", "待确认", "说明"])
        w.writeheader()
        for r in all_cand:
            w.writerow(r)

    lines = ["法律文书脱敏 —— 运行报告", "=" * 40, ""]
    total_entities = len(all_rows)
    lines.append(f"处理文件：{len(results)} 个")
    lines.append(f"脱敏片段：{total_entities} 处")
    lines.append(f"二次扫描残留：{len(all_left)} 处")
    lines.append(f"待人工复核：{len(all_cand)} 条")
    lines.append("")
    for r in results:
        lines.append(f"· {r['file'].name}  改动段落 {r['changed']}，脱敏 {len(r['rows'])} 处"
                     f"{'，⚠ 有残留' if r['leftovers'] else '，✓ 无残留'}")
    (out_dir / "运行报告.txt").write_text("\n".join(lines), encoding="utf-8")

    print("\n".join(lines))
    if all_left:
        print("\n⚠ 有残留，请打开 漏脱检查.csv 处理后再交付。")
        return 1
    print("\n✅ 二次扫描无残留。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

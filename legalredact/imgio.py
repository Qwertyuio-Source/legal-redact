# -*- coding: utf-8 -*-
"""图片通道：jpg/png → OCR 取字和框 → 命中片段按字符比例涂黑。

思路和 pdfio.py 的扫描页一致：OCR 给的框是「整行」的，
所以命中片段在行内的横向位置要按字符宽度比例切出来，不能整行涂黑。
中文按 1.0、ASCII 按 0.5、空格按 0.3 估宽（全角标点算 1.0）。
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageOps

from . import ocr
from .engine import Redactor

# 输出支持的格式（输入交给 PIL，不额外限制）
IMAGE_EXT = {".jpg", ".jpeg", ".png"}

# 涂黑时向外留的余量，别露出半个字
PAD_X = 2
PAD_Y = 1

STYLES = ("black", "white", "mosaic")


# ---------- 读图 ----------
def _flatten(im: Image.Image) -> Image.Image:
    """统一成 RGB：带透明通道的先铺白底，不直接丢掉 alpha。"""
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        rgba = im.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.getchannel("A"))
        return bg
    return im.convert("RGB")


def _load_rgb(path: Path) -> Image.Image:
    """读图：先按 EXIF 摆正方向，再统一成 RGB。"""
    with Image.open(path) as raw:
        return _flatten(ImageOps.exif_transpose(raw))


def _bbox(points) -> tuple[int, int, int, int]:
    """四点坐标 → 左上右下。"""
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (int(round(min(xs))), int(round(min(ys))),
            int(round(max(xs))), int(round(max(ys))))


def read_image(path) -> list[dict]:
    """读一张图里的文字：返回 [{text, box, page, raw, score}]。

    text 是归一化后的文本（O→0、全角标点→半角，用于检测）；
    raw 是 OCR 原文（长度和 text 一致，用来估字宽，避免全角标点被当成半角）。
    """
    path = Path(path)
    img = _load_rgb(path)
    lines = ocr.ocr_lines(np.array(img))
    out: list[dict] = []
    for ln in lines:
        raw_text = ln["text"] or ""
        text = ocr.normalize_ocr(raw_text)
        if not text.strip():
            continue
        out.append({
            "text": text,
            "raw": raw_text,
            "box": _bbox(ln["box"]),
            "score": round(float(ln["score"]), 4),
            "page": 0,          # 单图就是单页，和 api 里的 page 对齐
        })
    return out


# ---------- 定位 ----------
def _weights(text: str) -> list[float]:
    """按字符估相对宽度：中文 1.0、ASCII 0.5、空格 0.3。"""
    out: list[float] = []
    for ch in text:
        if ch.isspace():
            out.append(0.3)
        elif ord(ch) < 128:
            out.append(0.5)
        else:
            out.append(1.0)
    return out


def _slice_x(text: str, box: tuple[int, int, int, int],
             i0: int, i1: int) -> tuple[float, float]:
    """把行框按字宽比例切出 [i0, i1) 这段字符的横向范围。"""
    left, _top, right, _bottom = box
    width = right - left
    ws = _weights(text)
    total = sum(ws) or 1.0
    i0 = max(0, min(i0, len(text)))
    i1 = max(0, min(i1, len(text)))
    x0 = left + width * (sum(ws[:i0]) / total)
    x1 = left + width * (sum(ws[:i1]) / total)
    return x0, x1


def _line_offsets(items: list[dict]) -> list[tuple[int, int, int]]:
    """每行文本在整段拼文里的 (start, end, 行号)，行间用 \\n 拼。"""
    out: list[tuple[int, int, int]] = []
    pos = 0
    for i, item in enumerate(items):
        n = len(item["text"])
        out.append((pos, pos + n, i))
        pos += n + 1
    return out


def _padded_box(x0: float, y0: float, x1: float, y1: float,
                size: tuple[int, int]) -> tuple[int, int, int, int]:
    """涂黑框外扩 1~2px，再夹到图片范围内（坐标是含端点的整数）。"""
    w, h = size
    left = max(0, int(math.floor(x0)) - PAD_X)
    top = max(0, int(math.floor(y0)) - PAD_Y)
    right = min(w - 1, int(math.ceil(x1)) + PAD_X)
    bottom = min(h - 1, int(math.ceil(y1)) + PAD_Y)
    return left, top, right, bottom


# ---------- 涂抹 ----------
def _paint(img: Image.Image, box: tuple[int, int, int, int], style: str) -> None:
    left, top, right, bottom = box
    if right <= left or bottom <= top:
        return
    if style == "mosaic":
        w, h = right - left + 1, bottom - top + 1
        crop = img.crop((left, top, right + 1, bottom + 1))
        small = crop.resize((max(1, w // 10), max(1, h // 10)), Image.BILINEAR)
        img.paste(small.resize((w, h), Image.NEAREST), (left, top))
        return
    fill = (0, 0, 0) if style == "black" else (255, 255, 255)
    ImageDraw.Draw(img).rectangle(box, fill=fill)


def _save(img: Image.Image, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    suffix = dst.suffix.lower()
    if suffix in (".jpg", ".jpeg"):
        img.save(dst, "JPEG", quality=92)      # 同格式，质量 92
    elif suffix == ".png":
        img.save(dst, "PNG")
    else:
        raise RuntimeError(f"输出图片只支持 .jpg/.jpeg/.png，收到：{dst.suffix}")


# ---------- 主流程 ----------
def redact_image(src_path, dst_path, redactor: Redactor, style: str = "black") -> dict:
    """图片脱敏：OCR → 实体识别 → 按字符比例把命中片段涂掉。

    返回 {"hits": [...], "count": N, "ocr_text": "...", "out": ..., "size": ...}
    """
    if style not in STYLES:
        raise ValueError(f"未知涂抹样式：{style}（可选 {', '.join(STYLES)}）")

    src, dst = Path(src_path), Path(dst_path)
    if not src.is_file():
        raise FileNotFoundError(f"图片不存在：{src}")
    if dst.suffix.lower() not in IMAGE_EXT:
        raise RuntimeError(f"输出图片只支持 .jpg/.jpeg/.png，收到：{dst.suffix}")

    items = read_image(src)                    # 和后面画图用同一份坐标
    full = "\n".join(it["text"] for it in items)
    br = redactor.prepare([full])[0]           # 整图当一段文本，跨行实体也能认
    return blackout_prepared(src, dst, items, br, style)


def blackout_prepared(src_path, dst_path, items: list[dict], br, style: str = "black") -> dict:
    """已经 OCR 过、也已经 prepare 过时，直接涂（批量和命令行主流程用这个）。"""
    if style not in STYLES:
        raise ValueError(f"未知涂抹样式：{style}（可选 {', '.join(STYLES)}）")
    src, dst = Path(src_path), Path(dst_path)
    full = "\n".join(it["text"] for it in items)
    img = _load_rgb(src)

    line_pos = _line_offsets(items)
    hits: list[dict] = []
    for start, end, repl, kind, eid in br.spans:
        original = full[start:end]
        for lstart, lend, li in line_pos:
            if not (start < lend and lstart < end):   # 该片段不在这行
                continue
            item = items[li]
            text = item["text"]
            i0 = max(start, lstart) - lstart
            i1 = min(end, lend) - lstart
            if i1 <= i0:
                continue
            # 字宽用 OCR 原文估（全角标点宽度才正确），长度与 text 一致
            ref = item["raw"] if len(item["raw"]) == len(text) else text
            x0, x1 = _slice_x(ref, item["box"], i0, i1)
            box = _padded_box(x0, item["box"][1], x1, item["box"][3], img.size)
            _paint(img, box, style)
            hits.append({
                "text": text[i0:i1],
                "original": original,
                "type": kind,
                "replacement": repl,
                "entity_id": eid,
                "box": box,
                "line": li,
            })

    _save(img, dst)
    return {
        "hits": hits,
        "count": len(hits),
        "ocr_text": full,
        "out": str(dst),
        "size": list(img.size),
    }

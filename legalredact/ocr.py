# -*- coding: utf-8 -*-
"""OCR：扫描件 / 图片 → 文本 + 位置框。用 RapidOCR（PP-OCR 模型，ONNX 推理，无需装 Paddle）。"""
from __future__ import annotations

import re

import numpy as np

_ENGINE = None


def _engine():
    global _ENGINE
    if _ENGINE is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "缺少 OCR 依赖。请运行：\n"
                "  pip install rapidocr_onnxruntime -i https://pypi.tuna.tsinghua.edu.cn/simple"
            ) from exc
        _ENGINE = RapidOCR()
    return _ENGINE


OCR_FIX = str.maketrans({
    "O": "0", "o": "0", "I": "1", "l": "1", "|": "1",
    "：": ":", "，": ",", "。": ".", "（": "(", "）": ")",
    "　": " ",
})


def normalize_ocr(text: str) -> str:
    """OCR 常见误识别归一：全角→半角、O/o→0、l/I→1。"""
    return text.translate(OCR_FIX)


def ocr_lines(image) -> list[dict]:
    """返回 [{text, box, score}]，box 是四点坐标 [[x,y],...]。"""
    engine = _engine()
    result, _elapse = engine(image)
    lines = []
    for item in result or []:
        box, text, score = item[0], item[1], item[2]
        if not text:
            continue
        lines.append({"text": text, "box": box, "score": float(score)})
    return lines


def join_lines(lines: list[dict]) -> tuple[str, list[tuple[int, int, dict]]]:
    """把 OCR 行拼成整段文本，并记录每行在文本里的区间。"""
    parts, spans, pos = [], [], 0
    for ln in lines:
        t = ln["text"]
        parts.append(t)
        spans.append((pos, pos + len(t), ln))
        pos += len(t) + 1
    return "\n".join(parts), spans


RE_DIGITISH = re.compile(r"[\dOoIl|]{6,}")


def normalize_for_id(text: str) -> str:
    """只在数字串上下文中做 O→0 / I→1，避免把英文单词改坏。"""
    return RE_DIGITISH.sub(lambda m: m.group(0).translate(OCR_FIX), text)

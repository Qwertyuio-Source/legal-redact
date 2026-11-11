# -*- coding: utf-8 -*-
"""xlsx 读写：只改文字单元格，样式/列宽/合并/公式/数字格式原样保留。

思路和 docio.py 一致：用 openpyxl 打开 → 只给「文本单元格」换 value → 原样存回。
openpyxl 会把样式、列宽、合并区域、公式、数字格式一起写回去，所以我们只要
保证「非文本单元格一个都不碰」，格式就不会被破坏。

注意：它会丢图片 / 图表 / 透视表，正式使用请用 xlsxraw.redact_xlsx_auto；
这里保留是为了 raw 版解析失败时兜底。
"""
from __future__ import annotations

from pathlib import Path

from openpyxl import load_workbook

from .xlsxraw import redact_sheet_texts


# ---------- 公共 ----------

def _is_text(value, data_type: str) -> bool:
    """普通文本单元格：字符串、非空、不是公式（公式的 data_type 是 "f"）。"""
    return data_type != "f" and isinstance(value, str) and bool(value.strip())


def _text_cells(ws):
    """按行给出 (cell, 文本)，只要要脱敏的文本单元格。"""
    for row in ws.iter_rows():
        for cell in row:
            if _is_text(cell.value, cell.data_type):
                yield cell, cell.value


# ---------- 读取 ----------

def read_xlsx(path) -> list[dict]:
    """读所有 sheet 的文本单元格：[{"name": 表名, "cells": [(row, col, text), ...]}]。"""
    wb = load_workbook(str(path), data_only=False)
    try:
        sheets: list[dict] = []
        for ws in wb.worksheets:
            cells = [(cell.row, cell.column, text) for cell, text in _text_cells(ws)]
            sheets.append({"name": ws.title, "cells": cells})
        return sheets
    finally:
        wb.close()


# ---------- 写回 ----------

def redact_xlsx(src_path, dst_path, redactor) -> dict:
    """按 sheet 整表脱敏后另存：同一个"张伟"在各 sheet 里都是同一个替身。

    返回 {"shapes": [...], "cells": [(sheet, row, col, before, after), ...]}
    shapes 是单元格内每个被替换片段的坐标（给网页端做高亮用）。
    """
    wb = load_workbook(str(src_path), data_only=False)
    shapes: list[dict] = []
    changed: list[tuple] = []
    try:
        for ws in wb.worksheets:
            targets = list(_text_cells(ws))
            if not targets:
                continue
            # 一个 sheet 的文字一次性交给 prepare：同表内实体口径统一
            # （整格值再走 xlsx 通道自己的启发，见 xlsxraw.redact_sheet_texts）
            results = redact_sheet_texts(redactor, [text for _cell, text in targets])
            for (cell, before), br in zip(targets, results):
                if not br.spans:                     # 没命中就不动，格式绝对安全
                    continue
                after = br.redacted
                cell.value = after
                if cell.data_type == "f":            # 替身万一以 = 开头，别被当公式
                    cell.data_type = "s"
                changed.append((ws.title, cell.row, cell.column, before, after))
                for start, end, rep, kind, eid in br.spans:
                    shapes.append({
                        "sheet": ws.title, "ref": cell.coordinate,
                        "row": cell.row, "col": cell.column,
                        "start": start, "end": end,
                        "original": before[start:end], "replacement": rep,
                        "kind": kind, "entity_id": eid,
                    })
        dst = Path(dst_path)
        if dst.parent and not dst.parent.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
        wb.save(str(dst))
    finally:
        wb.close()

    return {"shapes": shapes, "cells": changed}

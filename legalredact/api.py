# -*- coding: utf-8 -*-
"""对外 API：一段文本 / 一份文件的统一脱敏入口。

    desensitize_text(text, mode="mask") -> {masked_text, entities, mapping}
"""
from __future__ import annotations

from pathlib import Path

from .engine import Redactor


def desensitize_text(text: str, mode: str = "mask", mask_style: str = "surname",
                     redact_len: str = "fixed", config: dict | None = None) -> dict:
    """对单段文本脱敏。三种模式共用同一套识别结果，只有渲染不同。"""
    redactor = Redactor(config or {}, mode=mode, mask_style=mask_style,
                        redact_len=redact_len)
    br = redactor.prepare([text])[0]
    return {
        "masked_text": br.redacted,
        "entities": [
            {"entity_id": r["entity_id"], "type": r["type"], "start": r["start"],
             "end": r["start"] + len(r["original"]), "original": r["original"],
             "replacement": r["replacement"], "mode": mode,
             "reversible": mode != "redact"}
            for r in redactor.rows
        ],
        "mapping": redactor.mapping(),
        "issues": redactor.issues,
    }


def desensitize_file(path: str | Path, out_dir: str | Path, mode: str = "mask",
                     mask_style: str = "surname", name: str | None = None) -> dict:
    """对一份文件脱敏（docx / pdf / png / html / txt 自动分派）。"""
    import redact as cli  # 根目录的命令行模块，复用同一套处理函数

    path, out_dir = Path(path), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = name or path.stem
    cli.OPTIONS.update({"mode": mode, "mask_style": mask_style})

    suffix = path.suffix.lower()
    if suffix == ".docx":
        return cli.process_docx(path, out_dir, {}, name)
    if suffix == ".pdf":
        return cli.process_pdf(path, out_dir, cli.new_redactor({}), name)
    if suffix in {".html", ".htm", ".png", ".jpg", ".jpeg"}:
        return cli.process_generic(path, out_dir, {}, name)
    return cli.process_text(path, out_dir, {}, name)

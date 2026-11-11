# -*- coding: utf-8 -*-
"""批量脱敏：文件夹 / 压缩包 → 自动筛选 → 只处理含敏感信息的文件 → 出清单。

    from legalredact.batch import scan, needs_redaction, run_batch
    from legalredact.engine import Redactor

    scan("客户材料")                                  # 先看有什么
    needs_redaction("起诉状.docx")                    # (是否需要脱敏, 命中数)
    run_batch("客户材料", "out", lambda: Redactor({}))  # 跑批量

筛选逻辑：先用 extract_documents() 取文字，再交给 detectors.detect() 扫一遍；
命中 0 处的文件直接跳过（记入清单并写明原因），不白跑脱敏流程。

输出：out_dir 下保持原有目录层级，另写一份 report.csv（UTF-8 BOM）：
    文件, 命中数, 结果（已处理/跳过/失败）, 输出
  · 跳过/失败的行，输出列写的是原因（以「— 」开头）；
  · 命中数列在没有检测过的行（已脱敏过 / 读不出文字）写「-」，不写 0，免得被当成「干净」。

压缩包：解压到临时目录逐层处理（支持嵌套 zip、中文文件名），
只把真正处理过的文件重新打成一个 `<原名>.脱敏.zip`，保持原有目录层级；
包内一个需要处理的都没有时不产出空压缩包，整个压缩包记入跳过清单。
对照.txt / 映射表.json 里有原文，只留在本机复核，不放进交付压缩包。

重复运行：文件名带「脱敏」的、或输出目录里已有 `<原名>.脱敏*` 的，直接记跳过。
"""
from __future__ import annotations

import csv
import importlib
import inspect
import shutil
import tempfile
import zipfile
from pathlib import Path

from .detectors import detect

# ---------- 支持的文件类型 ----------
# 扩展名 → 类型。xlsx / 图片的实际处理在 xlsxio / imgio 里（延迟导入）。
KIND_BY_EXT: dict[str, str] = {
    ".docx": "docx",
    ".pdf": "pdf",
    ".txt": "text", ".md": "text", ".markdown": "text",
    ".html": "html", ".htm": "html",
    ".xlsx": "xlsx", ".xlsm": "xlsx",
    ".jpg": "image", ".jpeg": "image", ".png": "image",
}
XLSX_EXT = {".xlsx", ".xlsm"}
ARCHIVE_EXT = {".zip"}

# 单文件处理顺带写出的复核产物（含原文）：文件名里带「脱敏」或这些后缀的都算处理过
REVIEW_SUFFIXES = (".对照.txt", ".映射表.json", ".漏脱检查.csv",
                   ".人工复核清单.csv", "运行报告.txt")

# 嵌套压缩包最多展开几层（防止 zip 炸弹式套娃）
MAX_ZIP_DEPTH = 3

RESULT_DONE = "已处理"
RESULT_SKIP = "跳过"
RESULT_FAIL = "失败"

REPORT_FIELDS = ["文件", "命中数", "结果", "输出"]


class _Unsupported(Exception):
    """该类型暂不支持（xlsxio / imgio 模块还没就绪）。"""


# ======================================================================
# 扫描
# ======================================================================

def scan(path) -> dict:
    """扫描一个文件 / 文件夹 / .zip，返回候选文件、压缩包和跳过项。

    返回结构：
        {"files":    [{"path", "kind", "size"}],            # 可以处理的普通文件
         "archives": [{"path", "kind", "size", "entries", "kinds"}],  # zip（不去读内部内容）
         "skipped":  [{"path", "reason"}]}                  # 不支持 / 已脱敏 / 隐藏文件

    kind 取值：docx / pdf / text / html / xlsx / image / zip。
    注意：scan() 不排除输出目录（它不知道 out_dir），run_batch() 会自己排除。
    """
    root = Path(path)
    info: dict = {"files": [], "archives": [], "skipped": []}
    if not root.exists():
        info["skipped"].append({"path": str(root), "reason": "路径不存在"})
        return info
    if root.is_dir():
        for child in sorted(root.rglob("*")):
            if child.is_dir():
                continue
            _add_candidate(child, info)
    else:
        _add_candidate(root, info)
    return info


def _add_candidate(path: Path, info: dict) -> None:
    """把一个路径归类到 files / archives / skipped。"""
    name = path.name
    if name.startswith((".", "~$")):
        info["skipped"].append({"path": str(path), "reason": "隐藏文件或 Office 临时文件"})
        return
    if _is_processed_name(name):
        info["skipped"].append({"path": str(path),
                                "reason": "已脱敏过（文件名带标记，避免重复处理）"})
        return

    suffix = path.suffix.lower()
    if suffix in ARCHIVE_EXT:
        info["archives"].append(_archive_entry(path))
        return

    kind = KIND_BY_EXT.get(suffix)
    if not kind:
        if suffix == ".doc":
            reason = "不支持的文件类型（旧版 .doc 请先用 Word 另存为 .docx）"
        elif suffix == ".zip":
            reason = "不支持的压缩格式（只支持 .zip）"
        else:
            reason = f"不支持的文件类型（{suffix or '无扩展名'}）"
        info["skipped"].append({"path": str(path), "reason": reason})
        return

    info["files"].append({"path": str(path), "kind": kind, "size": _size(path)})


def _archive_entry(path: Path) -> dict:
    """zip 只读目录信息（条目数、各类型数量），不解压。"""
    entry: dict = {"path": str(path), "kind": "zip", "size": _size(path)}
    try:
        with zipfile.ZipFile(path) as zf:
            names = [_zip_member_name(i) for i in zf.infolist() if not i.is_dir()]
    except (zipfile.BadZipFile, OSError) as exc:
        entry["error"] = f"无法读取压缩包：{exc}"
        return entry

    kinds: dict[str, int] = {}
    for name in names:
        suffix = Path(name).suffix.lower()
        if suffix in ARCHIVE_EXT:
            label = "zip(嵌套)"
        elif suffix in KIND_BY_EXT:
            label = KIND_BY_EXT[suffix]
        else:
            label = "不支持"
        kinds[label] = kinds.get(label, 0) + 1
    entry["entries"] = len(names)
    entry["kinds"] = kinds
    return entry


# ======================================================================
# 判断单个文件要不要脱敏
# ======================================================================

def needs_redaction(path) -> tuple[bool, int]:
    """(是否需要脱敏, 命中数)。

    用 extract_documents() 取文字 → detectors.detect() 数命中；
    取不到文字（依赖缺失 / 文件损坏 / 不支持的类型）按「不需要」返回，
    要区分「干净」和「没读出来」请用 run_batch，它会把异常记进 failed。
    """
    try:
        hits = _count_hits(Path(path))
    except Exception:  # noqa: BLE001 —— 检测失败不能当成「有敏感信息」硬处理
        return False, 0
    return hits > 0, hits


def _count_hits(path: Path) -> int:
    total = 0
    for text in _texts_for_detect(path):
        if text and text.strip():
            total += len(detect(text))
    return total


def _texts_for_detect(path: Path) -> list[str]:
    """尽量把文件里的文字取出来；xlsx 的 extract_documents 支持还在补，先走降级路径。"""
    if path.suffix.lower() in XLSX_EXT:
        return _xlsx_texts(path)
    from .extract import extract_documents   # 延迟导入：extract 依赖 numpy / docx

    return [doc.get("text") or "" for doc in extract_documents(path)]


def _xlsx_texts(path: Path) -> list[str]:
    """xlsx 取文字：优先用 xlsxio 的 read_xlsx，没就绪就用 openpyxl 只读扫一遍。"""
    module, _err = _load_optional_module("xlsxio")
    if module is not None:
        fn = getattr(module, "read_xlsx", None)
        if callable(fn):
            return _as_texts(fn(path))
        for fn_name in ("extract_texts", "read_texts", "extract_text", "cell_texts"):
            fn = getattr(module, fn_name, None)
            if callable(fn):
                return _as_texts(fn(path))

    try:
        import openpyxl
    except ImportError as exc:
        raise RuntimeError("xlsx 取文字需要 openpyxl，或等 legalredact/xlsxio.py 就绪") from exc

    workbook = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    try:
        lines: list[str] = []
        for sheet in workbook.worksheets:
            lines.append(sheet.title or "")
            for row in sheet.iter_rows(values_only=True):
                lines.append(" ".join(str(v) for v in row if v is not None))
        return ["\n".join(lines)]
    finally:
        workbook.close()


def _as_texts(value) -> list[str]:
    """把各种可能的返回值统一成文字列表（xlsxio 返回 [{name, cells}]）。"""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        if "cells" in value:                       # xlsxio.read_xlsx 的单表结构
            return _as_texts([cell[2] for cell in value["cells"] if len(cell) > 2])
        for key in ("texts", "text", "pages"):
            if key in value:
                return _as_texts(value[key])
        return _as_texts(list(value.values()))
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        for item in value:
            if isinstance(item, dict):
                out.extend(_as_texts(item.get("text") or item.get("cells") or ""))
            elif isinstance(item, (list, tuple)):
                out.extend(_as_texts(item))
            elif isinstance(item, (str, int, float)):
                out.append(str(item))
        return out
    return [str(value)]


# ======================================================================
# 批量处理
# ======================================================================

def run_batch(path, out_dir, redactor_factory) -> dict:
    """批量脱敏：自动筛选 → 只处理命中的文件 → 出 report.csv。

    path             文件 / 文件夹 / .zip
    out_dir          输出目录（会保持原有目录层级）
    redactor_factory 返回新的 Redactor 实例；它的 mode / mask_style / redact_len
                     会被同步给现有的单文件处理函数（desensitize_file）。

    返回：
        {"processed": [{"path", "kind", "hits", "out"}],
         "skipped":   [{"path", "hits", "reason"}],
         "failed":    [{"path", "hits", "reason"}],
         "report_path": ".../report.csv"}
    """
    src = Path(path)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    ctx = _Context(out, redactor_factory)
    if src.is_file() and src.suffix.lower() in ARCHIVE_EXT:
        _handle_zip(src, out, src.name, "", ctx, depth=0)
    elif src.is_file():
        _handle_single(src, out, ctx)
    elif src.is_dir():
        # 输出目录在输入目录「里面」时，遍历要把它排除，否则会处理自己的产物；
        # 输出目录在输入目录外面（哪怕是父目录）不受影响。
        excluded = None
        if out.resolve() != src.resolve() and _is_under(out.resolve(), src.resolve()):
            excluded = out.resolve()
        _walk(src, out, ctx, "", "", depth=0, excluded=excluded)
    else:
        ctx.skip(str(src), "-", "路径不存在")

    report_path = _write_report(out, ctx.rows)
    return {
        "processed": ctx.processed,
        "skipped": ctx.skipped,
        "failed": ctx.failed,
        "report_path": str(report_path),
    }


class _Context:
    """一次批量运行的记账本：输出目录、工厂、结果清单、report 行、重名控制。"""

    def __init__(self, out_dir: Path, factory):
        self._factory = factory
        self.processed: list[dict] = []
        self.skipped: list[dict] = []
        self.failed: list[dict] = []
        self.rows: list[dict] = []
        self.style = _probe_style(factory)
        self._used: dict[str, set[str]] = {}

    def new_redactor(self):
        return self._factory()

    def unique_stem(self, target_dir: Path, stem: str) -> str:
        """同一输出目录里同名不同后缀的文件别互相覆盖（a.txt / a.md 都会输出 a.脱敏.txt）。"""
        key = str(Path(target_dir).resolve())
        used = self._used.setdefault(key, set())
        name, n = stem, 2
        while name in used:
            name = f"{stem}-{n}"
            n += 1
        used.add(name)
        return name

    def done(self, display: str, hits: int, kind: str, out_label: str) -> None:
        self.processed.append({"path": display, "kind": kind, "hits": hits, "out": out_label})
        self.rows.append({"文件": display, "命中数": hits,
                          "结果": RESULT_DONE, "输出": out_label})

    def skip(self, display: str, hits, reason: str) -> None:
        self.skipped.append({"path": display, "hits": hits, "reason": reason})
        self.rows.append({"文件": display, "命中数": _hits_cell(hits),
                          "结果": RESULT_SKIP, "输出": f"— {reason}"})

    def fail(self, display: str, hits, reason: str) -> None:
        self.failed.append({"path": display, "hits": hits, "reason": reason})
        self.rows.append({"文件": display, "命中数": _hits_cell(hits),
                          "结果": RESULT_FAIL, "输出": f"— {reason}"})


def _probe_style(factory) -> dict:
    """用工厂造一个 Redactor 读渲染配置，再同步给 redact.OPTIONS（desensitize_file 靠它拿 redact_len）。"""
    try:
        probe = factory()
    except Exception:  # noqa: BLE001 —— 工厂自身问题交给具体文件处理时报错
        return {"mode": "mask", "mask_style": "surname", "redact_len": "fixed"}

    renderer = getattr(probe, "renderer", None)
    style = {
        "mode": getattr(probe, "mode", "mask"),
        "mask_style": getattr(renderer, "mask_style", "surname"),
        "redact_len": getattr(renderer, "redact_len", "fixed"),
    }
    try:
        import redact as cli      # 根目录命令行模块，复用同一套处理函数
    except ImportError:
        return style             # 项目根不在 sys.path 时 desensitize_file 会在处理阶段报错
    cli.OPTIONS.update(style)
    return style


def _handle_single(src_path: Path, out_root: Path, ctx: _Context) -> None:
    """输入就是一个文件：直接放进 out_dir。"""
    info = scan(src_path)
    for item in info["files"]:
        _handle_file(Path(item["path"]), out_root, Path(src_path.name), ctx, "", "")
    for item in info["skipped"]:
        ctx.skip(item["path"], "-", item["reason"])


def _walk(src_root: Path, out_root: Path, ctx: _Context, src_label: str,
          out_label: str, depth: int, excluded: Path | None) -> int:
    """递归遍历一个目录，返回成功产出的文件数。

    src_label / out_label 是压缩包内部文件在清单里显示的前缀，
    比如 "案件材料.zip/" 和 "案件材料.脱敏.zip/"；处理普通文件夹时都是空串。
    """
    produced = 0
    info = scan(src_root)
    for entry in info["files"]:
        src_path = Path(entry["path"])
        rel = src_path.relative_to(src_root)
        if excluded and _is_under(src_path, excluded):
            ctx.skip(f"{src_label}{rel.as_posix()}", "-", "输出目录内的文件（跳过）")
            continue
        produced += _handle_file(src_path, out_root / rel.parent, rel,
                                 ctx, src_label, out_label)
    for entry in info["skipped"]:
        display = _label(src_label, Path(entry["path"]), src_root)
        ctx.skip(display, "-", entry["reason"])
    for entry in info["archives"]:
        zip_path = Path(entry["path"])
        if excluded and _is_under(zip_path, excluded):
            ctx.skip(_label(src_label, zip_path, src_root), "-", "输出目录内的文件（跳过）")
            continue
        rel = zip_path.relative_to(src_root)
        target_dir = out_root / rel.parent
        produced += _handle_zip(zip_path, target_dir,
                                _label(src_label, zip_path, src_root),
                                out_label, ctx, depth + 1)
    return produced


def _handle_file(src_path: Path, target_dir: Path, rel: Path, ctx: _Context,
                 src_label: str, out_label: str) -> int:
    """判断 → 处理一个文件，返回 1（产出了文件）/ 0。"""
    display = f"{src_label}{rel.as_posix()}"
    kind = KIND_BY_EXT.get(src_path.suffix.lower(), "")

    # 已经产出过同名结果 → 当作重复运行，跳过（避免二次处理）
    if _already_output(target_dir, src_path.stem):
        ctx.skip(display, "-", "已处理过（输出已存在）")
        return 0

    try:
        hits = _count_hits(src_path)
    except Exception as exc:  # noqa: BLE001 —— 读不出文字就别猜「干净」
        ctx.fail(display, "-", f"检测失败：{exc}")
        return 0

    if hits == 0:
        ctx.skip(display, 0, "不含敏感信息（命中 0 处）")
        return 0

    try:
        out_path = _process_file(src_path, target_dir, kind, ctx)
    except _Unsupported as exc:
        ctx.fail(display, hits, str(exc))
        return 0
    except Exception as exc:  # noqa: BLE001 —— 单个文件失败不能拖垮整批
        ctx.fail(display, hits, f"处理失败：{exc}")
        return 0

    if not out_path:
        ctx.fail(display, hits, "处理函数没有产出文件")
        return 0

    rel_dir = rel.parent
    dir_part = "" if str(rel_dir) in (".", "") else rel_dir.as_posix().rstrip("/") + "/"
    ctx.done(display, hits, kind, f"{out_label}{dir_part}{Path(out_path).name}")
    return 1


def _process_file(src_path: Path, target_dir: Path, kind: str, ctx: _Context) -> Path | None:
    """按类型分派到现有处理函数；xlsx / 图片走并行开发中的模块。"""
    target_dir.mkdir(parents=True, exist_ok=True)
    suffix = src_path.suffix.lower()

    if kind in ("xlsx", "image"):
        if kind == "xlsx":
            module_name, func_name, kind_cn = "xlsxio", "redact_xlsx", "xlsx"
        else:
            module_name, func_name, kind_cn = "imgio", "redact_image", "图片"
        stem = ctx.unique_stem(target_dir, src_path.stem)
        # 两个模块的约定都是「源路径 + 目标文件路径 + Redactor」
        dst = target_dir / f"{stem}.脱敏{suffix}"
        return _run_optional_hook(module_name, func_name, kind_cn,
                                  src_path, dst, ctx)

    # docx / pdf / txt / md / html：复用单文件处理（内部会写对照.txt / 映射表.json）
    from .api import desensitize_file

    name = ctx.unique_stem(target_dir, src_path.stem)
    result = desensitize_file(src_path, target_dir, mode=ctx.style["mode"],
                              mask_style=ctx.style["mask_style"], name=name)
    return Path(result["out"])


def _run_optional_hook(module_name: str, func_name: str, kind_cn: str,
                       src_path: Path, dst_path: Path, ctx: _Context) -> Path | None:
    """调用还没就绪的 xlsxio / imgio：模块缺失时记「该类型暂不支持」而不是崩掉。"""
    module, load_error = _load_optional_module(module_name)
    if module is None:
        detail = f"（{load_error}）" if load_error else ""
        raise _Unsupported(
            f"该类型暂不支持：{kind_cn} 处理模块 legalredact/{module_name}.py 还没就绪{detail}"
        )
    fn = getattr(module, func_name, None)
    if not callable(fn):
        raise _Unsupported(
            f"该类型暂不支持：legalredact/{module_name}.py 里没有可用的 {func_name}()"
        )

    before = {p.name for p in dst_path.parent.iterdir() if p.is_file()}
    redactor = ctx.new_redactor()
    result = _call_flexibly(fn, src_path, dst_path, redactor)
    return _resolve_hook_output(result, dst_path, before)


def _handle_zip(zip_path: Path, target_dir: Path, display_name: str,
                out_label: str, ctx: _Context, depth: int) -> int:
    """解压 → 递归处理 → 重新打包成 <原名>.脱敏.zip；返回 1/0。"""
    display_name = str(display_name)
    if depth > MAX_ZIP_DEPTH:
        ctx.skip(display_name, "-", f"嵌套压缩包超过 {MAX_ZIP_DEPTH} 层，未展开")
        return 0

    target_dir.mkdir(parents=True, exist_ok=True)
    if _already_output(target_dir, zip_path.stem):
        ctx.skip(display_name, "-", "已处理过（输出已存在）")
        return 0

    try:
        with tempfile.TemporaryDirectory(prefix="legalredact_batch_") as tmp:
            src_root = Path(tmp) / "源文件"
            out_root = Path(tmp) / "已脱敏"
            _extract_zip(zip_path, src_root)

            inner_out_label = f"{out_label}{zip_path.stem}.脱敏.zip/"
            produced = _walk(src_root, out_root, ctx, f"{display_name}/",
                             inner_out_label, depth, excluded=None)
            if produced == 0:
                # 包里没有需要处理的文件：不产出空压缩包
                ctx.skip(display_name, "-", "压缩包内没有需要脱敏的文件")
                return 0

            out_zip = target_dir / f"{zip_path.stem}.脱敏.zip"
            _write_zip(out_root, out_zip)
            return 1
    except zipfile.BadZipFile as exc:
        ctx.fail(display_name, "-", f"压缩包损坏，无法解压：{exc}")
        return 0
    except Exception as exc:  # noqa: BLE001 —— 一个压缩包出错不能拖垮整批
        ctx.fail(display_name, "-", f"压缩包处理失败：{exc}")
        return 0


# ======================================================================
# 压缩包 / 路径 / 调用适配
# ======================================================================

def _zip_member_name(info: zipfile.ZipInfo) -> str:
    """还原 zip 里的文件名：带 UTF-8 标志位就直接用，否则从 cp437 转回中文编码。"""
    name = info.filename
    if info.flag_bits & 0x800:          # 0x800 = UTF-8 文件名标志
        return name.replace("\\", "/")
    try:
        raw = name.encode("cp437")      # zipfile 没标志位时按 cp437 解码
    except UnicodeEncodeError:
        return name.replace("\\", "/")
    for encoding in ("utf-8", "gb18030"):
        try:
            return raw.decode(encoding).replace("\\", "/")
        except UnicodeDecodeError:
            continue
    return name.replace("\\", "/")


def _extract_zip(zip_path: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            target = _safe_join(dest, _zip_member_name(info))
            if target is None:
                continue                    # zip slip：危险路径直接丢掉
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as fh:
                shutil.copyfileobj(src, fh)


def _write_zip(src_dir: Path, zip_path: Path) -> None:
    """把处理结果重新打包；中文文件名由 Python 自带 UTF-8 标志位。

    对照.txt / 映射表.json 里有原文，只留在本机复核用，不塞进交付压缩包。
    """
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for child in sorted(Path(src_dir).rglob("*")):
            if not child.is_file() or _is_review_artifact(child.name):
                continue
            zf.write(child, child.relative_to(src_dir).as_posix())


def _safe_join(root: Path, name: str) -> Path | None:
    parts = Path(name).parts
    if Path(name).is_absolute() or ".." in parts:
        return None
    clean = [p for p in parts if p not in ("", ".")]
    if not clean:
        return None
    return root.joinpath(*clean)


def _load_optional_module(name: str):
    """延迟导入 legalredact/<name>.py：返回 (模块或 None, 错误说明)。"""
    try:
        return importlib.import_module(f".{name}", __package__), None
    except ImportError as exc:
        return None, str(exc)


def _call_flexibly(fn, src_path: Path, dst_path: Path, redactor):
    """调用 xlsxio / imgio 的处理函数，签名对不上时兜底重试。

    两位同事的约定是 (src_path, dst_path, redactor)，但模块还在并行开发，
    所以先按参数名把三种角色填进去，再兜底试几种常见顺序。
    只对「参数个数/名字不对」的 TypeError 重试；函数体内部抛的 TypeError 原样上抛，
    避免重复执行同一个处理函数。
    """
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        sig = None

    if sig is not None:
        pool = {
            "path": src_path, "src": src_path, "src_path": src_path,
            "source": src_path, "file": src_path, "file_path": src_path,
            "in_path": src_path, "input_path": src_path, "filename": src_path,
            "dst": dst_path, "dst_path": dst_path, "dest": dst_path,
            "target": dst_path, "out": dst_path, "out_path": dst_path,
            "output": dst_path, "output_path": dst_path,
            "out_dir": dst_path.parent, "outdir": dst_path.parent,
            "output_dir": dst_path.parent,
            "redactor": redactor, "rb": redactor, "engine": redactor, "red": redactor,
        }
        kwargs: dict = {}
        complete = True
        for pname, param in sig.parameters.items():
            if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
                continue
            if pname in pool:
                kwargs[pname] = pool[pname]
            elif param.default is param.empty:
                complete = False
                break
        if complete:
            try:
                return fn(**kwargs)
            except TypeError as exc:
                if "argument" not in str(exc):
                    raise

    last_error: TypeError | None = None
    attempts = (
        (src_path, dst_path, redactor),
        (src_path, redactor, dst_path),
        (src_path, dst_path),
        (src_path, redactor),
        (src_path,),
    )
    for args in attempts:
        try:
            return fn(*args)
        except TypeError as exc:
            if "argument" not in str(exc):
                raise
            last_error = exc
    raise _Unsupported(f"无法调用处理函数（签名不匹配）：{last_error}")


def _resolve_hook_output(result, dst_path: Path, before: set[str]) -> Path | None:
    """从处理函数的返回值 / 目标目录里的新增文件找出输出路径。"""
    if isinstance(result, dict):
        for key in ("out", "path", "output", "out_path", "saved"):
            value = result.get(key)
            if value:
                return Path(value)
    elif isinstance(result, (str, Path)):
        return Path(result)
    if dst_path.exists():
        return dst_path

    after = [p for p in dst_path.parent.iterdir() if p.is_file() and p.name not in before]
    marked = [p for p in after if "脱敏" in p.name]
    pool = marked or after
    same_ext = [p for p in pool if p.suffix.lower() == dst_path.suffix.lower()]
    pool = same_ext or pool
    if not pool:
        return None
    return pool[0] if len(pool) == 1 else sorted(pool)[0]


# ======================================================================
# 小工具
# ======================================================================

def _is_review_artifact(name: str) -> bool:
    """对照.txt / 映射表.json 这类复核产物（里面有原文）。"""
    return any(name.endswith(suffix) for suffix in REVIEW_SUFFIXES)


def _is_processed_name(name: str) -> bool:
    """文件名里带「脱敏」或是复核产物 = 已经处理过，别再动一次。"""
    return "脱敏" in name or _is_review_artifact(name)


def _already_output(target_dir: Path, stem: str) -> bool:
    """输出目录里已经存在 <stem>.脱敏* 说明这个文件处理过（重复运行保护）。"""
    target_dir = Path(target_dir)
    if not target_dir.exists():
        return False
    try:
        return any(p.is_file() and p.name.startswith(f"{stem}.脱敏")
                   for p in target_dir.iterdir())
    except OSError:
        return False


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root)
        return True
    except (ValueError, OSError):
        return False


def _label(prefix: str, path: Path, root: Path) -> str:
    """清单里显示的文件名：普通文件夹用相对路径，压缩包内用「包名/内部路径」。"""
    return f"{prefix}{path.relative_to(root).as_posix()}"


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _hits_cell(hits) -> int | str:
    """report.csv 里没检测的数字别写成 0，容易让人以为「干净」。"""
    return hits if isinstance(hits, int) else "-"


def _write_report(out_dir: Path, rows: list[dict]) -> Path:
    """report.csv：UTF-8 BOM，Excel 双击打开不乱码。"""
    path = Path(out_dir) / "report.csv"
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=REPORT_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return path

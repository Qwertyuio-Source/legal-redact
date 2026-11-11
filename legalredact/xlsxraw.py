# -*- coding: utf-8 -*-
"""xlsx 裸 XML 脱敏：只改 sharedStrings.xml 和单元格里的 inline 字符串，
其它部件（图片 / 图表 / 透视表 / 样式 / 公式 / 数字格式 …）原样拷贝。

openpyxl 重存会丢图片、图表、透视表，所以正式使用走这条路；xlsxio.py 的
openpyxl 版留作兜底（见 redact_xlsx_auto，raw 版解析失败时退回去）。
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path

from . import data, detectors, lexicon, types as T

# 这些正则必须和 web/src/xlsx.js、web/src/xlsxwrite.js 里的口径保持一致
CELL_RE = re.compile(r"<c\b([^>]*?)(?:/>|>(.*?)</c>)", re.S)
SI_RE = re.compile(r"<si\b[^>]*?(?:/>|>(.*?)</si>)", re.S)
IS_RE = re.compile(r"<is\b([^>]*)>(.*?)</is>", re.S)
T_RE = re.compile(r"<t\b[^>]*>(.*?)</t>", re.S)
RUN_RE = re.compile(r"<r\b[^>]*>.*?</r>", re.S)
RPR_RE = re.compile(r"<rPr\s*/>|<rPr>.*?</rPr>", re.S)
RPH_RE = re.compile(r"<rPh\b.*?</rPh>", re.S)
SHEET_RE = re.compile(r"<sheet\b[^>]*?(?:/>|>)")
REL_RE = re.compile(r"<Relationship\b[^>]*?(?:/>|>)")
V_RE = re.compile(r"<v\b[^>]*>(.*?)</v>", re.S)
NUMREF_RE = re.compile(r"&#(\d+);|&#x([0-9a-fA-F]+);")


# ---------- XML 小工具 ----------

def _unescape(s: str) -> str:
    s = NUMREF_RE.sub(
        lambda m: chr(int(m.group(1))) if m.group(1) else chr(int(m.group(2), 16)), s)
    return (s.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
             .replace("&apos;", "'").replace("&amp;", "&"))


def _escape(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _attr(tag: str, name: str) -> str:
    """取标签属性：前面垫个空格，免得 r="A1" 匹配到 sr="..." 里面去。"""
    m = re.search(r"(?:^|\s)" + re.escape(name) + r'\s*=\s*"([^"]*)"', " " + str(tag or ""))
    return _unescape(m.group(1)) if m else ""


def _container_text(inner: str | None) -> str:
    """<si> / <is> 里的可见文字：所有 <t> 拼起来，跳过拼音标注 <rPh>。"""
    if not inner:
        return ""
    return "".join(_unescape(t) for t in T_RE.findall(RPH_RE.sub("", inner)))


def _container_body(inner: str | None, text: str) -> str:
    """用新文字重建 <si>/<is> 的内容：普通串一个 <t>；富文本保住首段的字体。"""
    body = f'<t xml:space="preserve">{_escape(text)}</t>'
    runs = RUN_RE.findall(inner or "")
    if not runs:
        return body
    pr = RPR_RE.search(runs[0])
    return f"<r>{pr.group(0) if pr else ''}{body}</r>"


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("这个 xlsx 的 XML 不是 UTF-8，改用 openpyxl 兜底版") from None


def _encode(xml: str) -> bytes:
    return xml.encode("utf-8")


# ---------- 单元格级启发（只在 xlsx 通道用，不动全局检测器） ----------
# 表格里的「一格就是一个值」没有上下文（没有「原告张三」「住北京市…」这种锚点），
# 检测器会漏。所以这里对「整格只有一个值」的情况补一层保守的判断，只在本通道生效。

# 地址尾巴：要求至少命中一个才算地址
ADDR_MARKS = set("省市区县镇路街巷号栋室楼")
# 表头 / 通用词：命中就绝不按人名、地址处理
HEADER_WORDS = {
    "姓名", "名字", "名称", "姓名/名称", "身份证", "身份证号", "身份证号码", "证件号",
    "证件号码", "电话", "手机", "手机号", "手机号码", "联系电话", "联系方式", "座机",
    "住址", "地址", "现住址", "户籍地址", "通讯地址", "单位", "工作单位", "单位名称",
    "公司", "公司名称", "部门", "序号", "编号", "案号", "备注", "性别", "民族",
    "出生日期", "出生年月", "年龄", "籍贯", "职务", "职业", "当事人", "合计", "总计",
}
# 词库里缺的几个常用名用字，只在 xlsx 通道补上（不动 data.py）
NAME_EXTRA_CHARS = set("涵桐梦玥丰")


def _is_han(ch: str) -> bool:
    return "\u4e00" <= ch <= "\u9fff"


def _all_han(tok: str) -> bool:
    return bool(tok) and all(_is_han(ch) for ch in tok)


def _han_ratio(tok: str) -> float:
    if not tok:
        return 0.0
    return sum(1 for ch in tok if _is_han(ch)) / len(tok)


def _name_chars_ok(tok: str) -> bool:
    """姓之后的部分是不是常用名用字（词库 + 本通道补的 5 个字）。"""
    if detectors.plausible_name(tok):
        return True
    rest = tok[2:] if tok[:2] in lexicon.surnames()[1] else tok[1:]
    given = lexicon.given_chars()
    return bool(rest) and all(ch in given or ch in NAME_EXTRA_CHARS for ch in rest)


def _preserved_whole(tok: str) -> bool:
    """整格是法院/案号/法条这类必须保留的东西。"""
    if T.find_preserve_spans(tok):
        return True
    return any(p in tok for p in data.ORG_KEEP_PATTERNS)


def cell_guess(text: str) -> tuple[str, str, int] | None:
    """整格文本猜类型：返回 (kind, 要替换的正文, 正文在原文里的起点)；猜不出返回 None。

    · 2~4 个汉字、首字是姓、其余是常用名用字 → 人名
    · 含 省/市/区/县/镇/路/街/巷/号/栋/室/楼 且长度 ≥ 6、汉字占一半以上 → 地址
    """
    body = (text or "").strip()
    if not body or body in HEADER_WORDS:
        return None
    start = len(text or "") - len((text or "").lstrip())

    # 1）整格人名
    if 2 <= len(body) <= 4 and _all_han(body) \
            and detectors.is_valid_name(body) and _name_chars_ok(body):
        return T.PERSON, body, start

    # 2）整格地址（法院/案号这类保留项不碰）
    if len(body) >= 6 and any(ch in ADDR_MARKS for ch in body) \
            and _han_ratio(body) >= 0.5 and not _preserved_whole(body):
        return T.ADDRESS, body, start

    return None


def redact_sheet_texts(redactor, texts: list[str]) -> list:
    """xlsx 单元格专用：先交给引擎 prepare，再给引擎没认出来的「整格值」补一刀。

    补出来的实体同样走 renderer，所以同一个「张伟」和其它地方仍是同一个替身。
    """
    results = redactor.prepare(texts)
    for i, text in enumerate(texts):
        br = results[i]
        if br.spans:                        # 引擎已经认出来了，不重复处理
            continue
        guess = cell_guess(text)
        if not guess:
            continue
        kind, body, start = guess
        if kind in redactor.disabled:       # 配置里关掉的类型不补
            continue
        rec = redactor.renderer.assign(kind, body, context=text, pos=start)
        spans = [(start, start + len(body), rec["replacement"], kind, rec["entity_id"])]
        br.spans = spans
        br.redacted = text[:start] + rec["replacement"] + text[start + len(body):]
        br.pos_spans = [[start, start + len(rec["replacement"]), kind, body]]
        redactor.rows.append({
            "block": i + 1, "type": kind, "entity_id": rec["entity_id"],
            "original": body, "replacement": rec["replacement"],
            "rule": "cell-guess", "start": start,
        })
    # 和 engine.prepare 同一口径：块跨度按这次的结果重算
    redactor.block_spans = [{"index": i, "spans": br.pos_spans}
                            for i, br in enumerate(results) if br.pos_spans]
    return results


# ---------- 包结构 ----------

def _norm_path(p: str) -> str:
    parts: list[str] = []
    for seg in p.replace("\\", "/").split("/"):
        if not seg or seg == ".":
            continue
        if seg == "..":
            if parts:
                parts.pop()
            continue
        parts.append(seg)
    return "/".join(parts)


def _resolve(target: str, base: str) -> str:
    """zip 里的 Target 都是相对 xl/ 目录的。"""
    t = (target or "").replace("\\", "/")
    return _norm_path(t if t.startswith("/") else base + "/" + t)


def _parse_rels(xml: str) -> dict[str, tuple[str, str]]:
    out: dict[str, tuple[str, str]] = {}
    for tag in REL_RE.findall(xml):
        rid = _attr(tag, "Id")
        if rid:
            out[rid] = (_attr(tag, "Type"), _attr(tag, "Target"))
    return out


def _parse_shared(xml: str) -> list[str]:
    """sharedStrings.xml → ["字符串", ...]，顺序就是 t="s" 引用的序号。"""
    texts = [_container_text(inner) for inner in SI_RE.findall(xml)]
    total = len(re.findall(r"<si\b", xml))
    if len(texts) != total:
        raise ValueError(f"sharedStrings.xml 解析不对（{len(texts)}/{total} 条）")
    return texts


def _parse_sheets(wb_xml: str, rels: dict[str, tuple[str, str]],
                  names: set[str]) -> list[dict]:
    out: list[dict] = []
    for tag in SHEET_RE.findall(wb_xml):
        rid = _attr(tag, "r:id")
        if not rid:                                   # 万一关系前缀不叫 r
            alt = re.search(r'\s[\w.-]+:id\s*=\s*"([^"]*)"', tag)
            rid = _unescape(alt.group(1)) if alt else ""
        rel = rels.get(rid) if rid else None
        if not rel:
            continue
        rtype, target = rel
        if rtype and not rtype.endswith("/worksheet"):
            continue                                  # 图表页之类跳过
        path = _resolve(target, "xl")
        if path not in names:
            continue
        out.append({"name": _attr(tag, "name"), "path": path})
    return out


def _ref_to_rc(ref: str) -> tuple[int, int]:
    m = re.match(r"^([A-Z]+)(\d+)$", str(ref or "").upper())
    if not m:
        return 0, 0
    col = 0
    for ch in m.group(1):
        col = col * 26 + (ord(ch) - 64)
    return int(m.group(2)), col


# ---------- 单元格 ----------

def _collect_cells(xml: str, shared: list[str]) -> list[dict]:
    """扫一个 sheet 的文字单元格；数字/日期/布尔/公式结果一律不返回（也就不会改）。"""
    out: list[dict] = []
    for m in CELL_RE.finditer(xml):
        attrs, inner = m.group(1) or "", m.group(2)
        if not inner:
            continue
        ref = _attr(attrs, "r")
        if not ref:
            continue
        type_ = _attr(attrs, "t")
        index = -1
        if type_ == "s":
            v = V_RE.search(inner)
            if not v:
                continue
            index = int(v.group(1))
            text = shared[index] if 0 <= index < len(shared) else ""
            kind = "shared"
        elif type_ == "inlineStr":
            is_ = IS_RE.search(inner)
            if not is_:
                continue
            text = _container_text(is_.group(2))
            kind = "inline"
        else:
            continue
        if not text.strip():
            continue
        row, col = _ref_to_rc(ref)
        out.append({"ref": ref, "text": text, "kind": kind, "index": index,
                    "row": row, "col": col})
    return out


def _rewrite_shared_strings(xml: str, updates: dict[int, str]) -> str:
    """原地换文字、条目不增不减，所以各 sheet 里的 t="s" 索引全都不用动。"""
    if not updates:
        return xml
    counter = iter(range(len(updates) + 10_000_000))   # 只需要一个递增序号

    def repl(m: re.Match) -> str:
        idx = next(counter)
        if idx not in updates:
            return m.group(0)
        node = m.group(0)
        open_tag = node[:node.index(">") + 1]
        attrs = re.sub(r"^<si", "", open_tag)
        attrs = re.sub(r"/?>$", "", attrs).strip()
        body = _container_body(m.group(1), updates[idx])
        return f"<si{' ' + attrs if attrs else ''}>{body}</si>"

    return SI_RE.sub(repl, xml)


def _rewrite_inline_cells(xml: str, updates: dict[str, str]) -> str:
    if not updates:
        return xml

    def repl(m: re.Match) -> str:
        attrs, inner = m.group(1) or "", m.group(2)
        if not inner:
            return m.group(0)
        ref = _attr(attrs, "r")
        if not ref or _attr(attrs, "t") != "inlineStr" or ref not in updates:
            return m.group(0)
        is_ = IS_RE.search(inner)
        if not is_:
            return m.group(0)
        body = _container_body(is_.group(2), updates[ref])
        new_is = f"<is{is_.group(1)}>{body}</is>"
        return "<c" + attrs + ">" + inner[:is_.start()] + new_is + inner[is_.end():] + "</c>"

    return CELL_RE.sub(repl, xml)


# ---------- 主流程 ----------

def _scrub_orphans(redactor, shared: list[str], used: set[int]) -> dict[int, str]:
    """孤儿共享串（没有任何格子引用）里也可能留着原文，清一遍：先用已认定的替身，
    再让引擎认独立可识别的 PII，最后按整格启发兜一次。"""
    known = sorted(redactor.renderer.by_key.items(), key=lambda kv: -len(kv[0][1]))
    updates: dict[int, str] = {}
    for idx, text in enumerate(shared):
        if idx in used or not text.strip():
            continue
        new = text
        for (_kind, orig), rec in known:
            if orig in new:
                new = new.replace(orig, rec["replacement"])
        for sp in sorted(detectors.detect(new), key=lambda s: -s.start):
            if sp.kind in redactor.disabled:
                continue
            rec = redactor.renderer.assign(sp.kind, sp.text, context=new,
                                           pos=sp.start, sub=sp.sub)
            new = new[:sp.start] + rec["replacement"] + new[sp.end:]
        guess = cell_guess(new)
        if guess:
            kind, body, start = guess
            if kind not in redactor.disabled:
                rec = redactor.renderer.assign(kind, body, context=new, pos=start)
                new = new[:start] + rec["replacement"] + new[start + len(body):]
        if new != text:
            updates[idx] = new
    return updates


def redact_xlsx_raw(src_path, dst_path, redactor) -> dict:
    """裸 XML 版：只重写文字所在的 XML，其它部件内容原样拷贝。

    返回 {"shapes": [...], "cells": [(sheet, row, col, before, after), ...]}
    口径与 xlsxio.redact_xlsx 完全一致（一个 sheet 一次 prepare，跨表同一实体同一替身）。
    """
    src, dst = Path(src_path), Path(dst_path)
    with zipfile.ZipFile(src) as zin:
        infos = zin.infolist()
        files = {info.filename: zin.read(info) for info in infos}

    wb_bytes = files.get("xl/workbook.xml")
    if wb_bytes is None:
        raise ValueError("这个文件里找不到 xl/workbook.xml，可能不是 xlsx（.xls 请先另存为 xlsx）")
    wb_xml = _decode(wb_bytes)

    rels = _parse_rels(_decode(files.get("xl/_rels/workbook.xml.rels", b"")))

    shared_path = ""
    for rtype, target in rels.values():
        if rtype and rtype.endswith("/sharedStrings"):
            shared_path = _resolve(target, "xl")
            break
    if not shared_path or shared_path not in files:
        shared_path = "xl/sharedStrings.xml" if "xl/sharedStrings.xml" in files else ""
    shared = _parse_shared(_decode(files[shared_path])) if shared_path else []

    sheets = _parse_sheets(wb_xml, rels, set(files))
    if not sheets:
        raise ValueError("这个 xlsx 里找不到工作表，可能文件损坏")

    shapes: list[dict] = []
    changed: list[tuple] = []
    shared_updates: dict[int, str] = {}
    used_shared: set[int] = set()
    sheet_updates: dict[str, str] = {}

    for sheet in sheets:
        xml = _decode(files[sheet["path"]])
        entries = _collect_cells(xml, shared)
        if not entries:
            continue
        for e in entries:
            if e["kind"] == "shared":
                used_shared.add(e["index"])

        # 一个 sheet 的文字一次性交给 prepare：同表内实体口径统一
        # （整格值再走 xlsx 通道自己的启发，见 redact_sheet_texts）
        results = redact_sheet_texts(redactor, [e["text"] for e in entries])
        inline_updates: dict[str, str] = {}
        for e, br in zip(entries, results):
            if not br.spans:                     # 没命中就不动
                continue
            if e["kind"] == "shared":
                shared_updates[e["index"]] = br.redacted
            else:
                inline_updates[e["ref"]] = br.redacted
            changed.append((sheet["name"], e["row"], e["col"], e["text"], br.redacted))
            for start, end, rep, kind, eid in br.spans:
                shapes.append({
                    "sheet": sheet["name"], "ref": e["ref"],
                    "row": e["row"], "col": e["col"],
                    "start": start, "end": end,
                    "original": e["text"][start:end], "replacement": rep,
                    "kind": kind, "entity_id": eid,
                })

        if inline_updates:
            sheet_updates[sheet["path"]] = _rewrite_inline_cells(xml, inline_updates)

    # 没被任何单元格引用的共享串也可能留着敏感原文，顺手清掉（防漏）
    shared_updates.update(_scrub_orphans(redactor, shared, used_shared))

    if shared_updates:
        files[shared_path] = _encode(
            _rewrite_shared_strings(_decode(files[shared_path]), shared_updates))
    for path, xml in sheet_updates.items():
        files[path] = _encode(xml)

    if dst.parent and not dst.parent.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
    # 其它部件按原条目的压缩参数原样写回（内容一个字节没变）
    with zipfile.ZipFile(dst, "w") as zout:
        for info in infos:
            zout.writestr(info, files[info.filename])

    return {"shapes": shapes, "cells": changed}


def redact_xlsx_auto(src_path, dst_path, redactor) -> dict:
    """推荐入口：先走裸 XML（保图片/图表），失败再退回 openpyxl 版。

    返回值多一个 "fallback" 键（只有回退时才出现），内容是回退原因。
    """
    rows, issues, spans = len(redactor.rows), len(redactor.issues), len(redactor.block_spans)
    try:
        return redact_xlsx_raw(src_path, dst_path, redactor)
    except Exception as exc:
        # raw 版中途失败：把它记的账撤掉，别让报告里出现重复行
        del redactor.rows[rows:]
        del redactor.issues[issues:]
        del redactor.block_spans[spans:]
        try:
            from .xlsxio import redact_xlsx
        except ImportError as no_dep:      # 没装 openpyxl 就没法兜底，报 raw 的真实原因
            raise RuntimeError(f"raw 版失败（{exc}），而且没有 openpyxl 可以兜底：{no_dep}") from exc
        result = redact_xlsx(src_path, dst_path, redactor)
        result["fallback"] = f"raw 版失败，已退回 openpyxl：{exc}"
        return result

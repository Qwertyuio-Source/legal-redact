# -*- coding: utf-8 -*-
"""xlsx 脱敏验收：文本替换、共享串/inline 串、合并单元格、样式与公式不能被弄坏。

运行：python tests/test_xlsx.py
"""
import re
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openpyxl import Workbook, load_workbook          # noqa: E402
from openpyxl.styles import Font, PatternFill        # noqa: E402

from legalredact import xlsxraw                      # noqa: E402
from legalredact.engine import Redactor              # noqa: E402
from legalredact.xlsxio import read_xlsx, redact_xlsx  # noqa: E402
from legalredact.xlsxraw import redact_xlsx_auto, redact_xlsx_raw  # noqa: E402

for _stream in (sys.stdout, sys.stderr):             # Windows 管道下也能打出 ✅
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

PASS, FAIL = "✓", "✗"
fails = 0


def check(name, cond, detail=""):
    global fails
    print(f"  {PASS if cond else FAIL} {name}{('  ' + detail) if detail else ''}")
    if not cond:
        fails += 1


# ---------- 造假样本 ----------

DATE_CELL = "A6"
FORMULA_CELL = "A7"
MERGED = "B1:C1"

S1 = {
    "A1": "民事判决书",
    "A2": "原告：张伟，男，1985年3月12日出生",
    "A3": "住北京市朝阳区建国路88号院3号楼1201室",
    "A4": "身份证号110105198503121234",
    "A8": "备注：A&B <测试> 张伟",
    "A9": "第一行\n第二行\n第三行",
    "B1": "合并单元格里的张伟",
}
S2 = {
    "A1": "当事人张伟，联系电话13812345678。",
    "A2": "被告：北京鼎盛科技有限公司",
    "B2": "张伟",                       # 单独一个名字，靠同表其它单元格带出来
}


def make_base(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "首页"
    for ref, text in S1.items():
        ws[ref] = text
    ws["A2"].font = Font(bold=True, color="FFFF0000", size=14)
    ws["A2"].fill = PatternFill("solid", fgColor="FFFFF2CC")
    ws["A5"] = 123
    ws["B5"] = 3.5
    ws[DATE_CELL] = datetime(2025, 3, 5)
    ws[DATE_CELL].number_format = "yyyy-mm-dd"
    ws[FORMULA_CELL] = "=SUM(A5,B5)"
    ws.merge_cells(MERGED)
    ws.column_dimensions["A"].width = 33
    ws.column_dimensions["B"].width = 12
    ws.column_dimensions["C"].width = 8

    ws2 = wb.create_sheet("附页")
    for ref, text in S2.items():
        ws2[ref] = text
    ws2["A3"] = 42

    ws3 = wb.create_sheet("空表")          # 没有文本单元格，不能崩
    ws3["A1"] = 1

    wb.save(str(path))
    wb.close()


_NUMREF = re.compile(r"&#(\d+);|&#x([0-9a-fA-F]+);")


def _unesc(s: str) -> str:
    s = _NUMREF.sub(lambda m: chr(int(m.group(1))) if m.group(1) else chr(int(m.group(2), 16)), s)
    return (s.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
             .replace("&apos;", "'").replace("&amp;", "&"))


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _join_t(inner: str) -> str:
    """把 <is>/<si> 里的所有 <t> 拼起来（跳过拼音标注 rPh）。"""
    inner = re.sub(r"<rPh\b.*?</rPh>", "", inner, flags=re.S)
    return "".join(_unesc(x) for x in re.findall(r"<t\b[^>]*>(.*?)</t>", inner, flags=re.S))


CELL_RE = re.compile(r"<c\b[^>]*?(?:/>|>.*?</c>)", re.S)


def to_shared_strings(src: Path, dst: Path, keep_inline=()) -> int:
    """把 openpyxl 写出的 inlineStr 改成共享字符串（keep_inline 里的留成 inline，测混合）。

    openpyxl 自己不用 sharedStrings，所以这里手工造一个真的共享串表出来。
    返回共享串条数。
    """
    with zipfile.ZipFile(src) as z:
        names = [i.filename for i in z.infolist()]
        files = {n: z.read(n) for n in names}

    shared: list[str] = []
    index: dict[str, int] = {}

    for name in names:
        if not (name.startswith("xl/worksheets/") and name.endswith(".xml")):
            continue
        xml = files[name].decode("utf-8")

        def repl(m, _keep=keep_inline):
            node = m.group(0)
            ref = re.search(r'\br="([A-Z]+\d+)"', node)
            kind = re.search(r'\bt="([^"]*)"', node)
            if not ref or not kind or kind.group(1) != "inlineStr" or ref.group(1) in _keep:
                return node
            inner = re.search(r"<is>(.*?)</is>", node, re.S)
            if not inner:
                return node
            text = _join_t(inner.group(1))
            if text not in index:
                index[text] = len(shared)
                shared.append(text)
            head = re.sub(r'\s+t="[^"]*"', "", node[:node.index(">")]).replace("<c", "", 1).strip()
            return f'<c {head} t="s"><v>{index[text]}</v></c>'

        files[name] = re.sub(CELL_RE, repl, xml).encode("utf-8")

    n = len(shared)
    sst = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
           '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
           f'count="{n}" uniqueCount="{n}">')
    sst += "".join(f'<si><t xml:space="preserve">{_esc(t)}</t></si>' for t in shared)
    sst += "</sst>"
    files["xl/sharedStrings.xml"] = sst.encode("utf-8")

    ct = files["[Content_Types].xml"].decode("utf-8")
    files["[Content_Types].xml"] = ct.replace(
        "</Types>",
        '<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-'
        'officedocument.spreadsheetml.sharedStrings+xml"/></Types>').encode("utf-8")
    rels = files["xl/_rels/workbook.xml.rels"].decode("utf-8")
    files["xl/_rels/workbook.xml.rels"] = rels.replace(
        "</Relationships>",
        '<Relationship Id="rIdSS" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/sharedStrings" Target="sharedStrings.xml"/></Relationships>').encode("utf-8")

    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as out:
        for name in names + ["xl/sharedStrings.xml"]:
            out.writestr(name, files[name])
    return n


# ---------- 检查项 ----------

def _all_texts(path: Path) -> list[str]:
    return [t for s in read_xlsx(path) for (_r, _c, t) in s["cells"]]


def _nontext_snapshot(wb) -> dict:
    """非文本单元格快照：值 + 数字格式 + 字体，用来证明它们一个都没被碰。"""
    snap = {}
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                if isinstance(c.value, str) and c.data_type == "s":
                    continue
                snap[(ws.title, c.coordinate)] = (repr(c.value), c.number_format,
                                                  c.font.b, c.font.sz)
    return snap


def check_read(tag: str, src: Path) -> None:
    print(f"\n[{tag}] 读取 {src.name}")
    sheets = read_xlsx(src)
    check("sheet 顺序/名字", [s["name"] for s in sheets] == ["首页", "附页", "空表"],
          str([s["name"] for s in sheets]))
    check("空表没有文本单元格", sheets[2]["cells"] == [])
    all_cells = [(r, c) for s in sheets for (r, c, _t) in s["cells"]]
    check("cells 是 (行, 列) 整数", all(isinstance(r, int) and isinstance(c, int) for r, c in all_cells))

    texts = _all_texts(src)
    check("读到普通文本", "民事判决书" in texts)
    check("读到共享/inline 字符串", "原告：张伟，男，1985年3月12日出生" in texts
          and "当事人张伟，联系电话13812345678。" in texts)
    check("公式不当文本（=SUM 不在里面）", not any(t.startswith("=") for t in texts))
    check("数字/日期不当文本", "123" not in texts and "42" not in texts
          and not any("2025-03-05" in t for t in texts))


def check_redact(tag: str, src: Path, tmp: Path, func=redact_xlsx) -> None:
    print(f"\n[{tag}] 脱敏")
    out = tmp / f"脱敏-{tag.replace('·', '-')}.xlsx"
    red = Redactor({}, mode="mask")
    res = func(src, out, red)

    check("返回只有 shapes/cells", set(res) == {"shapes", "cells"})
    check("shapes 有内容", bool(res["shapes"]),
          f"{len(res['shapes'])} 段")
    check("cells 是 (sheet, row, col, before, after)",
          bool(res["cells"]) and all(isinstance(x, tuple) and len(x) == 5 for x in res["cells"])
          and {x[0] for x in res["cells"]} == {"首页", "附页"})
    shape = res["shapes"][0]
    check("shape 带坐标和替换前后", {"sheet", "ref", "original", "replacement"} <= set(shape))

    # 同一实体 → 同一替身（跨 sheet）
    zhang = [r for r in red.rows if r["original"] == "张伟"]
    repl = {r["replacement"] for r in zhang}
    check("张伟出现 5 次（跨两个 sheet）", len(zhang) == 5, str(len(zhang)))
    check("张伟只有一个替身", len(repl) == 1 and zhang[0]["replacement"] != "张伟",
          str(repl))
    check("张伟的 entity_id 也只有一个", len({r["entity_id"] for r in zhang}) == 1)

    texts = _all_texts(out)
    joined = "\n".join(texts)
    for word in ("张伟", "110105198503121234", "13812345678", "建国路88号院", "北京鼎盛科技有限公司"):
        check(f"脱敏后查不到「{word}」", word not in joined)
    check("替身写进去了", "张某某" in joined and "某某某" in joined)
    check("XML 转义字符原样保留（A&B <测试>）", "备注：A&B <测试> 张某某" in texts)
    check("多行单元格原样保留", "第一行\n第二行\n第三行" in texts)

    # 重新打开：格式/值/公式/合并/列宽都得在
    wb = load_workbook(str(out))
    ws = wb["首页"]
    check("字体加粗还在", ws["A2"].font.b is True)
    check("字体颜色还在", ws["A2"].font.color.rgb == "FFFF0000", str(ws["A2"].font.color.rgb))
    check("字体字号还在", ws["A2"].font.sz == 14)
    check("单元格填充还在", ws["A2"].fill.fgColor.rgb == "FFFFF2CC", str(ws["A2"].fill.fgColor.rgb))
    check("数字格式还在", ws[DATE_CELL].number_format == "yyyy-mm-dd", ws[DATE_CELL].number_format)
    check("日期值没变", ws[DATE_CELL].value == datetime(2025, 3, 5), repr(ws[DATE_CELL].value))
    check("整数没变", ws["A5"].value == 123)
    check("小数没变", ws["B5"].value == 3.5)
    check("公式没变", ws[FORMULA_CELL].value == "=SUM(A5,B5)", repr(ws[FORMULA_CELL].value))
    check("合并单元格还在", [str(r) for r in ws.merged_cells.ranges] == [MERGED],
          str([str(r) for r in ws.merged_cells.ranges]))
    check("合并单元格里的字也换了", ws["B1"].value == "合并单元格里的张某某", repr(ws["B1"].value))
    check("单独一格的名字也换了", wb["附页"]["B2"].value == "张某某", repr(wb["附页"]["B2"].value))
    check("列宽还在", round(ws.column_dimensions["A"].width, 2) == 33
          and round(ws.column_dimensions["B"].width, 2) == 12)

    src_wb = load_workbook(str(src))
    check("非文本单元格逐个一致", _nontext_snapshot(src_wb) == _nontext_snapshot(wb))
    src_wb.close()
    wb.close()
    check("输出文件确实生成了", out.exists() and out.stat().st_size > 2000)


def check_clean(tmp: Path, func=redact_xlsx, tag="openpyxl") -> None:
    print(f"\n[{tag}·没有敏感信息] 原样存一份")
    src = tmp / "干净.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "无敏感"
    ws["A1"] = "民事判决书"
    ws["A2"] = 2025
    ws["A3"] = "=A2*2"
    wb.save(str(src))
    wb.close()

    out = tmp / f"干净-脱敏-{tag}.xlsx"
    res = func(src, out, Redactor({}, mode="mask"))
    check("cells / shapes 都是空的", res["cells"] == [] and res["shapes"] == [])
    wb2 = load_workbook(str(out))
    check("文字/数字/公式都还在", wb2["无敏感"]["A1"].value == "民事判决书"
          and wb2["无敏感"]["A2"].value == 2025
          and wb2["无敏感"]["A3"].value == "=A2*2")
    wb2.close()


# ---------- 表格里「一格就是一个值」：整格启发 ----------

LIST_HEADER = ["姓名", "身份证号", "联系电话", "住址", "单位"]
LIST_ROWS = [
    ["张伟", "110105198503121234", "13812345678", "北京市朝阳区建国路88号", "北京鼎盛科技有限公司"],
    ["李娜", "110105198503121234", "13812345678", "成都市武侯区一环路南三段66号", "上海某某商贸有限公司"],
    # 第三行：身份证带 X 结尾（要确认现有规则认得出）
    ["王芳", "11010519850312008X", "13900001111", "上海市浦东新区世纪大道100号", "广州某某咨询有限公司"],
]


def make_list(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "名单"
    ws.append(LIST_HEADER)
    for row in LIST_ROWS:
        ws.append(row)
    wb.save(str(path))
    wb.close()


def check_cell_guess(tmp: Path, func=redact_xlsx_raw, tag="raw") -> None:
    print(f"\n[{tag}·整格值] 姓名 / 地址 / 单位 / 表头")
    src = tmp / "名单.xlsx"
    make_list(src)

    out = tmp / f"名单-脱敏-{tag}.xlsx"
    red = Redactor({}, mode="mask")
    res = func(src, out, red)

    wb = load_workbook(str(out))
    ws = wb["名单"]

    # 表头行一个都不能动
    header = [c.value for c in ws[1]]
    check("表头行原样保留", header == LIST_HEADER, str(header))
    check("表头行没产生脱敏记录",
          not any(r["original"] in ("姓名", "身份证号", "联系电话", "住址", "单位")
                  for r in red.rows))

    # 整格值：姓名 / 地址 / 单位 / 身份证 / 电话
    check("整格姓名换成替身", ws["A2"].value == "张某某", repr(ws["A2"].value))
    check("整格地址换成替身", ws["D2"].value == "某地址", repr(ws["D2"].value))
    check("整格单位名换成替身", str(ws["E2"].value or "").startswith("某某某"), repr(ws["E2"].value))
    check("整格身份证照样命中", "×" in (ws["B2"].value or ""), repr(ws["B2"].value))
    check("整格电话照样命中", "×" in (ws["C2"].value or ""), repr(ws["C2"].value))
    check("第二行整格姓名也换了", ws["A3"].value == "李某某", repr(ws["A3"].value))
    check("第二行整格地址也换了", (ws["D3"].value or "").startswith("某地址"),
          repr(ws["D3"].value))
    check("带 X 的身份证也命中", "×" in (ws["B4"].value or ""), repr(ws["B4"].value))
    check("第三行整格姓名也换了", ws["A4"].value == "王某某", repr(ws["A4"].value))

    # 记账口径：补出来的都标 cell-guess，且同一个名字只有一个替身
    guessed = [r for r in red.rows if r["rule"] == "cell-guess"]
    check("整格启发有记账（rule=cell-guess）", {r["original"] for r in guessed}
          >= {"张伟", "李娜", "北京市朝阳区建国路88号"}, str({r["original"] for r in guessed}))
    zhang = [r for r in red.rows if r["original"] == "张伟"]
    check("整格张伟只有一个替身/一个 entity_id",
          len({r["replacement"] for r in zhang}) == 1
          and len({r["entity_id"] for r in zhang}) == 1)
    check("启发补的实体也进了 shapes", any(s.get("original") == "张伟" for s in res["shapes"]))

    texts = _all_texts(out)
    joined = "\n".join(texts)
    for word in ("张伟", "李娜", "北京市朝阳区建国路88号", "成都", "北京鼎盛科技有限公司"):
        check(f"整格脱敏后查不到「{word}」", word not in joined)

    # 全局检测器不受影响：光靠引擎 still 认不出整格姓名
    check("全局引擎仍然不认整格姓名（启发只在 xlsx 层）",
          Redactor({}).prepare(["张伟"])[0].spans == [])
    wb.close()


def _add_orphan_shared(src: Path, dst: Path, text: str) -> None:
    """往共享串表末尾塞一条没有任何格子引用的字符串（Excel 一般不产生，但别的工具会）。"""
    with zipfile.ZipFile(src) as z:
        names = [i.filename for i in z.infolist()]
        files = {n: z.read(n) for n in names}
    if "xl/sharedStrings.xml" not in files:
        raise RuntimeError("样本里没有共享串表")
    ss = files["xl/sharedStrings.xml"].decode("utf-8")
    if "</sst>" not in ss:
        raise RuntimeError("共享串表结构不认识")
    files["xl/sharedStrings.xml"] = ss.replace(
        "</sst>", f'<si><t xml:space="preserve">{_esc(text)}</t></si></sst>').encode("utf-8")
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as out:
        for name in names:
            out.writestr(name, files[name])


def check_orphan(tmp: Path, shared: Path) -> None:
    print("\n[raw·孤儿共享串] 没格子引用的残留也要清")
    src = tmp / "带孤儿串.xlsx"
    _add_orphan_shared(shared, src, "备注：张伟 身份证110105198503121234")
    out = tmp / "带孤儿串-脱敏.xlsx"
    redact_xlsx_raw(src, out, Redactor({}, mode="mask"))
    ss = _zip_map(out)["xl/sharedStrings.xml"].decode("utf-8")
    before_ss = _zip_map(shared)["xl/sharedStrings.xml"].decode("utf-8")
    check("孤儿串里的姓名/身份证清干净",
          "张伟" not in ss and "110105198503121234" not in ss)
    check("共享串条数没变（索引不能乱）",
          ss.count("<si>") == before_ss.count("<si>") + 1,
          f"{ss.count('<si>')} vs {before_ss.count('<si>') + 1}")
    check("正常格子照样脱敏", "张某某" in "\n".join(_all_texts(out)))


def check_without_openpyxl(tmp: Path) -> None:
    print("\n[raw·不装 openpyxl] 脱敏流程不依赖它")
    src = tmp / "样本-inline.xlsx"
    out = tmp / "无openpyxl.xlsx"
    import builtins
    real = builtins.__import__

    def guard(name, *a, **k):
        if name == "openpyxl" or name.startswith("openpyxl."):
            raise ImportError("测试里故意拦掉 openpyxl")
        return real(name, *a, **k)

    builtins.__import__ = guard
    try:
        res = redact_xlsx_auto(src, out, Redactor({}, mode="mask"))
    finally:
        builtins.__import__ = real
    check("没有 openpyxl 也能脱敏", bool(res["cells"]) and "fallback" not in res)
    wb = load_workbook(str(out))
    check("产出的文件仍然正常", (wb["首页"]["A2"].value or "").startswith("原告：张某某"))
    wb.close()


def _zip_map(path: Path) -> dict:
    with zipfile.ZipFile(path) as z:
        return {name: z.read(name) for name in z.namelist()}


def make_media(path: Path) -> None:
    """带图片 + 图表的工作簿：证明 raw 版不会把它们弄丢。"""
    from openpyxl.chart import BarChart, Reference
    from openpyxl.drawing.image import Image as XLImage
    from PIL import Image as PILImage

    png = path.parent / "_印章.png"
    PILImage.new("RGB", (16, 16), (200, 30, 30)).save(png)

    wb = Workbook()
    ws = wb.active
    ws.title = "带图"
    ws["A1"] = "原告：张伟，男，1985年3月12日出生"
    ws["A2"] = 1
    ws["A3"] = 2
    ws["A4"] = "=SUM(A2:A3)"
    ws["B2"] = "北京市朝阳区建国路88号"
    chart = BarChart()
    chart.title = "金额"
    chart.add_data(Reference(ws, min_col=1, min_row=2, max_row=3))
    ws.add_chart(chart, "D2")
    ws.add_image(XLImage(str(png)), "F2")
    wb.save(str(path))
    wb.close()
    png.unlink()


def check_media(tmp: Path) -> None:
    print("\n[raw·带图片和图表] 处理完还得在")
    src = tmp / "带图.xlsx"
    make_media(src)
    before = _zip_map(src)
    media = [n for n in before if n.startswith(("xl/media/", "xl/charts/", "xl/drawings/"))]
    check("样本里确实有图片/图表部件", len(media) >= 3
          and any(n.startswith("xl/media/") for n in media)
          and any(n.startswith("xl/charts/") for n in media), str(len(media)))

    out = tmp / "带图-脱敏.xlsx"
    res = redact_xlsx_raw(src, out, Redactor({}, mode="mask"))
    after = _zip_map(out)

    check("包里部件一个不少", set(before) == set(after), str(set(before) ^ set(after)))
    changed = {n for n in before if before[n] != after[n]}
    check("只改了工作表 XML（其它字节级原样）",
          changed <= {"xl/worksheets/sheet1.xml"}, str(changed))
    check("图片字节一模一样",
          all(before[n] == after[n] for n in before if n.startswith("xl/media/")))
    check("图表字节一模一样",
          all(before[n] == after[n] for n in before if n.startswith("xl/charts/")))
    check("zip 完整性没问题", zipfile.ZipFile(out).testzip() is None)

    texts = _all_texts(out)
    check("文字照常脱敏", "张伟" not in "\n".join(texts) and "某地址" in texts)
    check("有记账", bool(res["cells"]) and bool(res["shapes"]))

    # 对照：openpyxl 版会整包重写（这次图片/图表它居然也保住了，但缓存值会丢，见下面）
    try:
        out2 = tmp / "带图-脱敏-openpyxl.xlsx"
        redact_xlsx(src, out2, Redactor({}, mode="mask"))
        lost = [n for n in before if n.startswith(("xl/media/", "xl/charts/"))
                and n not in _zip_map(out2)]
        if lost:
            print(f"    提示：openpyxl 版丢了 {len(lost)} 个部件，所以正式使用走 raw 版")
        else:
            print("    提示：这个样本上 openpyxl 版也保住了图片/图表；但它整包重写，"
                  "缓存值/未知扩展照样会丢，正式使用仍走 raw 版")
    except Exception as exc:                    # 对照组失败不影响结论
        print(f"    提示：openpyxl 版处理这个样本直接报错（{exc}），更说明要 raw 版")


def make_cached(path: Path) -> None:
    """造一个带公式缓存值的样本（Excel 保存的文件里就有，openpyxl 重存会丢）。"""
    base = path.parent / "_缓存值_base.xlsx"
    wb = Workbook()
    ws = wb.active
    ws["A1"] = "张伟"
    ws["A2"] = 1
    ws["A3"] = 2
    ws["A4"] = "=SUM(A2:A3)"
    wb.save(str(base))
    wb.close()
    with zipfile.ZipFile(base) as z:
        files = {n: z.read(n) for n in z.namelist()}
    sh = files["xl/worksheets/sheet1.xml"].decode("utf-8").replace(
        "<f>SUM(A2:A3)</f><v></v>", "<f>SUM(A2:A3)</f><v>3</v>")
    if "<v>3</v>" not in sh:
        raise RuntimeError("样本构造失败：openpyxl 的公式写法变了")
    files["xl/worksheets/sheet1.xml"] = sh.encode("utf-8")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    base.unlink()


def check_cached(tmp: Path) -> None:
    print("\n[raw·公式缓存值] Excel 存过的计算值不能丢")
    src = tmp / "缓存值.xlsx"
    make_cached(src)
    out = tmp / "缓存值-脱敏.xlsx"
    redact_xlsx_raw(src, out, Redactor({}, mode="mask"))
    xml = _zip_map(out)["xl/worksheets/sheet1.xml"].decode("utf-8")
    check("公式还在", "<f>SUM(A2:A3)</f>" in xml)
    check("缓存值没丢（raw）", "<v>3</v>" in xml)
    check("文字也脱敏了", "张伟" not in xml and "张某某" in xml)

    opx = tmp / "缓存值-openpyxl.xlsx"
    redact_xlsx(src, opx, Redactor({}, mode="mask"))
    opx_xml = _zip_map(opx)["xl/worksheets/sheet1.xml"].decode("utf-8")
    if "<v>3</v>" not in opx_xml:
        print("    提示：openpyxl 版把这个缓存值弄丢了，所以正式使用走 raw 版")


def check_fallback(tmp: Path) -> None:
    print("\n[兜底] raw 版失败时退回 openpyxl")
    src = tmp / "样本-inline.xlsx"

    clean = tmp / "正常.xlsx"
    red0 = Redactor({}, mode="mask")
    redact_xlsx_auto(src, clean, red0)
    expected_rows = len(red0.rows)

    orig = xlsxraw._rewrite_inline_cells

    def boom(*_a, **_k):
        raise ValueError("模拟写回失败")

    xlsxraw._rewrite_inline_cells = boom
    try:
        out = tmp / "兜底.xlsx"
        red = Redactor({}, mode="mask")
        res = redact_xlsx_auto(src, out, red)
    finally:
        xlsxraw._rewrite_inline_cells = orig

    check("自动退回 openpyxl（带 fallback 说明）", "fallback" in res and "openpyxl" in res["fallback"])
    check("兜底也是脱敏过的", res["cells"] and "张伟" not in "\n".join(_all_texts(out)))
    check("失败那轮的账已撤回，没有重复行", len(red.rows) == expected_rows,
          f"{len(red.rows)} vs {expected_rows}")


def main():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        base = tmp / "样本-inline.xlsx"
        make_base(base)
        inline = base
        shared = tmp / "样本-共享串.xlsx"
        n = to_shared_strings(inline, shared, keep_inline={"A8"})
        print(f"样本已生成：inline 串 + {n} 条共享串（A8 故意留成 inline 串）")
        check("共享串样本确实是共享串", b't="s"' in zipfile.ZipFile(shared).read("xl/worksheets/sheet1.xml"))
        check("共享串样本里还留了一个 inline 串",
              b't="inlineStr"' in zipfile.ZipFile(shared).read("xl/worksheets/sheet1.xml"))

        check_read("inline 串", inline)
        check_read("共享串", shared)
        check("两种存法读出来的内容一致", read_xlsx(inline) == read_xlsx(shared))

        for name, func in (("openpyxl", redact_xlsx), ("raw", redact_xlsx_raw)):
            check_redact(f"{name}·inline 串", inline, tmp, func)
            check_redact(f"{name}·共享串", shared, tmp, func)
        check_clean(tmp, redact_xlsx, "openpyxl")
        check_clean(tmp, redact_xlsx_raw, "raw")

        # 整格值：两个后端都要过
        check_cell_guess(tmp, redact_xlsx, "openpyxl")
        check_cell_guess(tmp, redact_xlsx_raw, "raw")

        check_media(tmp)
        check_cached(tmp)
        check_orphan(tmp, shared)
        check_without_openpyxl(tmp)
        check_fallback(tmp)

    print()
    if fails:
        print(f"{fails} 项没过 ✗")
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())

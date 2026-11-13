# -*- coding: utf-8 -*-
"""docx 附加部件（批注/脚注/页眉页脚）脱敏的验收。"""
from __future__ import annotations

import re
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from docx import Document  # noqa: E402

from legalredact.docxraw import (  # noqa: E402
    apply_extra_texts,
    collect_extra_texts,
    list_extra_parts,
    redact_docx_extras,
)
from legalredact.engine import Redactor  # noqa: E402

OUT = ROOT / "out" / "docxraw_test"
PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name} {extra}")


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
CT_COMMENT = "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"
REL_COMMENT = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments"


def make_docx(path: Path, with_comment=True, with_header=True):
    """造一个带批注 + 页眉的 docx（python-docx 不支持批注，手工塞部件）。"""
    doc = Document()
    doc.add_paragraph("原告张伟，住北京市朝阳区建国路88号，电话13812345678。")
    if with_header:
        sec = doc.sections[0]
        sec.header.paragraphs[0].text = "页眉：承办人王建国 律师"
    doc.save(path)
    if not with_comment:
        return path

    tmp = OUT / "_parts"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    with zipfile.ZipFile(path) as z:
        z.extractall(tmp)

    (tmp / "word" / "comments.xml").write_text(
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:comments xmlns:w="{W}"><w:comment w:id="1" w:author="李四" w:initials="LS">'
        f'<w:p><w:r><w:t>这个张伟的联系电话是13812345678，注意核对。</w:t></w:r></w:p>'
        f"</w:comment></w:comments>",
        encoding="utf-8",
    )
    ct = tmp / "[Content_Types].xml"
    s = ct.read_text(encoding="utf-8")
    if "comments+xml" not in s:
        ct.write_text(s.replace("</Types>", f'<Override PartName="/word/comments.xml" ContentType="{CT_COMMENT}"/></Types>'),
                      encoding="utf-8")
    rels = tmp / "word" / "_rels" / "document.xml.rels"
    s = rels.read_text(encoding="utf-8")
    if "comments.xml" not in s:
        rels.write_text(s.replace("</Relationships>",
                                  f'<Relationship Id="rIdComments" Type="{REL_COMMENT}" Target="comments.xml"/></Relationships>'),
                        encoding="utf-8")

    path.unlink()
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in tmp.rglob("*"):
            if f.is_file():
                z.write(f, f.relative_to(tmp))
    shutil.rmtree(tmp)
    return path


def w_t_texts(docx: Path, part: str) -> list[str]:
    with zipfile.ZipFile(docx) as z:
        xml = z.read(part).decode("utf-8")
    return [re.sub(r"<[^>]+>", "", m) for m in re.findall(r"<w:t[^>]*>.*?</w:t>", xml, re.S)]


def main():
    OUT.mkdir(parents=True, exist_ok=True)

    print("== [1] 部件识别 ==")
    src = make_docx(OUT / "a.docx")
    parts = list_extra_parts(src)
    check("识别到 comments.xml", "word/comments.xml" in parts, parts)
    check("识别到 header", any("header" in p for p in parts), parts)

    print("== [2] 收集文本 ==")
    texts = collect_extra_texts(src)
    check("收集到批注文字", any("张伟" in t for t in texts), texts[:3])
    check("收集到页眉文字", any("王建国" in t for t in texts))

    print("== [3] 独立脱敏（批注 + 页眉都在改） ==")
    r = Redactor({})
    dst = OUT / "b.docx"
    res = redact_docx_extras(src, dst, r)
    check("批注被处理", any("comments" in p for p, _b, _t in res["texts"]))
    check("批注里的手机号被脱", any("×××" in t for p, b, t in res["texts"] if "comments" in p))
    header_changed = any("header" in p for p, _b, _t in res["texts"])
    check("页眉里的律师名按口径保留（不脱）", not header_changed)

    print("== [4] 正文 + 批注同一替身 ==")
    r2 = Redactor({})
    extra_texts = collect_extra_texts(src)
    body_texts = ["原告张伟，住北京市朝阳区建国路88号。"]
    all_res = r2.prepare(body_texts + extra_texts)     # 一次 prepare，和集成一致
    body_text = all_res[0].redacted
    dst2 = OUT / "c.docx"
    apply_extra_texts(src, dst2, [b.redacted for b in all_res[len(body_texts):]])
    comment = " ".join(w_t_texts(dst2, "word/comments.xml"))
    check("正文是张某某", "张某某" in body_text, body_text)
    check("批注也是同一个张某某", "张某某" in comment and "张伟" not in comment, comment)
    check("批注里的手机号也脱了", "×××" in comment, comment)

    print("== [5] 没有附加部件就原样拷 ==")
    plain = Document()
    plain.add_paragraph("干净文档")
    plain.save(OUT / "plain.docx")
    check("识别为空", list_extra_parts(OUT / "plain.docx") == [])
    res2 = redact_docx_extras(OUT / "plain.docx", OUT / "plain2.docx", Redactor({}))
    check("原样拷贝成功", res2["changed"] == 0 and (OUT / "plain2.docx").exists())

    print("== [6] 走完整流程：正文 + 批注一起脱 ==")
    import redact as cli
    cli.OPTIONS.update({"mode": "mask", "mask_style": "surname", "redact_len": "fixed"})
    full = cli.process_docx(src, OUT, {"mode": "mask"}, "full")
    doc = Document(full["out"])
    check("正文脱敏", "张某某" in doc.paragraphs[0].text, doc.paragraphs[0].text)
    ctext = " ".join(w_t_texts(full["out"], "word/comments.xml"))
    check("批注同一替身", "张某某" in ctext and "张伟" not in ctext, ctext)
    with zipfile.ZipFile(full["out"]) as z:
        check("zip 完整性", z.testzip() is None)
        check("批注部件还在", "word/comments.xml" in z.namelist())

    print()
    if FAIL:
        print(f"❌ {PASS} 项通过，{FAIL} 项失败")
        return 1
    print(f"全部通过 ✅（{PASS} 项）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

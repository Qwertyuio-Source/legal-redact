# -*- coding: utf-8 -*-
"""从 Python 版词表生成 JS 词表，保证网页版和命令行版口径一致。"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from legalredact import data, detectors, lexicon  # noqa: E402

OUT = ROOT / "web" / "src" / "rules.js"


def js(v):
    return json.dumps(v, ensure_ascii=False)


def main():
    single, compound = lexicon.surnames()
    payload = {
        "SINGLE_SURNAMES": "".join(sorted(single)),
        "COMPOUND_SURNAMES": sorted(compound),
        "GIVEN_NAME_CHARS": "".join(sorted(lexicon.given_chars())),
        "PLACE_WORDS": sorted(lexicon.place_words()),
        "LEX_STOPWORDS": sorted(lexicon.stopwords()),
        "NAME_REJECT_CHARS": "".join(sorted(data.NAME_REJECT_CHARS)),
        "NAME_STOPWORDS": sorted(set(data.NAME_STOPWORDS) | lexicon.stopwords() | lexicon.place_words()),
        "ORG_SUFFIXES": data.ORG_SUFFIXES,
        "ORG_INDUSTRY": list(detectors.ORG_INDUSTRY),
        "ORG_KEEP_PATTERNS": data.ORG_KEEP_PATTERNS,
        "REDACT_ANCHORS": data.REDACT_ANCHORS,
        "KEEP_ANCHORS": data.KEEP_ANCHORS,
        "BOUNDARY_CHARS": "".join(sorted(data.BOUNDARY_CHARS)),
        "LEAD_STOP_TOKENS": data.LEAD_STOP_TOKENS,
        "GENERIC_ORG_WORDS": sorted(data.GENERIC_ORG_WORDS),
        "ADDRESS_ANCHORS": data.ADDRESS_ANCHORS,
        "PLACE_TAIL": "".join(sorted(detectors.PLACE_TAIL)),
        "NAME_TERMINATORS": "".join(sorted(detectors.NAME_TERMINATORS)),
        "VERB_CHARS": "".join(sorted(detectors.VERB_CHARS)),
    }
    lines = [
        "/* 自动生成，请勿手改：python tools/gen_web_rules.py */",
        "(function (root) {",
        "  const R = {",
    ]
    for k, v in payload.items():
        lines.append(f"    {k}: {js(v)},")
    lines += [
        "  };",
        "  // 转成 Set，检测时查得快",
        "  R.set = {};",
        "  for (const k of ['NAME_REJECT_CHARS','NAME_STOPWORDS','GIVEN_NAME_CHARS',",
        "                   'BOUNDARY_CHARS','PLACE_TAIL','NAME_TERMINATORS','VERB_CHARS',",
        "                   'GENERIC_ORG_WORDS']) {",
        "    R.set[k] = new Set(R[k]);",
        "  }",
        "  R.set.COMPOUND_SURNAMES = new Set(R.COMPOUND_SURNAMES);",
        "  R.set.SINGLE_SURNAMES = new Set(R.SINGLE_SURNAMES);",
        "  root.LR_RULES = R;",
        "})(typeof window !== 'undefined' ? window : globalThis);",
        "",
    ]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"写入 {OUT}（{OUT.stat().st_size} 字节）")


if __name__ == "__main__":
    main()

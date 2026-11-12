# -*- coding: utf-8 -*-
"""脱敏评测：量出「该脱的漏了多少 / 不该脱的误伤多少」，也检查「该保留的有没有被误脱」。

口径（用户最初定的）：
  要脱：当事人姓名、单位名、住址、身份证、电话、邮箱
  要保留：法院、案号、法官、律师、法条

用法：python tools/bench.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from legalredact.engine import Redactor  # noqa: E402

CASES = [
    # (文本, 要脱掉的, 要保留的)
    (
        "项目执行由：卓燃时尚（北京）文化传媒为主体进行落地执行，\n"
        "协会听取了卓燃团队的建后，项目由卓燃文化提案，提案通过后由卓燃文化落地执行。",
        ["卓燃时尚（北京）文化传媒", "卓燃团队", "卓燃文化"],
        ["项目", "协会", "落地执行"],
    ),
    (
        "原告：张伟，男，1985年3月12日出生，汉族，住北京市朝阳区建国路88号，"
        "身份证号码110105198503121234，联系电话13812345678。",
        ["张伟", "北京市朝阳区建国路88号", "110105198503121234", "13812345678"],
        ["汉族", "1985年3月12日"],
    ),
    (
        "被告北京鼎盛（集团）科技有限公司，住所地北京市海淀区中关村大街1号，"
        "法定代表人王建国。",
        ["北京鼎盛（集团）科技有限公司", "北京市海淀区中关村大街1号", "王建国"],
        [],
    ),
    (
        "转账详情：付款方李娜，收款方上海某某贸易有限公司，时间2026/03/04 11:52:35。",
        ["李娜", "上海某某贸易有限公司"],
        ["2026/03/04"],
    ),
    (
        # 法官 / 书记员按口径保留
        "合议庭成员：审判长赵国庆、审判员钱伟、陪审员孙丽。书记员周敏。",
        [],
        ["赵国庆", "钱伟", "孙丽", "周敏"],
    ),
    (
        # 律师与律所：律师名保留，律所名要脱（律所是单位）
        "委托诉讼代理人：王建国，北京市中伦律师事务所律师。",
        ["北京市中伦律师事务所"],
        ["王建国", "律师"],
    ),
    (
        # 无锚点：表格里的姓名列
        "姓名\t职务\t身份证号\n李强\t总经理\t110105199001011232",
        ["李强", "110105199001011232"],
        ["总经理", "职务"],
    ),
    (
        # 邮箱 / 微信 / 银行账号
        "联系人邮箱 zhangwei@example.com，微信 wxid_abc123，"
        "收款账号 6222021234567890123。",
        ["zhangwei@example.com", "6222021234567890123"],
        [],
    ),
    (
        # 地址变体
        "现住上海市浦东新区张江路100号3号楼502室。",
        ["上海市浦东新区张江路100号3号楼502室"],
        ["现住"],
    ),
    (
        # 顿号并列人名
        "原告王伟、李娜、赵强均到庭参加诉讼。",
        ["王伟", "李娜", "赵强"],
        [],
    ),
    (
        # 顿号并列不应误伤地名
        "本案涉及北京、上海两地。",
        [],
        ["北京", "上海"],
    ),
    (
        # 真实公告里的两种地址形态：带破折号、标签中间有空格
        "山东省济南市市中区二环南路9078号恒大睿城9—2229\n"
        "地    址：河北省张家口市蔚县蔚州镇胜利西路81号",
        ["山东省济南市市中区二环南路9078号恒大睿城9—2229",
         "河北省张家口市蔚县蔚州镇胜利西路81号"],
        [],
    ),
    (
        # 表格被摊平：值在单独一行，没有锚点（真实政府采购公告里的形态）
        "供应商名称\n供应商地址\n中标（成交）金额\n"
        "潍坊盛云图文有限公司\n山东省潍坊市寒亭区通亭街3769号瑞城大厦9号商铺\n600,000.00元",
        ["潍坊盛云图文有限公司", "山东省潍坊市寒亭区通亭街3769号瑞城大厦9号商铺"],
        [],   # 金额本身按口径也要脱
    ),
    (
        # 公司简称（有全称出现过）
        "北京易盛（集团）科技有限公司起诉。易盛集团表示不服，易盛公司已上诉。",
        ["北京易盛（集团）科技有限公司", "易盛集团", "易盛公司"],
        ["上诉"],
    ),
]


def run():
    total_exp = total_hit = total_fp = total_kept = total_kept_bad = 0
    failures = []
    for idx, (text, expected, must_keep) in enumerate(CASES, 1):
        redactor = Redactor({})
        out = redactor.prepare([text])[0].redacted

        missed = [e for e in expected if e in out]
        hit = len(expected) - len(missed)
        over = [k for k in must_keep if k not in out]  # 该保留的被脱掉了

        total_exp += len(expected)
        total_hit += hit
        total_kept += len(must_keep)
        total_kept_bad += len(over)
        total_fp += len(over)

        line = f"[{idx:2d}] 召回 {hit}/{len(expected)}"
        line += "  ✓" if not missed else f"  ✗ 漏：{missed}"
        if over:
            line += f"  | 误脱：{over}"
        print(line)
        if missed or over:
            failures.append(idx)

    print()
    print(f"召回率：{total_hit}/{total_exp} = {total_hit / total_exp:.1%}")
    print(f"该保留却被脱：{total_kept_bad}/{total_kept}")
    print(f"失败样本：{failures or '无'}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(run())

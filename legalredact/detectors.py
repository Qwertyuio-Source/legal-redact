# -*- coding: utf-8 -*-
"""检测器：从文本里找出需要脱敏的片段（返回 Span 列表，不改文本）。"""
import re
from dataclasses import dataclass, field

from . import data, lexicon
from . import types as T


@dataclass
class Span:
    start: int
    end: int
    kind: str          # person / org / id / phone / email / address
    text: str
    rule: str          # 命中规则，便于回溯
    priority: int = 50
    sub: str = ""      # 单位类型后缀，比如 医院、学院

    def overlaps(self, other: "Span") -> bool:
        return self.start < other.end and other.start < self.end


PRIORITY = {
    "ID_CARD": 100,
    "USCC": 99,
    "BANK_ACCOUNT": 97,
    "PASSPORT": 96,
    "HK_MACAO_PASS": 95,
    "TAIWAN_PASS": 94,
    "PHONE": 98,
    "LANDLINE": 97,
    "EMAIL": 96,
    "QQ": 95,
    "WECHAT": 95,
    "PLATE": 93,
    "IP": 92,
    "URL": 91,
    "API_KEY": 98,
    "IMEI": 93,
    "MAC": 92,
    "TAX_NO": 96,
    "CASE_FILE_NO": 90,
    "SEAL": 88,
    "BIOMETRIC": 87,
    "HEALTH": 86,
    "BIRTHDATE": 85,
    "AMOUNT": 84,
    "ADDRESS": 80,
    "TITLE": 75,
    "PROPERTY_NO": 89,
    "SOCIAL_SEC": 91,
    "INSURANCE_NO": 90,
    "ORG": 70,
    "PERSON": 60,
    "id": 100,
    "phone": 98,
    "email": 96,
}

RE_ID = re.compile(
    r"(?<![0-9])[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?![0-9])"
)
RE_PHONE = re.compile(r"(?<![0-9])1[3-9]\d{9}(?![0-9])")
RE_LANDLINE = re.compile(r"(?<![0-9])0\d{2,3}[- ]?\d{7,8}(?![0-9])")
RE_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

ORG_SCAN_STOP = set(data.BOUNDARY_CHARS) - set("（）()《》“”\"'")

_ORG_ALT = "|".join(re.escape(s) for s in sorted(data.ORG_SUFFIXES, key=len, reverse=True))
RE_ORG_SUFFIX = re.compile(_ORG_ALT)

_REDACT_ALT = "|".join(
    re.escape(s) for s in sorted(data.REDACT_ANCHORS, key=len, reverse=True)
)
RE_PERSON_ANCHOR = re.compile(rf"(?:{_REDACT_ALT})\s*(?:（[^）]{{0,20}}）)?\s*[：:]?\s*")

_KEEP_ALT = "|".join(re.escape(s) for s in sorted(data.KEEP_ANCHORS, key=len, reverse=True))
RE_KEEP_ANCHOR = re.compile(rf"(?:{_KEEP_ALT})\s*[：:]?\s*")

RE_BIO = re.compile(
    r"(?P<name>[\u4e00-\u9fa5]{2,4})(?=[，,]\s*(?:男|女|汉族|满族|回族|蒙古族|壮族|19\d{2}年|20\d{2}年|系|该公司|担任|任该|为该公司))"
)
# 已经部分打码的名字：王某颖、李某华（法院公开时常用「姓+某+名」）
RE_PARTIAL_NAME = re.compile(r"([\u4e00-\u9fa5])某([\u4e00-\u9fa5]{1,2})")

# 名字 + 强谓语：没有「原告/被告」这类锚点时靠它兜底
RE_NAME_PREDICATE = re.compile(
    r"(?P<name>[\u4e00-\u9fa5]{2,3})(?=(?:到庭|辩称|诉称|陈述|未到庭|参加诉讼|出具|签字|签收|确认|表示|称|到案))"
)
RE_VS = re.compile(
    r"(?P<a>[\u4e00-\u9fa5]{2,3})\s*(?:与|诉|和|及)\s*(?P<b>[\u4e00-\u9fa5]{2,3})"
)

_ADDR_ALT = "|".join(re.escape(s) for s in sorted(data.ADDRESS_ANCHORS, key=len, reverse=True))
RE_ADDR_ANCHOR = re.compile(rf"(?:{_ADDR_ALT})\s*[：:]?\s*(?P<val>[^\n，。；;]{{4,80}})")
RE_ADDR_PLAIN = re.compile(
    r"(?<![居现所])住\s*(?P<val>[\u4e00-\u9fa5]{2,60}?(?:省|市|区|县)[^\n，。；;]{0,40})"
)

PLACE_TAIL = set("省市县区镇乡村路街巷号楼栋室组队")

# 人名后面允许紧跟的字：标点、连接词、法律文书里最常见的谓语动词
NAME_TERMINATORS = set(data.BOUNDARY_CHARS) | set(
    "等系和与及诉称辩说未已亦均皆也并且或因据就依按经从至于在为是的之其该本每各里处内外前"
    "后时中上下犯停驾骑驶住受伤到向出提签付还借欠收送买卖租用持将被由衷表示要求认为主张请"
    "赔偿承负担享得获取交罚判"
)
# 出现在姓名第三位基本可以断定是谓语而不是名字
VERB_CHARS = set(
    "辩称诉未已向于到经提主认为系等和与及的去来被将对给由在犯停驾骑驶受是并且或因据就依按从至于为的之其该本"
)


def is_valid_name(tok: str) -> bool:
    if not tok or not (2 <= len(tok) <= 4):
        return False
    if "某" in tok:
        return False
    if any(ch in data.NAME_REJECT_CHARS for ch in tok):
        return False
    if tok in data.NAME_STOPWORDS or tok in lexicon.stopwords() or tok in lexicon.place_words():
        return False
    if tok in data.COMMON_WORDS:
        return False          # 高频普通词（国家/对外/高水平…）不可能是人名
    if tok[-1] in PLACE_TAIL:
        return False
    if RE_ORG_SUFFIX.fullmatch(tok) or any(tok.endswith(s) for s in data.ORG_SUFFIXES):
        return False
    single, compound = lexicon.surnames()
    if tok[:2] in compound:
        rest = tok[2:]
        return 1 <= len(rest) <= 2 and not any(ch in data.NAME_REJECT_CHARS for ch in rest)
    return tok[0] in single


def plausible_name(tok: str) -> bool:
    """姓之后的部分是不是常见的名字用字——用来认「被害人赵敏停放…」这种后面直接跟谓语的名字。"""
    rest = tok[2:] if tok[:2] in lexicon.surnames()[1] else tok[1:]
    if not rest:
        return False
    chars = lexicon.given_chars()
    return all(ch in chars for ch in rest)


_ORG_LEAD_WORDS = ("参见", "载", "包括", "以及", "此外", "其中", "比如", "例如", "如",
                   "据", "见", "由", "在", "对", "于", "同", "及", "和", "与", "从",
                   "是", "为", "被", "将", "已", "现", "也", "均", "都")


def _trim_org_lead(cand: str) -> str:
    changed = True
    while changed:
        changed = False
        for w in _ORG_LEAD_WORDS:
            if cand.startswith(w) and len(cand) - len(w) >= 4:
                cand = cand[len(w):]
                changed = True
                break
    return cand


def _trim_org_candidate(cand: str) -> str:
    """切掉候选里混进来的前导动词/身份词，返回真正的字号部分。"""
    cut = 0
    for tok in data.LEAD_STOP_TOKENS:
        idx = cand.rfind(tok)
        if idx >= 0:
            cut = max(cut, idx + len(tok))
    cand = cand[cut:]
    cand = cand.lstrip("，。、；：（）()《》“”\"' 　")
    return cand.strip()


_ORG_FRAGMENTS = frozenset({"研究中心", "法治学院", "国语大学", "研究院", "研究所"})
# 单位名里绝不会出现的动词短语（出现说明是从句子里截出来的）
_ORG_VERB_PHRASES = ("取消", "实现", "推进", "加强", "包括", "进入", "建立", "完善",
                     "形成", "成为", "作为", "进行", "开展", "具备", "拥有", "达到",
                     "促进", "推动", "支持", "参与", "涉及", "属于", "存在", "具有")


def _org_ok(cand: str, suffix: str, text: str, end: int) -> bool:
    if cand in _ORG_FRAGMENTS:
        return False
    if any(ph in cand for ph in _ORG_VERB_PHRASES):
        return False
    if len(cand) < 2:
        return False
    if cand in data.GENERIC_ORG_WORDS:
        return False
    if any(bad in cand for bad in data.ORG_KEEP_PATTERNS):
        return False
    if cand.endswith("等"):
        return False
    # 「公司法」「公司章程」这类普通名词不算单位名
    if end < len(text) and text[end] in "法规定条章程":
        return False
    # 单位名至少要有 2 个汉字（不含后缀）
    body = cand[: -len(suffix)] if cand.endswith(suffix) else cand
    if len(body) < 2:
        return False
    return True


# 单位名里的「行业词」：替换时保留，读起来才像人话（某某某科技有限公司）
ORG_INDUSTRY = (
    "科技", "文化", "传媒", "贸易", "商贸", "实业", "信息", "网络", "软件", "电子",
    "机械", "建筑", "装饰", "环保", "能源", "材料", "医药", "生物", "化工", "食品",
    "服装", "置业", "地产", "建设", "工程", "设计", "咨询", "投资", "担保", "租赁",
    "物业", "物流", "教育", "旅游", "餐饮", "农业", "集团", "控股", "发展", "管理",
    "服务", "技术", "制造", "金融", "供应链", "机电", "设备", "仪器", "自动化",
    "智能", "数据", "数字", "新能源", "新材料", "半导体", "汽车", "运输", "仓储",
    "酒店", "娱乐", "影视", "广告", "会展", "体育", "医疗", "健康", "养老", "培训",
    "会计", "审计", "评估", "拍卖", "出版", "演艺", "航空", "船舶", "印刷", "包装",
)

RE_ORG_TAIL = re.compile(
    r"(?:" + "|".join(sorted(ORG_INDUSTRY + tuple(data.ORG_SUFFIXES), key=len, reverse=True)) + r")+$")


def org_tail(name: str) -> str:
    """取单位名末尾的「类型尾巴」：北京鼎盛科技有限公司 → 科技有限公司。"""
    m = RE_ORG_TAIL.search(name)
    if not m:
        return ""
    tail = m.group(0)
    # 尾巴不能等于整个名字（否则等于没脱），也不能太短
    if tail == name or len(tail) < 2:
        return ""
    return tail


# 括号归一化：OCR 常把（）认成〈〉【】，长度相同，位置不受影响
_BRACKET_MAP = str.maketrans({"〈": "（", "〉": "）", "【": "（", "】": "）",
                              "〔": "（", "〕": "）", "＜": "（", "＞": "）"})


RE_PAREN_CITY = re.compile(
    r"[\u4e00-\u9fa5]{2,8}[（(][\u4e00-\u9fa5]{2,4}[）)]\s*[\u4e00-\u9fa5]{2,10}")
# 括号后面遇到这些字就截断（"…文化传媒为主体进行落地" → "…文化传媒"）
_ORG_TAIL_STOP = set("为所的进行由与和及在是，。、；：！？\n")
_ORG_TAIL_VERB = set("迟未已将正均都也仍却则并且后前中内上下还又再才就")


def find_paren_orgs(text: str, taken: list[Span] | None = None) -> list[Span]:
    """「XX（北京）YY」这类公司写法：括号里是地名，后面跟 2~10 个字的字号。

    这类名字常常不以「公司」结尾（如「卓燃时尚（北京）文化传媒」），
    后缀规则抓不到，但括号+地名是很强的信号。
    """
    spans: list[Span] = []
    norm = text.translate(_BRACKET_MAP)      # 长度不变，位置可直接用
    for m in RE_PAREN_CITY.finditer(norm):
        tok = m.group(0)
        cp = max(tok.find("）"), tok.find(")"))
        if cp < 0:
            continue
        cut = len(tok)
        for i in range(cp + 1, len(tok)):
            if tok[i] in _ORG_TAIL_STOP or tok[i] in _ORG_TAIL_VERB:
                cut = i
                break
        cut = min(cut, cp + 1 + 8)   # 括号后最多 8 个字
        tok = tok[:cut]
        # 去掉开头的介词/连词（「由卓燃…」→「卓燃…」）
        lead = set("由与和及在是对向给被让使据经项其该")
        n_lead = 0
        for w in ("参见", "载", "见", "包括", "以及"):
            if tok.startswith(w) and len(tok) - len(w) >= 6:
                tok = tok[len(w):]
                n_lead += len(w)
                break
        while tok and tok[0] in lead:
            tok = tok[1:]
            n_lead += 1
            cp -= 1
        if cp < 0:
            continue
        tail = len(tok) - cp - 1
        if tail < 2 or tail > 10:
            continue
        # 注意：不要因为「以公司结尾」就跳过——括号名往往比后缀规则认得更长
        # （北京易盛（集团) 科技有限公司），重复由下面的包含判断处理
        if any(rx.search(tok) for _name, rx in T.PRESERVE_RULES):
            continue          # 法院/检察院/案号之类不脱
        start = m.start() + n_lead
        end = start + len(tok)
        tok_orig = text[start:end]           # 用原文（保留它自己的括号写法）
        # 已经和后缀规则认出的是同一家，就不要再重复标
        if taken and any(t.start <= start and end <= t.end for t in taken):
            continue      # 已被后缀规则完全覆盖，不用重复
        spans.append(Span(start, end, T.ORG, tok_orig, "paren-city", PRIORITY["ORG"],
                          sub=org_tail(tok) or "公司"))
    return spans


# 简称后缀：出现「核心词+这些词」就算同一家单位
ORG_SHORT_SUFFIX = ("集团", "公司", "团队", "文化", "传媒", "工作室", "实业", "科技", "事务所")
_CITY_LEAD = ("北京", "上海", "天津", "重庆", "广州", "深圳", "杭州", "南京", "成都",
              "武汉", "西安", "苏州", "郑州", "长沙", "青岛", "济南", "合肥", "福州",
              "厦门", "宁波", "无锡", "大连", "沈阳", "哈尔滨", "长春", "昆明", "南昌")


def _org_core(name: str) -> str:
    """取单位名的「核心词」：去掉开头地名，取 2 个汉字（卓燃时尚… → 卓燃）。"""
    body = name
    for city in _CITY_LEAD:
        if body.startswith(city):
            body = body[len(city):]
            break
    m = re.match(r"[\u4e00-\u9fa5]{2}", body)
    return m.group(0) if m else ""


_TABLE_SPLIT = re.compile(r"[\t|\uFF5C]")


_TABLE_HEADER_NAME = re.compile(
    r"(姓名|名字|人名|当事人|原告|被告|申请人|被申请人|负责人|法定代表人|联系人|经办人|"
    r"代理人|辩护人|股东|员工|职工|收款人|付款人|签署人|签字人|审核人|权利人|义务人|户主|承租人|出借人)")


def find_table_names(text: str) -> list[Span]:
    """表格里的姓名（「姓名\t职务\t身份证」这种没有锚点的场景）。

    误判教训：以前只要 2~3 字的单元格长得像人名就认，结果「A\t高水平」「对外\t国国」都被
    当成人名。现在要求**同一行或表头里有明确的姓名类标签**，宁缺勿滥。
    """
    spans: list[Span] = []
    lines = text.split("\n")

    # 先找横向表头（表头行 + 后面若干数据行）
    header_cols: dict[int, str] = {}
    for line in lines:
        if len(_TABLE_SPLIT.findall(line)) < 1:
            continue
        cells = [c.strip() for c in _TABLE_SPLIT.split(line)]
        cols = {i: c for i, c in enumerate(cells) if _TABLE_HEADER_NAME.search(c)}
        if cols:
            header_cols = cols
            break

    pos = 0
    for line in lines:
        line_start = pos
        pos += len(line) + 1
        if len(_TABLE_SPLIT.findall(line)) < 1:
            continue
        cells = [c.strip() for c in _TABLE_SPLIT.split(line)]

        # 纵向：同一行里「姓名 	 张伟」
        for i, c in enumerate(cells[:-1]):
            if _TABLE_HEADER_NAME.search(c):
                tok = cells[i + 1].strip()
                spans.extend(_mk_cell_span(line, line_start, tok))
        # 横向：按表头列取数据行的对应列
        if header_cols:
            for i in header_cols:
                if i < len(cells):
                    tok = cells[i].strip()
                    if _TABLE_HEADER_NAME.search(tok):
                        continue          # 还是表头本身
                    spans.extend(_mk_cell_span(line, line_start, tok))
    # 去重（同一位置只留一个）
    seen = set()
    out = []
    for sp in spans:
        key = (sp.start, sp.end)
        if key in seen:
            continue
        seen.add(key)
        out.append(sp)
    return out


def _mk_cell_span(line: str, line_start: int, tok: str) -> list[Span]:
    if not (2 <= len(tok) <= 3):
        return []
    if tok in data.COMMON_WORDS or tok in data.NAME_STOPWORDS:
        return []
    if not is_valid_name(tok) or not plausible_name(tok):
        return []
    idx = line.find(tok)
    if idx < 0:
        return []
    return [Span(line_start + idx, line_start + idx + len(tok),
                 T.PERSON, tok, "table-cell", PRIORITY["PERSON"])]


RE_NAME_LIST = re.compile(r"[\u4e00-\u9fa5]{2,3}(?:、[\u4e00-\u9fa5]{2,3}){1,}")


def find_enumerated_names(text: str, keep: set[str] | None = None) -> list[Span]:
    """顿号并列的人名：「王伟、李娜、赵强」。

    误判教训（真实文档）：「全方位、深层次、高水平推进」「广西、河北、云南」都被当成并列人名。
    现在两条硬约束：
      1. 每一项的姓氏必须在**常见姓氏表**里（顶级冷门姓不认）
      2. 首项必须是完整有效人名，不允许截断；后续项才允许把「赵强均到庭」截成「赵强」
    """
    spans: list[Span] = []
    for m in RE_NAME_LIST.finditer(text):
        parts = m.group(0).split("、")
        if not parts:
            continue
        head = parts[0]
        head_cut = 0
        # 正则可能多抓了前一个字（「告王伟」），从左边削掉再判
        if not (is_valid_name(head) and plausible_name(head)):
            for cut in (1, 2):
                if len(head) - cut >= 2:
                    t2 = head[cut:]
                    if t2[:1] in data.COMMON_SURNAMES and is_valid_name(t2) and plausible_name(t2):
                        head, head_cut = t2, cut
                        break
        if head[:1] not in data.COMMON_SURNAMES:
            continue
        # 「参见张琼斯、范子萌」这种引注列表：前面有引注标记时放宽名字用字校验
        before = text[max(0, m.start() - 6): m.start()]
        is_citation = any(before.endswith(w) for w in ("参见", "载", "见", "如", "据", "阅"))
        if is_citation:
            if not is_valid_name(head):
                continue
        elif not (is_valid_name(head) and plausible_name(head)):
            continue                     # 首项不成立，整串放弃
        pos = m.start() + head_cut
        for idx, raw in enumerate(parts):
            if idx == 0:
                raw = head
            picked = None
            if is_citation and 2 <= len(raw) <= 3 and raw[:1] in data.COMMON_SURNAMES \
                    and is_valid_name(raw):
                picked = raw          # 引注列表：整项当名字，不截断
            elif raw[:1] in data.COMMON_SURNAMES and 2 <= len(raw) <= 3 \
                    and is_valid_name(raw) and plausible_name(raw):
                picked = raw
            elif len(raw) == 3 and raw[2] not in lexicon.given_chars() \
                    and raw[:1] in data.COMMON_SURNAMES \
                    and is_valid_name(raw[:2]) and plausible_name(raw[:2]):
                picked = raw[:2]          # 「李娜签订」→ 李娜
            if picked and picked not in (keep or set()):
                spans.append(Span(pos, pos + len(picked), T.PERSON, picked,
                                  "name-list", PRIORITY["PERSON"]))
            pos += len(raw) + 1
    return spans


RE_STANDALONE_ADDR = re.compile(
    r"^(?:[\u4e00-\u9fa5]{2,10}(?:省|自治区|特别行政区)?)[\u4e00-\u9fa5\d]{2,10}"
    r"(?:市|自治州|地区)[\u4e00-\u9fa5\d]{2,12}(?:区|县|市)?[\u4e00-\u9fa5\d]{0,20}"
    r"(?:路|街|巷|道|村|镇|号)[\u4e00-\u9fa5\dA-Za-z\-—–－]{0,20}$")


def find_standalone(text: str) -> list[Span]:
    """没锚点、整行就是地址的情况（docx/xlsx 表格被摊平后最常见）。"""
    spans: list[Span] = []
    pos = 0
    for line in text.split("\n"):
        line_start = pos
        pos += len(line) + 1
        tok = line.strip()
        if not tok or len(tok) < 6:
            continue
        stripped = re.sub(r"^[地住]\s*址\s*[：:]\s*", "", tok)
        if stripped != tok:
            offset = line_start + line.find(stripped)
            tok = stripped
        else:
            offset = line_start + line.find(tok)
        if 8 <= len(tok) <= 60 and RE_STANDALONE_ADDR.match(tok):
            hits = sum(1 for k in ("省", "市", "区", "县", "路", "街", "号", "镇", "村") if k in tok)
            if hits >= 2:
                spans.append(Span(offset, offset + len(tok), T.ADDRESS, tok,
                                  "standalone-addr", PRIORITY["ADDRESS"]))
    return spans


def find_org_shorts(text: str, org_spans: list[Span]) -> list[Span]:
    """简称：全称是「卓燃时尚（北京）文化传媒」，文中又出现「卓燃团队」「卓燃文化」。"""
    cores = set()
    for sp in org_spans:
        c = _org_core(sp.text)
        if c:
            cores.add(c)
    if not cores:
        return []
    spans: list[Span] = []
    for core in cores:
        for suf in ORG_SHORT_SUFFIX:
            token = core + suf
            start = 0
            while True:
                i = text.find(token, start)
                if i < 0:
                    break
                start = i + 1
                end = i + len(token)
                # 已经被全称覆盖的不要重复
                if any(i < t.end and t.start < end for t in org_spans):
                    continue
                spans.append(Span(i, end, T.ORG, token, "org-short",
                                  PRIORITY["ORG"], sub=suf))
    return spans


def find_orgs(text: str) -> list[Span]:
    spans: list[Span] = []
    seen: set[tuple[int, int]] = set()
    for m in RE_ORG_SUFFIX.finditer(text):
        suffix = m.group(0)
        start, end = m.start(), m.end()
        # 同一位置可能有「有限公司」和「公司」两个匹配，只取最长的
        if any(start == s and end < e for s, e in seen):
            continue
        j = start
        limit = 40
        while j > 0 and (start - (j - 1)) <= limit and text[j - 1] not in ORG_SCAN_STOP:
            j -= 1
        raw = text[j:start]
        cand = _trim_org_lead(_trim_org_candidate(raw))
        if not _org_ok(cand, suffix, text, end):
            continue
        cstart = start - len(cand)
        if cstart < 0:
            continue
        if (cstart, end) in seen:
            continue
        # 去掉前面仍然是截断的情况（左邻是汉字且不是常见修饰，说明可能切多了）
        seen.add((cstart, end))
        final = text[cstart:end]
        if final in _ORG_FRAGMENTS or any(ph in final for ph in _ORG_VERB_PHRASES):
            continue
        if len(final) <= 3 and not any(final.endswith(x) for x in ("公司", "集团", "大学", "学院", "医院")):
            continue
        # 替身保留类型尾巴：北京鼎盛科技有限公司 → 某某某科技有限公司
        sub = org_tail(final) or suffix
        spans.append(Span(cstart, end, T.ORG, text[cstart:end], "org-suffix", PRIORITY["ORG"], sub))
    return spans


_PLACE_MARKS = set("省市区县镇乡村组街道巷路号")
_ORG_NEXT_CHARS = set("局委府院厅部办站所公司集团会市省区县镇乡")


def _bad_name(tok: str, text: str, start: int, L: int) -> bool:
    """锚点后面的候选值是不是「地名/单位的前半截」——盘州市某资源局 → 盘州 这种，判否。"""
    if not tok:
        return True
    if any(ch in _PLACE_MARKS for ch in tok):
        return True
    if tok in data.COMMON_WORDS:
        return True
    nxt = text[start + L] if start + L < len(text) else ""
    if nxt in _ORG_NEXT_CHARS:
        return True
    # 后面紧跟虚词/动词 → 这是句子片段，不是人名（「户持 有…」「单方 制作」）
    if nxt in "的了着有在可会要能对为是不没很太更最被将把让使给也都而且还只就才又再已":
        return True
    # 后面 8 个字里出现行政区划/单位词，说明这是个地名或单位名
    window = text[start: start + 10]
    if any(w in window for w in ("自治县", "自治州", "自治区", "政府", "委员会", "资源局",
                                 "管理局", "人民法院", "公司", "事务所")):
        return True
    return False


def _take_name_after(text: str, pos: int, strict: bool = False) -> tuple[int, int, str] | None:
    """从 pos 开始取一个人名。

    strict=False：锚点后面（原告：/被告：…）信任上下文，两字就收；
    strict=True：只用于「甲与乙」这种弱上下文，要求后面是标点或谓语。
    """
    i = pos
    while i < len(text) and text[i] in " 　\t：:":
        i += 1
    start = i
    n = 0
    while i < len(text) and n < 6 and text[i] not in data.BOUNDARY_CHARS:
        i += 1
        n += 1
    seg = text[start:i]
    if not seg:
        return None
    # 「原告：北京某某科技有限公司」这种单位不是人
    if any(s in seg for s in data.ORG_SUFFIXES):
        return None

    def ok_next(L: int) -> bool:
        nxt = text[start + L] if start + L < len(text) else ""
        return nxt == "" or nxt in NAME_TERMINATORS

    # 复姓 + 两字名
    if seg[:2] in lexicon.surnames()[1]:
        for L in (4, 3):
            if len(seg) >= L and is_valid_name(seg[:L]) and (ok_next(L) or plausible_name(seg[:L])):
                if _bad_name(seg[:L], text, start, L):
                    return None
                return start, start + L, seg[:L]

    # 三字名：后面必须是标点/谓语，且不是「应当承」这种词打头、第三个字不像动词
    if (len(seg) >= 3 and seg[:2] not in data.NAME_STOPWORDS
            and is_valid_name(seg[:3]) and seg[2] not in VERB_CHARS and ok_next(3)
            and plausible_name(seg[:3])):
        if _bad_name(seg[:3], text, start, 3):
            return None
        return start, start + 3, seg[:3]

    # 两字名
    if len(seg) >= 2 and is_valid_name(seg[:2]):
        if strict and not ok_next(2) and not plausible_name(seg[:2]):
            return None
        if _bad_name(seg[:2], text, start, 2):
            return None
        return start, start + 2, seg[:2]
    return None


RE_CITE_AUTHOR = re.compile(r"([\u4e00-\u9fa5]{2,4})[：:]\s*[“\"《]")


def find_cite_authors(text: str, keep: set[str] | None = None) -> list[Span]:
    """引注里的作者名：「黄进：“论统筹推进…”」「马忠法:《…》」。

    学术/文书脚注里作者名很密，但没有「原告：」这类锚点，单独一条规则兜。
    """
    spans: list[Span] = []
    for m in RE_CITE_AUTHOR.finditer(text):
        tok = m.group(1)
        if tok in (keep or set()) or tok in data.COMMON_WORDS:
            continue
        # 引注作者只要求「像名字」，不套 given-chars 白名单（马忠法、黄文艺这种）
        for suf in ("主编", "编著", "编", "著"):
            if tok.endswith(suf) and len(tok) - len(suf) >= 2:
                tok = tok[: -len(suf)]
                break
        start = m.start(1)
        if not is_valid_name(tok):
            # 「参见黄进：」「以上黄文艺：」——从尾部试 4/3/2 字
            fixed = None
            for L in (4, 3, 2):
                if L < len(tok):
                    tail = tok[-L:]
                    if tail[:1] in data.COMMON_SURNAMES and is_valid_name(tail):
                        fixed = (tail, m.start(1) + len(tok) - L)
                        break
            if not fixed:
                continue
            tok, start = fixed
        if tok[:1] not in data.COMMON_SURNAMES:
            continue
        spans.append(Span(start, start + len(tok), T.PERSON, tok,
                          "cite-author", PRIORITY["PERSON"]))
    return spans


RE_BYLINE = re.compile(r"([\u4e00-\u9fa5])\s+([\u4e00-\u9fa5]{1,2})\s*\*{1,2}|([\u4e00-\u9fa5]{2,4})\*{1,2}")


def find_byline_names(text: str, keep: set[str] | None = None) -> list[Span]:
    """文章/文书的署名行：「李 鸣*」「黄蓬北**」（带星号的作者）。"""
    spans: list[Span] = []
    for m in RE_BYLINE.finditer(text):
        if m.group(1):
            tok = m.group(1) + m.group(2)
            start = m.start(1)
        else:
            tok = m.group(3)
            start = m.start(3)
        if tok in (keep or set()) or tok in data.COMMON_WORDS:
            continue
        if tok[:1] not in data.COMMON_SURNAMES:
            continue
        if not is_valid_name(tok):
            continue
        spans.append(Span(start, start + len(tok), T.PERSON, tok,
                          "byline", PRIORITY["PERSON"]))
    return spans


def find_keep_names(text: str) -> set[str]:
    """法官、律师这些按需求保留的名字，不能被误脱。"""
    keep: set[str] = set()
    for m in RE_KEEP_ANCHOR.finditer(text):
        got = _take_name_after(text, m.end())
        if got:
            keep.add(got[2])
    return keep


def find_partial_names(text: str) -> list[Span]:
    """「姓+某+名」这种半打码的名字：仍然暴露了名里的字，统一成「姓某某」。

    只认「姓某」后面紧跟名字用字（1~2 个），不能把「诉/协/办」这类动词吃进来。
    """
    single, _compound = lexicon.surnames()
    given = lexicon.given_chars()
    spans: list[Span] = []
    for m in re.finditer(r"([\u4e00-\u9fa5])某", text):
        if m.group(1) not in single:
            continue
        i = m.end()
        tail = ""
        for length in (2, 1):
            cand = text[i:i + length]
            if len(cand) == length and all(ch in given for ch in cand):
                tail = cand
                break
        # 「张某某」本身就完整了，别再吃一遍
        if not tail and text[i:i + 1] == "某":
            continue
        token = m.group(0) + tail
        if any(token.endswith(suf) for suf in data.ORG_SUFFIXES):
            continue

        # 上下文校验：后面必须是标点或谓语，或者前面紧挨着身份锚点
        # 否则「浦东新区某小区」会被当成人名「区某」
        end = m.end() + len(tail)
        nxt = text[end:end + 1]
        strong_next = set("、，。；：！？") | set("诉称辩说未已向于到经提主认为系等和与及协办按被将对给由在")
        ok = nxt in strong_next
        if not ok:
            left = text[max(0, m.start() - 12):m.start()]
            ok = any(left.endswith(a) for a in data.REDACT_ANCHORS)
        if not ok:
            continue
        spans.append(Span(m.start(), m.end() + len(tail), T.PERSON, token,
                          "partial-mask", PRIORITY["PERSON"]))
    return spans


def find_persons(text: str, keep: set[str]) -> list[Span]:
    spans: list[Span] = []
    hits: dict[tuple[int, int], Span] = {}

    def add(s: int, e: int, tok: str, rule: str):
        if tok in keep:
            return
        if not is_valid_name(tok):
            return
        hits[(s, e)] = Span(s, e, T.PERSON, tok, rule, PRIORITY["PERSON"])

    for m in RE_PERSON_ANCHOR.finditer(text):
        got = _take_name_after(text, m.end())
        if got:
            add(got[0], got[1], got[2], "anchor")

    for m in RE_BIO.finditer(text):
        tok = m.group("name")
        add(m.start("name"), m.end("name"), tok, "bio")

    for m in RE_NAME_PREDICATE.finditer(text):
        tok = m.group("name")
        if is_valid_name(tok) and plausible_name(tok):
            add(m.start("name"), m.end("name"), tok, "predicate")

    for m in RE_VS.finditer(text):
        # 「甲与乙」这种弱上下文，两边都必须像人名（姓 + 常见名字用字）
        picked = []
        for key in ("a", "b"):
            tok = m.group(key)
            for L in (3, 2):
                t = tok[:L]
                if t in (keep or set()):
                    break
                if is_valid_name(t) and plausible_name(t):
                    picked.append((m.start(key), m.start(key) + L, t))
                    break
        if len(picked) == 2:
            for s, e, t in picked:
                add(s, e, t, "versus")

    return list(hits.values())


def find_addresses(text: str) -> list[Span]:
    spans: list[Span] = []
    for m in RE_ADDR_ANCHOR.finditer(text):
        val = m.group("val").strip()
        if len(val) < 4:
            continue
        start = m.start("val")
        end = start + len(m.group("val").rstrip())
        spans.append(Span(start, end, T.ADDRESS, text[start:end], "addr-anchor", PRIORITY["ADDRESS"]))
    # 锚点已经吃掉的区域不要再被「住…」规则重复截一段
    for m in RE_ADDR_PLAIN.finditer(text):
        val = m.group("val").strip()
        if not any(k in val for k in ("省", "市", "区", "县")):
            continue
        start = m.start("val")
        end = start + len(m.group("val").rstrip())
        if any(start < a.end and a.start < end for a in spans):
            continue
        spans.append(Span(start, end, T.ADDRESS, text[start:end], "addr-plain", PRIORITY["ADDRESS"]))
    return spans


def find_pii(text: str) -> list[Span]:
    spans: list[Span] = []
    for m in RE_ID.finditer(text):
        spans.append(Span(m.start(), m.end(), T.ID_CARD, m.group(0), "id-18", PRIORITY["ID_CARD"]))
    for m in RE_PHONE.finditer(text):
        spans.append(Span(m.start(), m.end(), T.PHONE, m.group(0), "mobile", PRIORITY["PHONE"]))
    for m in RE_LANDLINE.finditer(text):
        spans.append(Span(m.start(), m.end(), T.LANDLINE, m.group(0), "landline", PRIORITY["LANDLINE"]))
    for m in RE_EMAIL.finditer(text):
        spans.append(Span(m.start(), m.end(), T.EMAIL, m.group(0), "email", PRIORITY["EMAIL"]))
    return spans


def resolve(spans: list[Span]) -> list[Span]:
    """优先级高者胜，重叠的低优先级片段丢弃；最后按位置排序。"""
    ordered = sorted(spans, key=lambda s: (-s.priority, -(s.end - s.start)))
    kept: list[Span] = []
    for sp in ordered:
        if any(sp.overlaps(k) for k in kept):
            continue
        kept.append(sp)
    return sorted(kept, key=lambda s: s.start)


def detect(text: str, keep_names: set[str] | None = None) -> list[Span]:
    from . import extras  # 延迟导入，避免与 extras 互相引用

    keep = keep_names if keep_names is not None else find_keep_names(text)
    spans: list[Span] = []
    spans += extras.find_structured(text)
    spans += find_addresses(text)
    org_spans = find_orgs(text)
    spans += org_spans
    spans += find_paren_orgs(text, org_spans)
    spans += find_org_shorts(text, org_spans + [x for x in spans if x.kind == T.ORG])
    spans += find_table_names(text)
    spans += find_enumerated_names(text, keep)
    spans += find_cite_authors(text, keep)
    spans += find_byline_names(text, keep)
    spans += find_standalone(text)
    spans += find_persons(text, keep)
    spans += find_partial_names(text)
    # 已经匿名化过的（张某、丁某某、××）不再二次脱敏，避免「张某→某某某」和「丁某行政」这种碎片
    spans = [sp for sp in spans
             if "×" not in sp.text
             and not (sp.kind == T.PERSON and sp.text in data.COMMON_WORDS)]
    spans = resolve(spans)

    # 白名单最高优先级：法条、法院、案号绝不遮挡（命中就整段跳过）
    preserved = T.find_preserve_spans(text)
    if preserved:
        spans = [s for s in spans
                 if not any(s.start < pe and ps < s.end for ps, pe, _ in preserved)]
    return spans

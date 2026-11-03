# -*- coding: utf-8 -*-
"""结构化检测器：证件号校验、账号、金额、网络标识等（正则 + 校验码，不靠训练）。"""
from __future__ import annotations

import re
from datetime import date

from . import types as T
from .detectors import Span, PRIORITY

# ---------- 校验 ----------
ID_WEIGHTS = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
ID_CHECK = "10X98765432"
PROVINCE_CODES = {
    "11", "12", "13", "14", "15", "21", "22", "23", "31", "32", "33", "34", "35",
    "36", "37", "41", "42", "43", "44", "45", "46", "50", "51", "52", "53", "54",
    "61", "62", "63", "64", "65", "71", "81", "82", "91",
}
USCC_CHARS = "0123456789ABCDEFGHJKLMNPQRTUWXY"
USCC_WEIGHTS = [1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28]


def id18_valid(code: str) -> bool:
    if len(code) != 18 or not code[:17].isdigit():
        return False
    if code[:2] not in PROVINCE_CODES:
        return False
    try:
        y, m, d = int(code[6:10]), int(code[10:12]), int(code[12:14])
        if not (1900 <= y <= 2099):
            return False
        date(y, m, d)
    except ValueError:
        return False
    total = sum(int(c) * w for c, w in zip(code[:17], ID_WEIGHTS))
    return ID_CHECK[total % 11] == code[17].upper()


def normalize_id_candidate(raw: str) -> str:
    """OCR 常见误识别归一：O/o→0，l/I→1，小写 x→X。"""
    table = str.maketrans({"O": "0", "o": "0", "l": "1", "I": "1", "x": "X"})
    return raw.translate(table)


def uscc_valid(code: str) -> bool:
    if len(code) != 18:
        return False
    if code[0] not in USCC_CHARS or code[1] not in USCC_CHARS:
        return False
    body = code[:17]
    if not all(c in USCC_CHARS for c in body):
        return False
    total = sum(USCC_CHARS.index(c) * w for c, w in zip(body, USCC_WEIGHTS))
    expect = (31 - total % 31) % 31
    return USCC_CHARS[expect] == code[17]


# ---------- 正则 ----------
RE_ID_LOOSE = re.compile(r"(?<![0-9A-Za-z])[1-9]\d{5}[0-9OoIlXx]{12}(?![0-9A-Za-z])")
RE_ID15 = re.compile(r"(?<![0-9])[1-9]\d{5}\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}(?![0-9])")
RE_USCC = re.compile(r"(?<![0-9A-Z])[0-9A-HJ-NPQRTUWXY]{2}\d{6}[0-9A-HJ-NPQRTUWXY]{10}(?![0-9A-Z])")
RE_PHONE = re.compile(r"(?<![0-9])1[3-9]\d{9}(?![0-9])")
RE_LANDLINE = re.compile(r"(?<![0-9])0\d{2,3}[-－ ]?\d{7,8}(?![0-9])")
RE_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
RE_PASSPORT = re.compile(r"(?<![A-Za-z0-9])(?:E[A-Z]?\d{7,8}|G\d{8}|P\d{8})(?![A-Za-z0-9])")
RE_HK_MACAO = re.compile(r"(?<![A-Za-z0-9])[CW]\d{8}(?![A-Za-z0-9])")
RE_PLATE = re.compile(
    r"(?<![京沪津渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤青藏川宁琼使领A-Z])"
    r"[京沪津渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤青藏川宁琼]"
    r"[A-HJ-NP-Z][A-HJ-NP-Z0-9]{4,5}[A-HJ-NP-Z0-9挂学警港澳]")
RE_IP = re.compile(r"(?<!\d)(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?!\d)")
RE_URL = re.compile(r"https?://[^\s，。；、）)】\"'<>]+", re.I)
RE_MAC = re.compile(r"(?<![0-9A-Fa-f])(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}(?![0-9A-Fa-f])")
RE_AMOUNT = re.compile(r"(?:人民币|¥|￥)?\s?\d[\d,，]*(?:\.\d{1,2})?\s*(?:万元|亿元|元)")
RE_BIRTH = re.compile(r"(?:出生于|出生日期[：:]?|生于)\s*(\d{4})年(\d{1,2})月(\d{1,2})日")
RE_CASE_FILE = re.compile(r"[（(]\s*\d{4}\s*[）)]\s*[\u4e00-\u9fa5]{0,8}(?:公|检|法|司|狱)[\u4e00-\u9fa5]{0,6}字?\s*第?\s*\d+\s*号")

# 带上下文的弱结构
CTX_QQ = re.compile(r"(?:QQ|qq|扣扣|企鹅)\s*[号：:]?\s*(\d{5,12})")
CTX_WECHAT = re.compile(r"(?:微信号|微信|VX|vx)\s*[号：:]?\s*([A-Za-z][-_A-Za-z0-9]{5,19})")
CTX_BANK = re.compile(r"(?:账号|帐号|账户|卡号|银行账号)\s*[：:]?\s*(\d{12,25})")
CTX_TAX = re.compile(r"(?:纳税人识别号|税号)\s*[：:]?\s*([0-9A-Z]{15,20})")
CTX_APIKEY = re.compile(r"(?:api[_-]?key|token|secret|密钥|令牌)\s*[：:=]?\s*([A-Za-z0-9_\-]{16,})", re.I)
CTX_IMEI = re.compile(r"(?:IMEI|imei|设备号|设备标识)\s*[：:]?\s*(\d{14,16})")
CTX_SEAL = re.compile(r"([\u4e00-\u9fa5]{2,20}(?:公司|单位|委员会|局|院|中心|协会))\s*(?:的)?(?:公章|印章|合同专用章|财务专用章)")
CTX_TAIWAN = re.compile(r"(?:台胞证|台湾居民来往大陆通行证)\s*[号：:]?\s*([0-9]{8,10})")
CTX_HEALTH = re.compile(r"(?:患有|诊断为|确诊|病历载明|病情为)\s*([\u4e00-\u9fa5]{2,12}(?:病|症|癌|障碍|综合征|感染))")
CTX_BIOMETRIC = re.compile(r"(?:指纹|人脸|虹膜|声纹|DNA|基因|掌纹)(?:信息|数据|识别|特征)?")
CTX_PROPERTY = re.compile(r"(?:不动产权证|房产证|产权证|土地证)\s*第?\s*[\u4e00-\u9fa5]?字?\s*\d+\s*号")
CTX_SOCIAL = re.compile(r"(?:社保号|医保号|社保卡号|社会保障号)\s*[：:]?\s*([0-9A-Za-z]{6,20})")
CTX_INSURANCE = re.compile(r"(?:保单号|保险单号)\s*[：:]?\s*([0-9A-Za-z]{6,24})")
CTX_TITLE = re.compile(r"(?:职务|担任)\s*[：:]?\s*([\u4e00-\u9fa5]{2,10}(?:经理|董事|监事|主任|主管|总监|部长|处长|科长))")


def _span(m: re.Match, kind: str, rule: str, group: int = 0) -> Span:
    start, end = m.start(group), m.end(group)
    return Span(start, end, kind, m.group(group), rule, PRIORITY.get(kind, 55), "")


def find_id_near_misses(text: str) -> list[str]:
    """校验码不通过、但长得像身份证的串：不自动脱敏，但要进人工复核清单。"""
    out = []
    for m in RE_ID_LOOSE.finditer(text):
        raw = m.group(0)
        if not id18_valid(normalize_id_candidate(raw)):
            out.append(raw)
    return out


def find_structured(text: str) -> list[Span]:
    spans: list[Span] = []

    # 身份证：先宽松匹配，再归一化 + 校验码
    for m in RE_ID_LOOSE.finditer(text):
        raw = m.group(0)
        code = normalize_id_candidate(raw)
        if id18_valid(code):
            spans.append(Span(m.start(), m.end(), T.ID_CARD, raw, "id18+checksum", 100, ""))
            continue
        if id18_valid(normalize_id_candidate(raw.upper())):
            spans.append(Span(m.start(), m.end(), T.ID_CARD, raw, "id18+checksum", 100, ""))
    for m in RE_ID15.finditer(text):
        if m.group(0)[:2] in PROVINCE_CODES:
            spans.append(Span(m.start(), m.end(), T.ID_CARD, m.group(0), "id15", 99, ""))

    for m in RE_USCC.finditer(text):
        code = m.group(0)
        # 纯 18 位数字会和身份证撞车，只有校验码通过才当统一社会信用代码
        if uscc_valid(code):
            spans.append(_span(m, T.USCC, "uscc+checksum"))

    for m in RE_PHONE.finditer(text):
        spans.append(_span(m, T.PHONE, "mobile"))
    for m in RE_LANDLINE.finditer(text):
        spans.append(_span(m, T.LANDLINE, "landline"))
    for m in RE_EMAIL.finditer(text):
        spans.append(_span(m, T.EMAIL, "email"))
    for m in RE_PASSPORT.finditer(text):
        spans.append(_span(m, T.PASSPORT, "passport"))
    for m in RE_HK_MACAO.finditer(text):
        spans.append(_span(m, T.HK_MACAO, "hk-macao"))
    for m in RE_PLATE.finditer(text):
        spans.append(_span(m, T.PLATE, "plate"))
    for m in RE_IP.finditer(text):
        spans.append(_span(m, T.IP, "ip"))
    for m in RE_URL.finditer(text):
        spans.append(_span(m, T.URL, "url"))
    for m in RE_MAC.finditer(text):
        spans.append(_span(m, T.MAC, "mac"))
    for m in RE_AMOUNT.finditer(text):
        spans.append(_span(m, T.AMOUNT, "amount"))

    # 出生日期（只在"出生"语境里认，避免和判决日期混淆）
    for m in RE_BIRTH.finditer(text):
        spans.append(_span(m, T.BIRTHDATE, "birth-context"))

    # 卷宗编号：公检法内部文号，和"案号"不是一回事
    for m in RE_CASE_FILE.finditer(text):
        spans.append(_span(m, T.CASE_FILE_NO, "case-file"))

    for pattern, kind, rule, grp in [
        (CTX_QQ, T.QQ, "qq-context", 1),
        (CTX_WECHAT, T.WECHAT, "wechat-context", 1),
        (CTX_BANK, T.BANK_ACCOUNT, "bank-context", 1),
        (CTX_TAX, T.TAX_NO, "tax-context", 1),
        (CTX_APIKEY, T.API_KEY, "apikey-context", 1),
        (CTX_IMEI, T.IMEI, "imei-context", 1),
        (CTX_TAIWAN, T.TAIWAN, "taiwan-context", 1),
        (CTX_HEALTH, T.HEALTH, "health-context", 1),
        (CTX_SEAL, T.SEAL, "seal-context", 1),
        (CTX_TITLE, T.TITLE, "title-context", 1),
        (CTX_SOCIAL, T.SOCIAL_SEC, "social-context", 1),
        (CTX_INSURANCE, T.INSURANCE_NO, "insurance-context", 1),
    ]:
        for m in pattern.finditer(text):
            spans.append(_span(m, kind, rule, grp))

    for m in CTX_PROPERTY.finditer(text):
        spans.append(_span(m, T.PROPERTY_NO, "property-context"))
    for m in CTX_BIOMETRIC.finditer(text):
        spans.append(_span(m, T.BIOMETRIC, "biometric-keyword"))

    return spans

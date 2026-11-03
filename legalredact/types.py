# -*- coding: utf-8 -*-
"""实体类型、保留白名单、各模式的默认渲染模板。"""
from __future__ import annotations

import re

# ---------- 类型 ----------
PERSON = "PERSON"
ORG = "ORG"
ADDRESS = "ADDRESS"
AMOUNT = "AMOUNT"
ID_CARD = "ID_CARD"
PASSPORT = "PASSPORT"
HK_MACAO = "HK_MACAO_PASS"
TAIWAN = "TAIWAN_PASS"
PHONE = "PHONE"
LANDLINE = "LANDLINE"
EMAIL = "EMAIL"
QQ = "QQ"
WECHAT = "WECHAT"
PLATE = "PLATE"
BIRTHDATE = "BIRTHDATE"
BANK_ACCOUNT = "BANK_ACCOUNT"
USCC = "USCC"
TAX_NO = "TAX_NO"
IP = "IP"
URL = "URL"
API_KEY = "API_KEY"
IMEI = "IMEI"
MAC = "MAC"
CASE_FILE_NO = "CASE_FILE_NO"
SEAL = "SEAL"
HEALTH = "HEALTH"
BIOMETRIC = "BIOMETRIC"
TITLE = "TITLE"
PROPERTY_NO = "PROPERTY_NO"
SOCIAL_SEC = "SOCIAL_SEC"
INSURANCE_NO = "INSURANCE_NO"

TYPE_CN = {
    PERSON: "姓名", ORG: "单位名称", ADDRESS: "地址", AMOUNT: "金额",
    ID_CARD: "身份证号", PASSPORT: "护照号", HK_MACAO: "港澳通行证",
    TAIWAN: "台胞证", PHONE: "手机号", LANDLINE: "固定电话", EMAIL: "邮箱",
    QQ: "QQ号", WECHAT: "微信号", PLATE: "车牌号", BIRTHDATE: "出生日期",
    BANK_ACCOUNT: "银行账号", USCC: "统一社会信用代码", TAX_NO: "纳税人识别号",
    IP: "IP地址", URL: "网址", API_KEY: "密钥/Token", IMEI: "设备标识",
    MAC: "MAC地址", CASE_FILE_NO: "卷宗编号", SEAL: "印章信息",
    HEALTH: "健康状况", BIOMETRIC: "生物识别信息", TITLE: "职务",
    PROPERTY_NO: "产权证号", SOCIAL_SEC: "社保/医保号", INSURANCE_NO: "保单号",
}

# 同一替身必须唯一，所以这些类型要加序号
INDEXED_TYPES = {PERSON, ORG, ADDRESS, AMOUNT, BANK_ACCOUNT, USCC, TAX_NO,
                 CASE_FILE_NO, SEAL, HEALTH, TITLE, PROPERTY_NO, SOCIAL_SEC,
                 INSURANCE_NO}

# ---------- 强制保留白名单（优先级最高，命中就绝不脱敏）----------
PRESERVE_RULES = [
    # 《民法典》第577条 / 《中华人民共和国民法典》第五百七十七条
    ("law_article", re.compile(r"《[^》]{1,40}》\s*第?[一二三四五六七八九十百千零〇\d]*条?")),
    # （2025）京0105民初12345号 / (2025)京0105民初12345号
    ("case_number", re.compile(
        r"[（(]\s*\d{4}\s*[）)]\s*[\u4e00-\u9fa5]{0,3}\d{0,6}[\u4e00-\u9fa5]{1,4}\d+号")),
    # 北京市朝阳区人民法院 / 上海市人民检察院 / 北京仲裁委员会
    ("court", re.compile(r"[\u4e00-\u9fa5]{2,12}(?:人民法院|人民检察院|仲裁委员会|铁路运输法院)")),
    # 指导案例 24 号 / 指导性案例第24号
    ("guiding_case", re.compile(r"指导(?:性)?案例\s*第?\s*\d+\s*号?")),
    # 法条编号：第577条（不带书名号时也保留，但要求前面有「依照/根据/第…条」语境）
    ("article_no", re.compile(r"(?<=[照据])第[一二三四五六七八九十百千零〇\d]+条(?:之[一二三四五六七八九十]+)?")),
]


def find_preserve_spans(text: str) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    for name, pattern in PRESERVE_RULES:
        for m in pattern.finditer(text):
            spans.append((m.start(), m.end(), name))
    return spans


# ---------- 模式渲染模板 ----------
MASK_TEMPLATES = {
    PERSON: "{surname}某某",
    ORG: "某某公司",
    ADDRESS: "某地址",
    AMOUNT: "某金额",
    ID_CARD: "×" * 18,
    PASSPORT: "×" * 8,
    HK_MACAO: "×" * 9,
    TAIWAN: "×" * 8,
    PHONE: "×××××××××××",
    LANDLINE: "×××－××××××××",
    EMAIL: "×××@×××",
    QQ: "××××××",
    WECHAT: "××××××",
    PLATE: "××××××",
    BIRTHDATE: "××××年××月××日",
    BANK_ACCOUNT: "××××××××××××××××",
    USCC: "×" * 18,
    TAX_NO: "××××××××××××××",
    IP: "×.×.×.×",
    URL: "×××",
    API_KEY: "××××××××",
    IMEI: "×××××××××××××××",
    MAC: "××:××:××:××:××:××",
    CASE_FILE_NO: "××字〔××〕××号",
    SEAL: "某单位印章",
    HEALTH: "某疾病",
    BIOMETRIC: "××××信息",
    TITLE: "某职务", PROPERTY_NO: "××字第××号", SOCIAL_SEC: "××××××",
    INSURANCE_NO: "××××××××",
}

ROLE_TEMPLATES = {
    PERSON: "当事人{n}",
    ORG: "某某公司{n}",
    ADDRESS: "涉案地址{n}",
    AMOUNT: "涉案金额{n}",
    ID_CARD: "证件号{n}",
    PASSPORT: "证件号{n}",
    HK_MACAO: "证件号{n}",
    TAIWAN: "证件号{n}",
    PHONE: "号码{n}",
    LANDLINE: "号码{n}",
    EMAIL: "邮箱号{n}",
    QQ: "号码{n}",
    WECHAT: "号码{n}",
    PLATE: "号牌{n}",
    BIRTHDATE: "出生日期{n}",
    BANK_ACCOUNT: "银行账号{n}",
    USCC: "统一代码{n}",
    TAX_NO: "税号{n}",
    IP: "IP地址{n}",
    URL: "网址{n}",
    API_KEY: "密钥{n}",
    IMEI: "设备标识{n}",
    MAC: "MAC地址{n}",
    CASE_FILE_NO: "卷宗编号{n}",
    SEAL: "印章信息{n}",
    HEALTH: "健康状况{n}",
    BIOMETRIC: "生物识别信息{n}",
    TITLE: "职务{n}", PROPERTY_NO: "产权证号{n}", SOCIAL_SEC: "社保号{n}",
    INSURANCE_NO: "保单号{n}",
}

REDACT_FILL = "████"

# 角色关键词：越靠前优先级越高
ROLE_KEYWORDS = [
    ("原告", "原告方"),
    ("被告", "被告方"),
    ("上诉人", "上诉方"),
    ("被上诉人", "被上诉方"),
    ("申请人", "申请方"),
    ("被申请人", "被申请方"),
    ("第三人", "第三人"),
    ("被执行人", "被执行人"),
    ("申请执行人", "申请执行人"),
    ("被害人", "被害人"),
    ("被告人", "被告人"),
    ("证人", "证人"),
    ("辩护人", "辩护人"),
    ("委托诉讼代理人", "代理人"),
    ("法定代表人", "法定代表人"),
    ("负责人", "负责人"),
    ("经营者", "经营者"),
]

# -*- coding: utf-8 -*-
"""姓氏 / 名字用字 / 地名词表：优先读 jcseg 词库，缺失时回落到内置表。"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from . import data

LEX_DIR = Path(__file__).resolve().parent / "lexicon"

SKIP_HEADERS = {"CN_LNAME", "CN_SNAME", "CJK_WORDS"}


def _lines(name: str) -> list[str]:
    path = LEX_DIR / name
    if not path.exists():
        return []
    out = []
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line in SKIP_HEADERS:
            continue
        out.append(line)
    return out


@lru_cache(maxsize=1)
def surnames() -> tuple[frozenset[str], frozenset[str]]:
    """返回 (单姓, 复姓)。词库里用 # 开头的姓是 jcseg 认为有歧义而注释掉的，这里同样跳过。"""
    single: set[str] = set()
    compound: set[str] = set()
    section = "single"
    for line in _lines("lex-lname.lex"):
        if line == "#复姓":
            section = "compound"
            continue
        if line.startswith("#"):
            # 「#向」「#和」这种是有歧义被注释掉的单姓，不启用
            continue
        if len(line) == 1 and section == "single":
            single.add(line)
        elif len(line) >= 2:
            compound.add(line)

    single |= set(data.SINGLE_SURNAMES) - set()
    compound |= set(data.COMPOUND_SURNAMES)
    return frozenset(single), frozenset(compound)


@lru_cache(maxsize=1)
def given_chars() -> frozenset[str]:
    chars = {line[0] for line in _lines("lex-sname.lex") if len(line) == 1}
    chars |= data.GIVEN_NAME_CHARS
    return frozenset(chars)


@lru_cache(maxsize=1)
def place_words() -> frozenset[str]:
    words = set()
    for line in _lines("lex-cn-place.lex"):
        word = line.split("/")[0]
        if 2 <= len(word) <= 8:
            words.add(word)
    return frozenset(words)


@lru_cache(maxsize=1)
def stopwords() -> frozenset[str]:
    words = set()
    for line in _lines("lex-stopword.lex"):
        word = line.split("/")[0]
        if len(word) >= 2:
            words.add(word)
    return frozenset(words)


@lru_cache(maxsize=1)
def company_words() -> frozenset[str]:
    words = set()
    for line in _lines("lex-company.lex"):
        word = line.split("/")[0]
        if len(word) >= 2:
            words.add(word)
    return frozenset(words)

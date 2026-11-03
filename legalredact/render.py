# -*- coding: utf-8 -*-
"""三种渲染器：mask（掩码）/ role（角色化）/ redact（涂黑）。"""
from __future__ import annotations

from collections import defaultdict

from . import types as T

CN_NUM = "零一二三四五六七八九十"


def _suffix(n: int) -> str:
    if n <= 1:
        return ""
    if n <= 10:
        return CN_NUM[n]
    return str(n)


def detect_role(text: str, start: int, window: int = 40) -> tuple[str, str] | None:
    """从实体位置往前找最近的职务/身份词，返回 (关键词, 角色标签)。"""
    left = text[max(0, start - window):start]
    best: tuple[str, str] | None = None
    best_pos = -1
    for kw, label in sorted(T.ROLE_KEYWORDS, key=lambda kv: -len(kv[0])):
        idx = left.rfind(kw)
        if idx >= 0 and idx > best_pos:
            best_pos = idx
            best = (kw, label)
    return best


class Renderer:
    def __init__(self, mode: str = "mask", mask_style: str = "surname",
                 redact_len: str = "fixed"):
        if mode not in ("mask", "role", "redact"):
            raise ValueError(f"未知模式：{mode}")
        self.mode = mode
        self.mask_style = mask_style      # surname | full
        self.redact_len = redact_len      # fixed | same
        self.by_key: dict[tuple[str, str], dict] = {}
        self.counter: dict[str, int] = defaultdict(int)
        self.seq = 0
        self.replacements: set[str] = set()

    # ---------- 对外 ----------
    def assign(self, kind: str, text: str, context: str = "", pos: int | None = None,
               sub: str = "") -> dict:
        key = (kind, text)
        if key in self.by_key:
            return self.by_key[key]
        self.seq += 1
        eid = f"E{self.seq}"
        role = None
        if self.mode == "role":
            at = pos if pos is not None else (context.find(text) if context else 0)
            role = detect_role(context, at)
        # 「原告：原告方1」这种重复，前缀已经点明身份了，就退回中性叫法
        dup = False
        if role and pos is not None:
            prefix = context[max(0, pos - 8):pos]
            dup = prefix.endswith(role[0])
        replacement = self.render(kind, text, eid, role, sub, dup)
        record = {
            "entity_id": eid,
            "type": kind,
            "original": text,
            "replacement": replacement,
            "mode": self.mode,
            "reversible": self.mode != "redact",
            "role": role[1] if role else "",
        }
        self.by_key[key] = record
        self.replacements.add(replacement)
        return record

    # ---------- 渲染 ----------
    def render(self, kind: str, text: str, eid: str, role, sub: str, dup: bool = False) -> str:
        if self.mode == "redact":
            return self._render_redact(text)
        if self.mode == "role":
            return self._render_role(kind, text, role, dup)
        return self._render_mask(kind, text, sub)

    def _uniq(self, kind: str, base: str) -> str:
        """只有「同一个替身被第二个实体占用」时才加序号；不同姓氏不会互相顶。"""
        if kind not in T.INDEXED_TYPES:
            return base
        n = self.counter[base] + 1
        self.counter[base] = n
        return base + _suffix(n)

    def _render_mask(self, kind: str, text: str, sub: str) -> str:
        tpl = T.MASK_TEMPLATES.get(kind, "××××")
        if kind == T.PERSON:
            if self.mask_style == "full":
                return self._uniq(kind, "×××")
            surname = text[:2] if len(text) >= 2 and text[:2] in _compound_surnames() else text[:1]
            return self._uniq(kind, f"{surname}某某")
        if kind == T.ORG:
            typ = sub or "公司"
            return self._uniq(kind, f"某某某{typ}")
        if kind in (T.ADDRESS, T.AMOUNT, T.BANK_ACCOUNT, T.USCC, T.TAX_NO,
                    T.CASE_FILE_NO, T.SEAL, T.HEALTH, T.TITLE):
            base = tpl.format(type=(sub or ""))
            return self._uniq(kind, base) if kind in T.INDEXED_TYPES else base
        return tpl

    def _render_role(self, kind: str, text: str, role, dup: bool = False) -> str:
        label = role[1] if role else ""
        n = self.counter["role_" + kind] + 1
        self.counter["role_" + kind] = n
        # 角色替身一律带数字后缀：避免「被告方」+「辩称」被再识别成「方辩」这种假人名
        num = str(n)
        if kind == T.PERSON:
            if dup and role:
                kw, lab = role
                # 前面已经写了「原告」，这里只补「方1」，拼起来正好是「原告方1」
                if lab.startswith(kw) and len(lab) > len(kw):
                    base = lab[len(kw):] + num
                else:
                    base = f"当事人{num}"
            else:
                base = (label + num) if label else f"{T.ROLE_TEMPLATES[kind].format(n=num)}"
        elif kind == T.ORG:
            if label.endswith("方"):
                base = f"{label[:-1]}公司{num}"
            elif label in ("代理人", "辩护人"):
                base = f"某律师事务所{num}"
            elif label in ("法定代表人", "负责人", "经营者"):
                base = f"某某公司{num}"
            else:
                base = f"{label}公司{num}" if label else f"某某公司{num}"
        else:
            base = T.ROLE_TEMPLATES.get(kind, "某某{n}").format(n=num)
        return base

    def _render_redact(self, text: str) -> str:
        if self.redact_len == "same":
            return T.REDACT_FILL[0] * len(text)
        return T.REDACT_FILL

    # ---------- 输出 ----------
    def entities(self) -> list[dict]:
        return sorted(self.by_key.values(), key=lambda e: e["entity_id"])


def _compound_surnames() -> frozenset[str]:
    from . import lexicon
    return lexicon.surnames()[1]

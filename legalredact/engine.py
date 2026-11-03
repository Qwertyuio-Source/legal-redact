# -*- coding: utf-8 -*-
"""脱敏引擎：实体识别 → 保留白名单 → 统一映射 → 按 mode 渲染。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import detectors, types as T
from .render import Renderer

# 旧的开关名（config.json 里那套）映射到新类型
LEGACY_SWITCH = {
    "person": T.PERSON, "org": T.ORG, "address": T.ADDRESS,
    "id": T.ID_CARD, "phone": T.PHONE, "email": T.EMAIL,
}


@dataclass
class Issue:
    kind: str          # leftover / candidate
    text: str
    note: str
    block: int = -1


@dataclass
class BlockResult:
    text: str
    spans: list[tuple] = field(default_factory=list)  # (start, end, repl, kind, entity_id)
    redacted: str = ""
    pos_spans: list[list] = field(default_factory=list)


class Redactor:
    def __init__(self, config: dict | None = None, mode: str = "mask",
                 mask_style: str = "surname", redact_len: str = "fixed"):
        self.config = config or {}
        self.mode = mode
        self.renderer = Renderer(mode, mask_style, redact_len)
        self.rows: list[dict] = []
        self.issues: list[Issue] = []
        self.block_spans: list[dict] = []
        self.disabled = self._disabled_types(self.config)

    @staticmethod
    def _disabled_types(config: dict) -> set[str]:
        enabled = config.get("enable", {})
        disabled: set[str] = set()
        for key, value in enabled.items():
            if value is False:
                disabled.add(LEGACY_SWITCH.get(key, key))
        return disabled

    # ---------- 主流程 ----------
    def prepare(self, texts: list[str]) -> list[BlockResult]:
        full = "\n".join(texts)
        keep = detectors.find_keep_names(full)
        global_spans = detectors.detect(full, keep)

        known: dict[str, detectors.Span] = {}
        for sp in global_spans:
            if sp.kind in (T.PERSON, T.ORG):
                known.setdefault(sp.text, sp)

        results: list[BlockResult] = []
        for bi, text in enumerate(texts):
            spans = detectors.detect(text, keep)
            for tok, meta in known.items():
                if tok not in text:
                    continue
                for m in re.finditer(re.escape(tok), text):
                    spans.append(detectors.Span(m.start(), m.end(), meta.kind, tok,
                                                "known-entity", meta.priority, meta.sub))
            spans = detectors.resolve(spans)
            spans = [s for s in spans if s.kind not in self.disabled]

            out: list[tuple] = []
            for sp in spans:
                rec = self.renderer.assign(sp.kind, sp.text, context=text, pos=sp.start,
                                           sub=sp.sub)
                out.append((sp.start, sp.end, rec["replacement"], sp.kind, rec["entity_id"]))
                self.rows.append({
                    "block": bi + 1, "type": sp.kind, "entity_id": rec["entity_id"],
                    "original": sp.text, "replacement": rec["replacement"],
                    "rule": sp.rule, "start": sp.start,
                })
            br = BlockResult(text=text, spans=out)
            br.redacted, br.pos_spans = self._apply_and_record(text, out)
            results.append(br)

        self._self_check(results)
        self._collect_candidates(full, keep)
        self.block_spans = [
            {"index": i, "spans": br.pos_spans}
            for i, br in enumerate(results) if br.pos_spans
        ]
        return results

    # ---------- 自检 ----------
    def _self_check(self, results: list[BlockResult]) -> None:
        replacements = self.renderer.replacements
        for bi, br in enumerate(results):
            covered = [(s[0], s[1]) for s in br.pos_spans]
            for sp in detectors.detect(br.redacted):
                # 替身本身（比如「被告方三」）不要再被当成新实体
                if any(sp.start < b and a < sp.end for a, b in covered):
                    continue
                if sp.text in replacements or "某" in sp.text or "×" in sp.text:
                    continue
                if sp.kind in (T.ID_CARD, T.PHONE, T.EMAIL, T.LANDLINE,
                               T.USCC, T.BANK_ACCOUNT):
                    continue
                self.issues.append(Issue("leftover", sp.text,
                                         f"二次扫描仍有残留（{sp.rule}）", bi + 1))

    @staticmethod
    def _apply_and_record(text: str, spans) -> tuple[str, list[list]]:
        out: list[str] = []
        pos_spans: list[list] = []
        pos = 0
        delta = 0
        for start, end, rep, kind, _eid in sorted(spans, key=lambda s: s[0]):
            out.append(text[pos:start])
            new_start = start + delta
            pos_spans.append([new_start, new_start + len(rep), kind, text[start:end]])
            out.append(rep)
            delta += len(rep) - (end - start)
            pos = end
        out.append(text[pos:])
        return "".join(out), pos_spans

    def _collect_candidates(self, full: str, keep: set[str]) -> None:
        from . import data, extras
        for bad in extras.find_id_near_misses(full):
            self.issues.append(Issue("candidate", bad, "18 位但校验码不过，疑似身份证，请人工确认"))
        for name in sorted(keep):
            self.issues.append(Issue("candidate", name, "法官/律师，按需求保留，请确认"))
        for m in detectors.RE_ORG_SUFFIX.finditer(full):
            cand = full[max(0, m.start() - 12):m.end()]
            if any(p in cand for p in data.ORG_KEEP_PATTERNS):
                self.issues.append(Issue("candidate", cand, "机关/法院类，按需求保留，请确认"))

    # ---------- 输出 ----------
    def mapping(self) -> dict:
        return {
            "version": 3,
            "mode": self.mode,
            "entities": self.renderer.entities(),
            "blocks": self.block_spans,
        }

    def apply_text(self, br: BlockResult) -> str:
        return br.redacted

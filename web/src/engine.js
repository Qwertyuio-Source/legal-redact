/* 脱敏引擎：识别 → 白名单 → 统一映射 → 按 mode 渲染（与 Python engine.py 对齐） */
(function (root) {
  const D = root.LR_DETECT;
  const T = root.LR_TYPES;
  const R = root.LR_RULES;

  class Renderer {
    constructor(mode, maskStyle, redactLen) {
      this.mode = mode || "mask";
      this.maskStyle = maskStyle || "surname";
      this.redactLen = redactLen || "fixed";
      this.byKey = new Map();
      this.counter = new Map();
      this.seq = 0;
      this.replacements = new Set();
    }
    next(kind, base) {
      if (!T.INDEXED.has(kind)) return base;
      const n = (this.counter.get(base) || 0) + 1;
      this.counter.set(base, n);
      return base + T.suffix(n);
    }
    assign(kind, text, context, pos, sub) {
      const key = kind + "\u0000" + text;
      if (this.byKey.has(key)) return this.byKey.get(key);
      this.seq += 1;
      const eid = "E" + this.seq;
      let role = null;
      if (this.mode === "role") role = detectRole(context || "", pos == null ? 0 : pos);
      let replacement;
      if (this.mode === "redact") replacement = this.redactText(text);
      else if (this.mode === "role") replacement = this.renderRole(kind, text, role, sub, context, pos);
      else replacement = this.renderMask(kind, text, sub);
      const rec = {
        entity_id: eid, type: kind, original: text, replacement,
        mode: this.mode, reversible: this.mode !== "redact",
        role: role ? role[1] : "",
      };
      this.byKey.set(key, rec);
      this.replacements.add(replacement);
      return rec;
    }
    renderMask(kind, text, sub) {
      if (kind === T.PERSON) {
        if (this.maskStyle === "full") return this.next(kind, "×××");
        const surname = R.COMPOUND_SURNAMES.includes(text.slice(0, 2)) ? text.slice(0, 2) : text.slice(0, 1);
        return this.next(kind, surname + "某某");
      }
      if (kind === T.ORG) return this.next(kind, "某某某" + (sub || "公司"));
      const base = T.MASK[kind] || "××××";
      return T.INDEXED.has(kind) ? this.next(kind, base) : base;
    }
    renderRole(kind, text, role, sub, context, pos) {
      const label = role ? role[1] : "";
      const n = (this.counter.get("role_" + kind) || 0) + 1;
      this.counter.set("role_" + kind, n);
      const num = String(n);
      if (kind === T.PERSON) {
        let dup = false;
        if (role && pos != null) {
          const prefix = (context || "").slice(Math.max(0, pos - 8), pos);
          dup = prefix.endsWith(role[0]);
        }
        if (dup && role) {
          const [kw, lab] = role;
          if (lab.startsWith(kw) && lab.length > kw.length) return lab.slice(kw.length) + num;
          return "当事人" + num;
        }
        return label ? label + num : "当事人" + num;
      }
      if (kind === T.ORG) {
        if (label.endsWith("方")) return label.slice(0, -1) + "公司" + num;
        if (label === "代理人" || label === "辩护人") return "某律师事务所" + num;
        if (["法定代表人", "负责人", "经营者"].includes(label)) return "某某公司" + num;
        return (label ? label + "公司" : "某某公司") + num;
      }
      const tpl = T.ROLE[kind] || "某某{n}";
      return tpl.replace("{n}", num);
    }
    redactText(text) {
      return this.redactLen === "same" ? "█".repeat(text.length) : "████";
    }
    entities() {
      return [...this.byKey.values()].sort((a, b) =>
        a.entity_id.localeCompare(b.entity_id, "en", { numeric: true }));
    }
  }

  function detectRole(text, pos) {
    const left = text.slice(Math.max(0, pos - 40), pos);
    let best = null, bestPos = -1;
    for (const [kw, label] of T.ROLE_KEYWORDS) {
      const idx = left.lastIndexOf(kw);
      if (idx >= 0 && idx > bestPos) { bestPos = idx; best = [kw, label]; }
    }
    return best;
  }

  class Redactor {
    constructor(config, mode, maskStyle, redactLen) {
      config = config || {};
      this.mode = mode || "mask";
      this.renderer = new Renderer(this.mode, maskStyle, redactLen);
      this.rows = [];
      this.issues = [];
      this.blockSpans = [];
      this.disabled = new Set();
      const enable = config.enable || {};
      const legacy = { person: T.PERSON, org: T.ORG, address: T.ADDRESS,
                       id: T.ID_CARD, phone: T.PHONE, email: T.EMAIL };
      for (const [k, v] of Object.entries(enable)) {
        if (v === false) this.disabled.add(legacy[k] || k);
      }
    }

    prepare(texts) {
      const full = texts.join("\n");
      const keep = D.findKeepNames(full);
      const globalSpans = D.detect(full, keep);
      const known = new Map();
      for (const sp of globalSpans) {
        if ((sp.kind === T.PERSON || sp.kind === T.ORG) && !known.has(sp.text)) {
          known.set(sp.text, sp);
        }
      }

      const results = [];
      texts.forEach((text, bi) => {
        let spans = D.detect(text, keep);
        for (const [tok, meta] of known) {
          let from = 0;
          while (true) {
            const idx = text.indexOf(tok, from);
            if (idx < 0) break;
            spans.push({ start: idx, end: idx + tok.length, kind: meta.kind, text: tok,
                         rule: "known-entity", priority: meta.priority, sub: meta.sub });
            from = idx + tok.length;
          }
        }
        spans = D.resolve(spans).filter((s) => !this.disabled.has(s.kind));

        const out = [];
        for (const sp of spans) {
          const rec = this.renderer.assign(sp.kind, sp.text, text, sp.start, sp.sub);
          out.push([sp.start, sp.end, rec.replacement, sp.kind]);
          this.rows.push({
            block: bi + 1, type: sp.kind, entity_id: rec.entity_id,
            original: sp.text, replacement: rec.replacement,
            rule: sp.rule, start: sp.start,
          });
        }
        const [redacted, posSpans] = Redactor.applyAndRecord(text, out);
        results.push({ text, spans: out, redacted, posSpans });
        if (posSpans.length) this.blockSpans.push({ index: bi, spans: posSpans });
      });

      this.results = results;
      this.selfCheck(results);
      this.collectCandidates(full, keep);
      return results;
    }

    static applyAndRecord(text, spans) {
      const sorted = spans.slice().sort((a, b) => a[0] - b[0]);
      let out = "", pos = 0, delta = 0;
      const posSpans = [];
      for (const [start, end, rep, kind] of sorted) {
        out += text.slice(pos, start);
        const ns = start + delta;
        posSpans.push([ns, ns + rep.length, kind, text.slice(start, end)]);
        out += rep;
        delta += rep.length - (end - start);
        pos = end;
      }
      out += text.slice(pos);
      return [out, posSpans];
    }

    selfCheck(results) {
      results.forEach((br, bi) => {
        const covered = br.posSpans.map((s) => [s[0], s[1]]);
        for (const sp of D.detect(br.redacted)) {
          if (covered.some(([a, b]) => sp.start < b && a < sp.end)) continue;
          if (this.renderer.replacements.has(sp.text) || sp.text.includes("某") || sp.text.includes("×")) continue;
          this.issues.push({ kind: "leftover", text: sp.text,
                             note: "二次扫描仍有残留（" + sp.rule + "）", block: bi + 1 });
        }
      });
    }

    collectCandidates(full, keep) {
      for (const name of keep) {
        this.issues.push({ kind: "candidate", text: name, note: "法官/律师，按需求保留，请确认" });
      }
      for (const bad of root.LR_EXTRAS.findIdNearMisses(full)) {
        this.issues.push({ kind: "candidate", text: bad,
                           note: "18 位但校验码不过，疑似身份证，请人工确认" });
      }
      const re = new RegExp(R.ORG_SUFFIXES.slice().sort((a, b) => b.length - a.length)
        .map((s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|"), "g");
      let m;
      while ((m = re.exec(full))) {
        const cand = full.slice(Math.max(0, m.index - 12), m.index + m[0].length);
        if (R.ORG_KEEP_PATTERNS.some((p) => cand.includes(p))) {
          this.issues.push({ kind: "candidate", text: cand, note: "机关/法院类，按需求保留，请确认" });
        }
      }
    }

    mapping() {
      return { version: 3, mode: this.mode,
               entities: this.renderer.entities(), blocks: this.blockSpans };
    }
  }

  root.LR_ENGINE = { Redactor, Renderer, detectRole };
})(typeof window !== "undefined" ? window : globalThis);

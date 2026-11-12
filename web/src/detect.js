/* 检测器：从文本里找出需要脱敏的片段（与 Python 版逻辑一一对应） */
(function (root) {
  const R = root.LR_RULES;
  const S = R.set;
  const T = root.LR_TYPES;
  const X = root.LR_EXTRAS;

  const PRIORITY = {
    ID_CARD: 100, USCC: 99, BANK_ACCOUNT: 97, PASSPORT: 96, HK_MACAO_PASS: 95,
    TAIWAN_PASS: 94, PHONE: 98, LANDLINE: 97, EMAIL: 96, QQ: 95, WECHAT: 95,
    PLATE: 93, IP: 92, URL: 91, API_KEY: 98, IMEI: 93, MAC: 92, TAX_NO: 96,
    CASE_FILE_NO: 90, SEAL: 88, BIOMETRIC: 87, HEALTH: 86, BIRTHDATE: 85,
    AMOUNT: 84, ADDRESS: 80, TITLE: 75, ORG: 70, PERSON: 60,
  };

  const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const alt = (arr) => arr.slice().sort((a, b) => b.length - a.length).map(esc).join("|");

  const RE_ID = /(?<![0-9])[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?![0-9])/g;
  const RE_PHONE = /(?<![0-9])1[3-9]\d{9}(?![0-9])/g;
  const RE_LANDLINE = /(?<![0-9])0\d{2,3}[- ]?\d{7,8}(?![0-9])/g;
  const RE_EMAIL = /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g;

  const RE_ORG_SUFFIX = new RegExp(alt(R.ORG_SUFFIXES), "g");
  const RE_PERSON_ANCHOR = new RegExp(
    `(?:${alt(R.REDACT_ANCHORS)})\\s*(?:（[^）]{0,20}）)?\\s*[：:]?\\s*`, "g");
  const RE_KEEP_ANCHOR = new RegExp(`(?:${alt(R.KEEP_ANCHORS)})\\s*[：:]?\\s*`, "g");
  const RE_BIO = /([\u4e00-\u9fa5]{2,4})(?=[，,]\s*(?:男|女|汉族|满族|回族|蒙古族|壮族|19\d{2}年|20\d{2}年|系|该公司|担任|任该|为该公司))/g;
  const RE_VS = /([\u4e00-\u9fa5]{2,3})\s*(?:与|诉|和|及)\s*([\u4e00-\u9fa5]{2,3})/g;
  const RE_NAME_PREDICATE = /([\u4e00-\u9fa5]{2,3})(?=(?:到庭|辩称|诉称|陈述|未到庭|参加诉讼|出具|签字|签收|确认|表示|称|到案))/g;

  const RE_ADDR_ANCHOR = new RegExp(
    `(?:${alt(R.ADDRESS_ANCHORS)})\\s*[：:]?\\s*([^\\n，。；;]{4,80})`, "g");
  const RE_ADDR_PLAIN = /(?<![居现所])住\s*([\u4e00-\u9fa5]{2,60}?(?:省|市|区|县)[^\n，。；;]{0,40})/g;

  function is_valid_name(tok) {
    if (!tok || tok.length < 2 || tok.length > 4) return false;
    if (tok.includes("某")) return false;
    for (const ch of tok) if (S.NAME_REJECT_CHARS.has(ch)) return false;
    if (S.NAME_STOPWORDS.has(tok)) return false;
    if (S.PLACE_TAIL.has(tok[tok.length - 1])) return false;
    for (const suf of R.ORG_SUFFIXES) if (tok.endsWith(suf)) return false;
    const head2 = tok.slice(0, 2);
    if (R.COMPOUND_SURNAMES.includes(head2)) {
      const rest = tok.slice(2);
      if (rest.length < 1 || rest.length > 2) return false;
      for (const ch of rest) if (S.NAME_REJECT_CHARS.has(ch)) return false;
      return true;
    }
    return S.SINGLE_SURNAMES.has(tok[0]);
  }

  function plausible_name(tok) {
    const rest = R.COMPOUND_SURNAMES.includes(tok.slice(0, 2)) ? tok.slice(2) : tok.slice(1);
    if (!rest) return false;
    for (const ch of rest) if (!S.GIVEN_NAME_CHARS.has(ch)) return false;
    return true;
  }

  function trimOrgCandidate(cand) {
    let cut = 0;
    for (const tok of R.LEAD_STOP_TOKENS) {
      const idx = cand.lastIndexOf(tok);
      if (idx >= 0) cut = Math.max(cut, idx + tok.length);
    }
    return cand.slice(cut).replace(/^[，。、；：（）()《》“”"'\s]+/, "").trim();
  }

  function orgOk(cand, suffix, text, end) {
    if (cand.length < 2) return false;
    if (S.GENERIC_ORG_WORDS.has(cand)) return false;
    for (const bad of R.ORG_KEEP_PATTERNS) if (cand.includes(bad)) return false;
    if (cand.endsWith("等")) return false;
    if (end < text.length && "法规定条章程".includes(text[end])) return false;
    const body = cand.endsWith(suffix) ? cand.slice(0, -suffix.length) : cand;
    return body.length >= 2;
  }

  const RE_PAREN_CITY = /[\u4e00-\u9fa5]{2,8}[（(][\u4e00-\u9fa5]{2,4}[）)][\u4e00-\u9fa5]{2,10}/g;
  const ORG_TAIL_STOP = new Set("为所的进行由与和及在是，。、；：！？\n".split(""));
  const ORG_LEAD = new Set("由与和及在是对向给被让使据经项其该".split(""));
  const ORG_TAIL_VERB = new Set("迟未已将正均都也仍却则并且后前中内上下还又再才就".split(""));

  /** 「XX（北京）YY」这种不以「公司」结尾的单位名，括号+地名是很强的信号 */
  function findParenOrgs(text, taken) {
    const out = [];
    RE_PAREN_CITY.lastIndex = 0;
    let m;
    while ((m = RE_PAREN_CITY.exec(text))) {
      let tok = m[0];
      const cp = Math.max(tok.indexOf("）"), tok.indexOf(")"));
      if (cp < 0) continue;
      let cut = tok.length;
      for (let i = cp + 1; i < tok.length; i++) {
        if (ORG_TAIL_STOP.has(tok[i]) || ORG_TAIL_VERB.has(tok[i])) { cut = i; break; }
      }
      cut = Math.min(cut, cp + 1 + 8);
      tok = tok.slice(0, cut);
      let nLead = 0;
      while (tok.length && ORG_LEAD.has(tok[0])) { tok = tok.slice(1); nLead++; }
      const cp2 = Math.max(tok.indexOf("）"), tok.indexOf(")"));
      if (cp2 < 0) continue;
      const tail = tok.length - cp2 - 1;
      if (tail < 2 || tail > 10) continue;
      if (R.ORG_SUFFIXES.some((suf) => tok.endsWith(suf))) continue;
      if (T.PRESERVE.some(([, rx]) => rx.test(tok))) continue;
      const start = m.index + nLead;
      const end = start + tok.length;
      if (taken && taken.some((t) => start < t.end && t.start < end)) continue;
      out.push({ start, end, kind: T.ORG, text: tok,
                 rule: "paren-city", priority: PRIORITY.ORG, sub: "" });
    }
    return out;
  }

  const ORG_SHORT_SUFFIX = ["集团", "公司", "团队", "文化", "传媒", "工作室", "实业", "科技", "事务所"];
  const CITY_LEAD = ["北京", "上海", "天津", "重庆", "广州", "深圳", "杭州", "南京", "成都",
    "武汉", "西安", "苏州", "郑州", "长沙", "青岛", "济南", "合肥", "福州", "厦门", "宁波",
    "无锡", "大连", "沈阳", "哈尔滨", "长春", "昆明", "南昌"];

  function orgCore(name) {
    let body = name;
    for (const c of CITY_LEAD) { if (body.startsWith(c)) { body = body.slice(c.length); break; } }
    const m = /^[\u4e00-\u9fa5]{2}/.exec(body);
    return m ? m[0] : "";
  }

  /** 简称：全称「卓燃时尚（北京）文化传媒」，文中又出现「卓燃团队」「卓燃文化」 */
  const TABLE_SPLIT = /[\t|\uFF5C]/;

  /** 表格行首列常见「姓名\t职务\t身份证」，没锚点也要认 */
  function findTableNames(text) {
    const out = [];
    let pos = 0;
    for (const line of text.split("\n")) {
      const lineStart = pos;
      pos += line.length + 1;
      if (!TABLE_SPLIT.test(line)) continue;
      for (const cell of line.split(TABLE_SPLIT)) {
        const tok = cell.trim();
        if (tok.length < 2 || tok.length > 3) continue;
        if (S.NAME_STOPWORDS.has(tok)) continue;
        if (!is_valid_name(tok)) continue;
        const idx = line.indexOf(tok);
        if (idx < 0) continue;
        out.push({ start: lineStart + idx, end: lineStart + idx + tok.length,
                   kind: T.PERSON, text: tok, rule: "table-cell",
                   priority: PRIORITY.PERSON, sub: "" });
      }
    }
    return out;
  }

  const RE_NAME_LIST = /[\u4e00-\u9fa5]{2,3}(?:、[\u4e00-\u9fa5]{2,3}){1,}/g;

  /** 顿号并列的人名：「王伟、李娜、赵强」 */
  /** 顿号并列的人名：「王伟、李娜、赵强」 */
  function findEnumeratedNames(text, keep) {
    const out = [];
    RE_NAME_LIST.lastIndex = 0;
    let m;
    while ((m = RE_NAME_LIST.exec(text))) {
      let pos = m.index;
      for (const raw of m[0].split("、")) {
        let picked = null;
        for (const L of [raw.length, 2]) {
          if (L < 2 || L > raw.length) continue;
          const t = raw.slice(0, L);
          if (keep && keep.has(t)) break;
          if (is_valid_name(t) && plausible_name(t)) { picked = t; break; }
        }
        if (picked) {
          out.push({ start: pos, end: pos + picked.length, kind: T.PERSON, text: picked,
                     rule: "name-list", priority: PRIORITY.PERSON, sub: "" });
        }
        pos += raw.length + 1;
      }
    }
    return out;
  }

  const RE_STANDALONE_ADDR = new RegExp(
    "^(?:[\\u4e00-\\u9fa5]{2,10}(?:省|自治区|特别行政区)?)[\\u4e00-\\u9fa5\\d]{2,10}" +
    "(?:市|自治州|地区)[\\u4e00-\\u9fa5\\d]{2,12}(?:区|县|市)?[\\u4e00-\\u9fa5\\d]{0,20}" +
    "(?:路|街|巷|道|村|镇|号)[\\u4e00-\\u9fa5\\dA-Za-z\\-—–－]{0,20}$");

  /** 整行就是地址（表格摊平后的典型形态） */
  function findStandalone(text) {
    const out = [];
    let pos = 0;
    for (const line of text.split("\n")) {
      const lineStart = pos;
      pos += line.length + 1;
      const tok = line.trim();
      if (!tok || tok.length < 6) continue;
      if (tok.length <= 60 && RE_STANDALONE_ADDR.test(tok)) {
        const hits = ["省", "市", "区", "县", "路", "街", "号", "镇", "村"].filter((k) => tok.includes(k)).length;
        if (hits >= 2) {
          const offset = lineStart + line.indexOf(tok);
          out.push({ start: offset, end: offset + tok.length, kind: T.ADDRESS,
                     text: tok, rule: "standalone-addr", priority: PRIORITY.ADDRESS, sub: "" });
        }
      }
    }
    return out;
  }

  function findOrgShorts(text, orgSpans) {
    const cores = new Set();
    for (const sp of orgSpans) { const c = orgCore(sp.text); if (c) cores.add(c); }
    if (!cores.size) return [];
    const out = [];
    for (const core of cores) {
      for (const suf of ORG_SHORT_SUFFIX) {
        const token = core + suf;
        let from = 0;
        while (true) {
          const i = text.indexOf(token, from);
          if (i < 0) break;
          from = i + 1;
          const end = i + token.length;
          if (orgSpans.some((t) => i < t.end && t.start < end)) continue;
          out.push({ start: i, end, kind: T.ORG, text: token, rule: "org-short",
                     priority: PRIORITY.ORG, sub: suf });
        }
      }
    }
    return out;
  }

  function findOrgs(text) {
    const spans = [];
    const seen = new Set();
    RE_ORG_SUFFIX.lastIndex = 0;
    let m;
    while ((m = RE_ORG_SUFFIX.exec(text))) {
      const suffix = m[0];
      const start = m.index;
      const end = start + suffix.length;
      let j = start;
      const allowed = "（）()《》“”\"'";
      while (j > 0 && start - (j - 1) <= 40) {
        const ch = text[j - 1];
        if (S.BOUNDARY_CHARS.has(ch) && !allowed.includes(ch)) break;
        j -= 1;
      }
      const cand = trimOrgCandidate(text.slice(j, start));
      if (!orgOk(cand, suffix, text, end)) continue;
      const cstart = start - cand.length;
      if (cstart < 0) continue;
      const key = cstart + ":" + end;
      if (seen.has(key)) continue;
      seen.add(key);
      let sub = suffix;
      for (const s of ["股份有限公司", "有限责任公司", "有限公司", "公司"]) {
        if (suffix.endsWith(s)) { sub = "公司"; break; }
      }
      spans.push({ start: cstart, end, kind: T.ORG, text: text.slice(cstart, end),
                   rule: "org-suffix", priority: PRIORITY.ORG, sub });
    }
    return spans;
  }

  function takeNameAfter(text, pos, strict) {
    let i = pos;
    while (i < text.length && " 　\t：:".includes(text[i])) i += 1;
    const start = i;
    let n = 0;
    while (i < text.length && n < 6 && !S.BOUNDARY_CHARS.has(text[i])) { i += 1; n += 1; }
    const seg = text.slice(start, i);
    if (!seg) return null;
    for (const s of R.ORG_SUFFIXES) if (seg.includes(s)) return null;

    const okNext = (L) => {
      const nxt = start + L < text.length ? text[start + L] : "";
      return nxt === "" || S.NAME_TERMINATORS.has(nxt);
    };

    if (R.COMPOUND_SURNAMES.includes(seg.slice(0, 2))) {
      for (const L of [4, 3]) {
        if (seg.length >= L && is_valid_name(seg.slice(0, L)) &&
            (okNext(L) || plausible_name(seg.slice(0, L)))) {
          return { start, end: start + L, name: seg.slice(0, L) };
        }
      }
    }
    if (seg.length >= 3 && !S.NAME_STOPWORDS.has(seg.slice(0, 2)) &&
        is_valid_name(seg.slice(0, 3)) && !S.VERB_CHARS.has(seg[2]) && okNext(3)) {
      return { start, end: start + 3, name: seg.slice(0, 3) };
    }
    if (seg.length >= 2 && is_valid_name(seg.slice(0, 2))) {
      if (!strict || okNext(2) || plausible_name(seg.slice(0, 2))) {
        return { start, end: start + 2, name: seg.slice(0, 2) };
      }
    }
    return null;
  }

  function findKeepNames(text) {
    const keep = new Set();
    RE_KEEP_ANCHOR.lastIndex = 0;
    let m;
    while ((m = RE_KEEP_ANCHOR.exec(text))) {
      const got = takeNameAfter(text, m.index + m[0].length, false);
      if (got) keep.add(got.name);
    }
    return keep;
  }

  const RE_PARTIAL = /([\u4e00-\u9fa5])某/g;
  const STRONG_NEXT = new Set(("、，。；：！？诉称辩说未已向于到经提主认为系等和与及协办按被将对给由在").split(""));

  /** 「姓+某(+名)」半打码的名字，仍然泄露名里的字，统一成「姓某某」 */
  function findPartialNames(text) {
    const out = [];
    RE_PARTIAL.lastIndex = 0;
    let m;
    while ((m = RE_PARTIAL.exec(text))) {
      const surname = m[1];
      if (!S.SINGLE_SURNAMES.has(surname)) continue;
      let i = m.index + 2;
      let tail = "";
      for (const L of [2, 1]) {
        const cand = text.slice(i, i + L);
        if (cand.length === L && [...cand].every((ch) => S.GIVEN_NAME_CHARS.has(ch))) {
          tail = cand;
          break;
        }
      }
      if (!tail && text[i] === "某") continue;   // 张某某 本身完整
      const token = m[0] + tail;
      if (R.ORG_SUFFIXES.some((suf) => token.endsWith(suf))) continue;
      const end = i + tail.length;
      const nxt = text[end] || "";
      let ok = STRONG_NEXT.has(nxt);
      if (!ok) {
        const left = text.slice(Math.max(0, m.index - 12), m.index);
        ok = R.REDACT_ANCHORS.some((a) => left.endsWith(a));
      }
      if (!ok) continue;
      out.push({ start: m.index, end, kind: T.PERSON, text: token,
                 rule: "partial-mask", priority: PRIORITY.PERSON, sub: "" });
    }
    return out;
  }

  function findPersons(text, keep) {
    const hits = new Map();
    const add = (s, e, tok, rule) => {
      if (keep.has(tok)) return;
      if (!is_valid_name(tok)) return;
      hits.set(s + ":" + e, { start: s, end: e, kind: T.PERSON, text: tok,
                              rule, priority: PRIORITY.PERSON, sub: "" });
    };

    RE_PERSON_ANCHOR.lastIndex = 0;
    let m;
    while ((m = RE_PERSON_ANCHOR.exec(text))) {
      const got = takeNameAfter(text, m.index + m[0].length, false);
      if (got) add(got.start, got.end, got.name, "anchor");
    }
    RE_BIO.lastIndex = 0;
    while ((m = RE_BIO.exec(text))) {
      add(m.index, m.index + m[1].length, m[1], "bio");
    }
    RE_NAME_PREDICATE.lastIndex = 0;
    while ((m = RE_NAME_PREDICATE.exec(text))) {
      if (is_valid_name(m[1]) && plausible_name(m[1])) {
        add(m.index, m.index + m[1].length, m[1], "predicate");
      }
    }
    const vs = [];
    RE_VS.lastIndex = 0;
    while ((m = RE_VS.exec(text))) {
      const bStart = m.index + m[1].length + (m[0].length - m[1].length - m[2].length);
      const picked = [];
      for (const [tok, base] of [[m[1], m.index], [m[2], bStart]]) {
        for (const L of [3, 2]) {
          const t = tok.slice(0, L);
          if (is_valid_name(t) && plausible_name(t)) {
            picked.push({ s: base, e: base + L, t });
            break;
          }
        }
      }
      if (picked.length === 2) vs.push(...picked);
    }
    for (const p of vs) add(p.s, p.e, p.t, "versus");
    return [...hits.values()];
  }

  function findAddresses(text) {
    const spans = [];
    RE_ADDR_ANCHOR.lastIndex = 0;
    let m;
    while ((m = RE_ADDR_ANCHOR.exec(text))) {
      const val = m[1];
      if (val.trim().length < 4) continue;
      const start = m.index + m[0].length - val.length;
      const end = start + val.replace(/\s+$/, "").length;
      spans.push({ start, end, kind: T.ADDRESS, text: text.slice(start, end),
                   rule: "addr-anchor", priority: PRIORITY.ADDRESS, sub: "" });
    }
    RE_ADDR_PLAIN.lastIndex = 0;
    while ((m = RE_ADDR_PLAIN.exec(text))) {
      const val = m[1];
      if (!/[省市县区]/.test(val)) continue;
      const start = m.index + m[0].length - val.length;
      const end = start + val.replace(/\s+$/, "").length;
      if (spans.some((a) => start < a.end && a.start < end)) continue;
      spans.push({ start, end, kind: T.ADDRESS, text: text.slice(start, end),
                   rule: "addr-plain", priority: PRIORITY.ADDRESS, sub: "" });
    }
    return spans;
  }

  function findPII(text) {
    const spans = [];
    const grab = (re, kind, rule, prio) => {
      re.lastIndex = 0;
      let m;
      while ((m = re.exec(text))) {
        spans.push({ start: m.index, end: m.index + m[0].length, kind, text: m[0],
                     rule, priority: prio, sub: "" });
      }
    };
    grab(RE_ID, "id", "id-18", PRIORITY.id);
    grab(RE_PHONE, "phone", "mobile", PRIORITY.phone);
    grab(RE_LANDLINE, "phone", "landline", PRIORITY.phone);
    grab(RE_EMAIL, "email", "email", PRIORITY.email);
    return spans;
  }

  function resolve(spans) {
    const ordered = spans.slice().sort((a, b) =>
      (b.priority - a.priority) || ((b.end - b.start) - (a.end - a.start)));
    const kept = [];
    for (const sp of ordered) {
      if (kept.some((k) => sp.start < k.end && k.start < sp.end)) continue;
      kept.push(sp);
    }
    return kept.sort((a, b) => a.start - b.start);
  }

  function detect(text, keepNames) {
    const keep = keepNames !== undefined ? keepNames : findKeepNames(text);
    const spans = [];
    spans.push(...X.findStructured(text));
    spans.push(...findAddresses(text));
    const orgSpans = findOrgs(text);
    spans.push(...orgSpans);
    spans.push(...findParenOrgs(text, orgSpans));
    spans.push(...findOrgShorts(text, orgSpans.concat(spans.filter((x) => x.kind === T.ORG))));
    spans.push(...findTableNames(text));
    spans.push(...findEnumeratedNames(text, keep));
    spans.push(...findStandalone(text));
    spans.push(...findPersons(text, keep));
    spans.push(...findPartialNames(text));
    const resolved = resolve(spans);
    // 白名单最高优先级：法条 / 法院 / 案号绝不遮挡
    const preserved = T.findPreserve(text);
    if (!preserved.length) return resolved;
    return resolved.filter((sp) =>
      !preserved.some(([ps, pe]) => sp.start < pe && ps < sp.end));
  }

  root.LR_DETECT = {
    PRIORITY, is_valid_name, plausible_name, findOrgs, findKeepNames,
    findPersons, findAddresses, findPII, resolve, detect,
  };
})(typeof window !== "undefined" ? window : globalThis);

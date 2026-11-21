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
    if (R.COMMON_WORDS && R.COMMON_WORDS.indexOf(tok) >= 0) return false;
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

  const RE_PAREN_CITY = /[\u4e00-\u9fa5]{2,8}[（(][\u4e00-\u9fa5]{2,4}[）)]\s*[\u4e00-\u9fa5]{2,10}/g;
  const ORG_TAIL_STOP = new Set("为所的进行由与和及在是，。、；：！？\n".split(""));
  const ORG_LEAD = new Set("由与和及在是对向给被让使据经项其该".split(""));
  const ORG_TAIL_VERB = new Set("迟未已将正均都也仍却则并且后前中内上下还又再才就".split(""));

  const ORG_TAIL_WORDS = (R.ORG_INDUSTRY || []).concat(R.ORG_SUFFIXES || [])
    .sort((a, b) => b.length - a.length);
  const RE_ORG_TAIL = new RegExp("(?:" + ORG_TAIL_WORDS.join("|") + ")+$");

  /** 单位名末尾的类型词：北京鼎盛科技有限公司 → 科技有限公司 */
  function orgTail(name) {
    const m = RE_ORG_TAIL.exec(name || "");
    if (!m) return "";
    const tail = m[0];
    if (tail === name || tail.length < 2) return "";
    return tail;
  }

  /** 「XX（北京）YY」这种不以「公司」结尾的单位名，括号+地名是很强的信号 */
  const BRACKET_MAP = { "〈": "（", "〉": "）", "【": "（", "】": "）", "〔": "（", "〕": "）", "＜": "（", "＞": "）" };
  function normalizeBrackets(s) {
    return s.replace(/[〈〉【】〔〕＜＞]/g, (c) => BRACKET_MAP[c] || c);
  }

  function findParenOrgs(text, taken) {
    const out = [];
    const norm = normalizeBrackets(text);   // 长度不变，位置可直接用
    RE_PAREN_CITY.lastIndex = 0;
    let m;
    while ((m = RE_PAREN_CITY.exec(norm))) {
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
      for (const w of ["参见", "载", "见", "包括", "以及"]) {
        if (tok.startsWith(w) && tok.length - w.length >= 6) { tok = tok.slice(w.length); nLead += w.length; break; }
      }
      while (tok.length && ORG_LEAD.has(tok[0])) { tok = tok.slice(1); nLead++; }
      if (tok[0] === "(" || tok[0] === "（") continue;   // 「(加纳)科菲」这种是人名不是单位
      const cp2 = Math.max(tok.indexOf("）"), tok.indexOf(")"));
      if (cp2 < 0) continue;
      const tail = tok.length - cp2 - 1;
      if (tail < 2 || tail > 10) continue;
      if (T.PRESERVE.some(([, rx]) => rx.test(tok))) continue;
      const start = m.index + nLead;
      const end = start + tok.length;
      // 只在「已被完全覆盖」时跳过；更长的括号名应该赢
      if (taken && taken.some((t) => t.start <= start && end <= t.end)) continue;
      out.push({ start, end, kind: T.ORG, text: text.slice(start, end),
                 rule: "paren-city", priority: PRIORITY.ORG, sub: orgTail(tok) || "公司" });
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
  const TABLE_HEADER_NAME = /(姓名|名字|人名|当事人|原告|被告|申请人|被申请人|负责人|法定代表人|联系人|经办人|代理人|辩护人|股东|员工|职工|收款人|付款人|签署人|签字人|审核人|权利人|义务人|户主|承租人|出借人)/;

  /** 表格里的姓名：必须有姓名类表头才认，避免把「高水平」「国国」当人名 */
  function findTableNames(text) {
    const out = [];
    const lines = text.split("\n");

    let headerCols = null;
    for (const line of lines) {
      if (!/[\t|]/.test(line)) continue;
      const cells = line.split(/[\t|]/).map((c) => c.trim());
      const cols = {};
      cells.forEach((c, i) => { if (TABLE_HEADER_NAME.test(c)) cols[i] = c; });
      if (Object.keys(cols).length) { headerCols = cols; break; }
    }

    let pos = 0;
    for (const line of lines) {
      const lineStart = pos;
      pos += line.length + 1;
      if (!/[\t|]/.test(line)) continue;
      const cells = line.split(/[\t|]/).map((c) => c.trim());

      const mk = (tok) => {
        if (tok.length < 2 || tok.length > 3) return;
        if (S.NAME_STOPWORDS.has(tok)) return;
        if (R.COMMON_WORDS && R.COMMON_WORDS.indexOf(tok) >= 0) return;
        if (!is_valid_name(tok) || !plausible_name(tok)) return;
        const idx = line.indexOf(tok);
        if (idx < 0) return;
        out.push({ start: lineStart + idx, end: lineStart + idx + tok.length, kind: T.PERSON,
                   text: tok, rule: "table-cell", priority: PRIORITY.PERSON, sub: "" });
      };

      // 纵向：姓名 \t 张伟
      for (let i = 0; i < cells.length - 1; i++) {
        if (TABLE_HEADER_NAME.test(cells[i])) mk(cells[i + 1]);
      }
      // 横向：按表头列取数据行
      if (headerCols) {
        for (const k of Object.keys(headerCols)) {
          const i = Number(k);
          if (i < cells.length && !TABLE_HEADER_NAME.test(cells[i])) mk(cells[i]);
        }
      }
    }
    // 去重
    const seen = new Set();
    return out.filter((sp) => {
      const key = sp.start + ":" + sp.end;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }

  const RE_NAME_LIST = /[\u4e00-\u9fa5]{2,3}(?:、[\u4e00-\u9fa5]{2,3}){1,}/g;

  /** 顿号并列的人名：「王伟、李娜、赵强」 */
  /** 顿号并列的人名：「王伟、李娜、赵强」 */
  function findEnumeratedNames(text, keep) {
    const out = [];
    const COMMON = new Set(R.COMMON_SURNAMES || []);
    const isCommonSurname = (ch) => COMMON.has(ch);
    RE_NAME_LIST.lastIndex = 0;
    let m;
    while ((m = RE_NAME_LIST.exec(text))) {
      const parts = m[0].split("、");
      if (!parts.length) continue;
      let head = parts[0];
      let headCut = 0;
      if (!(is_valid_name(head) && plausible_name(head))) {
        for (const cut of [1, 2]) {
          const t2 = head.slice(cut);
          if (t2.length >= 2 && isCommonSurname(t2[0]) && is_valid_name(t2) && plausible_name(t2)) {
            head = t2; headCut = cut; break;
          }
        }
      }
      if (!isCommonSurname(head[0])) continue;
      const before = text.slice(Math.max(0, m.index - 6), m.index);
      const isCitation = ["参见", "载", "见", "如", "据", "阅"].some((w) => before.endsWith(w));
      if (isCitation) {
        if (!is_valid_name(head)) continue;
      } else if (!(is_valid_name(head) && plausible_name(head))) {
        continue;
      }
      let pos = m.index + headCut;
      for (let ri = 0; ri < parts.length; ri++) {
        const raw = ri === 0 ? head : parts[ri];
        let picked = null;
        if (isCitation && raw.length >= 2 && raw.length <= 3 && isCommonSurname(raw[0]) && is_valid_name(raw)) {
          picked = raw;
        } else if (raw.length >= 2 && raw.length <= 3 && isCommonSurname(raw[0]) &&
                   is_valid_name(raw) && plausible_name(raw)) {
          picked = raw;
        } else if (raw.length === 3 && !S.GIVEN_NAME_CHARS.has(raw[2]) && isCommonSurname(raw[0]) &&
                   is_valid_name(raw.slice(0, 2)) && plausible_name(raw.slice(0, 2))) {
          picked = raw.slice(0, 2);
        }
        if (picked && !(keep && keep.has(picked))) {
          out.push({ start: pos, end: pos + picked.length, kind: T.PERSON, text: picked,
                     rule: "name-list", priority: PRIORITY.PERSON, sub: "" });
        }
        pos += raw.length + 1;
      }
    }
    return out;
  }

  const RE_CITE_AUTHOR = /([\u4e00-\u9fa5]{2,4})[：:]\s*[“"《]/g;
  const COMMON_SURNAME_SET = new Set(R.COMMON_SURNAMES || []);

  /** 引注作者：「黄进：“…”」「马忠法:《…》」 */
  function findCiteAuthors(text, keep) {
    const out = [];
    RE_CITE_AUTHOR.lastIndex = 0;
    let m;
    while ((m = RE_CITE_AUTHOR.exec(text))) {
      let tok = m[1];
      let start = m.index;
      if ((keep && keep.has(tok)) || (R.COMMON_WORDS || []).indexOf(tok) >= 0) continue;
      for (const suf of ["主编", "编著", "编", "著"]) {
        if (tok.endsWith(suf) && tok.length - suf.length >= 2) { tok = tok.slice(0, -suf.length); break; }
      }
      if (!is_valid_name(tok)) {
        let fixed = null;
        for (const L of [4, 3, 2]) {
          if (L < tok.length) {
            const tail = tok.slice(-L);
            if (COMMON_SURNAME_SET.has(tail[0]) && is_valid_name(tail)) { fixed = [tail, start + tok.length - L]; break; }
          }
        }
        if (!fixed) continue;
        tok = fixed[0]; start = fixed[1];
      }
      if (!COMMON_SURNAME_SET.has(tok[0])) continue;
      out.push({ start, end: start + tok.length, kind: T.PERSON, text: tok,
                 rule: "cite-author", priority: PRIORITY.PERSON, sub: "" });
    }
    return out;
  }

  const RE_BYLINE = /([\u4e00-\u9fa5])\s+([\u4e00-\u9fa5]{1,2})\s*\*{1,2}|([\u4e00-\u9fa5]{2,4})\*{1,2}/g;

  /** 署名行：「李 鸣*」「黄蓬北**」 */
  function findBylineNames(text, keep) {
    const out = [];
    RE_BYLINE.lastIndex = 0;
    let m;
    while ((m = RE_BYLINE.exec(text))) {
      const tok = m[1] ? (m[1] + m[2]) : m[3];
      const start = m[1] ? m.index : m.index + m[0].indexOf(m[3]);
      if ((keep && keep.has(tok)) || (R.COMMON_WORDS || []).indexOf(tok) >= 0) continue;
      if (!COMMON_SURNAME_SET.has(tok[0]) || !is_valid_name(tok)) continue;
      out.push({ start, end: start + tok.length, kind: T.PERSON, text: tok,
                 rule: "byline", priority: PRIORITY.PERSON, sub: "" });
    }
    return out;
  }

  const RE_STANDALONE_ADDR = new RegExp(
    "^(?:[\\u4e00-\\u9fa5]{2,10}(?:省|自治区|特别行政区)?)[\\u4e00-\\u9fa5\\d]{2,10}" +
    "(?:市|自治州|地区)[\\u4e00-\\u9fa5\\d]{2,12}(?:区|县|市)?[\\u4e00-\\u9fa5\\d]{0,20}" +
    "(?:路|街|巷|道|村|镇|号)[\\u4e00-\\u9fa5\\dA-Za-z\\-—–－]{0,20}$");

  /** 没锚点、整行就是地址（表格摊平后最常见） */
  function findStandalone(text) {
    const out = [];
    let pos = 0;
    for (const line of text.split("\n")) {
      const lineStart = pos;
      pos += line.length + 1;
      let tok = line.trim();
      if (!tok || tok.length < 6) continue;
      const stripped = tok.replace(/^[地住]\s*址\s*[：:]\s*/, "");
      let offset;
      if (stripped !== tok) { tok = stripped; offset = lineStart + line.indexOf(tok); }
      else { offset = lineStart + line.indexOf(tok); }
      if (tok.length >= 8 && tok.length <= 60 && RE_STANDALONE_ADDR.test(tok)) {
        const hits = ["省", "市", "区", "县", "路", "街", "号", "镇", "村"].filter((k) => tok.indexOf(k) >= 0).length;
        if (hits >= 2) {
          out.push({ start: offset, end: offset + tok.length, kind: T.ADDRESS, text: tok,
                     rule: "standalone-addr", priority: PRIORITY.ADDRESS, sub: "" });
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

  const ORG_LEAD_WORDS = ["参见", "载", "包括", "以及", "此外", "其中", "比如", "例如", "如",
    "据", "见", "由", "在", "对", "于", "同", "及", "和", "与", "从", "是", "为", "被", "将",
    "已", "现", "也", "均", "都"];
  const ORG_FRAGMENTS = new Set(["研究中心", "法治学院", "国语大学", "研究院", "研究所"]);
  const ORG_VERB_PHRASES = ["取消", "实现", "推进", "加强", "包括", "进入", "建立", "完善",
    "形成", "成为", "作为", "进行", "开展", "具备", "拥有", "达到", "促进", "推动", "支持",
    "参与", "涉及", "属于", "存在", "具有"];

  function trimOrgLead(cand) {
    let changed = true;
    while (changed) {
      changed = false;
      for (const w of ORG_LEAD_WORDS) {
        if (cand.startsWith(w) && cand.length - w.length >= 4) { cand = cand.slice(w.length); changed = true; break; }
      }
    }
    return cand;
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
      const cand = trimOrgLead(trimOrgCandidate(text.slice(j, start)));
      if (!orgOk(cand, suffix, text, end)) continue;
      const cstart = start - cand.length;
      if (cstart < 0) continue;
      const key = cstart + ":" + end;
      if (seen.has(key)) continue;
      seen.add(key);
      const final = text.slice(cstart, end);
      if (ORG_FRAGMENTS.has(final)) continue;
      if (ORG_VERB_PHRASES.some((ph) => final.indexOf(ph) >= 0)) continue;
      if (final.length <= 3 && !/(公司|集团|大学|学院|医院)$/.test(final)) continue;
      // 替身保留类型尾巴：北京鼎盛科技有限公司 → 某某某科技有限公司
      const sub = orgTail(final) || suffix;
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
    let orgSpans = findOrgs(text);
    spans.push(...orgSpans);
    const parenSpans = findParenOrgs(text, orgSpans);
    // 括号名比后缀规则认得更长时，把被它整个包住的短 span 去掉，避免同一家脱两次
    if (parenSpans.length) {
      orgSpans = orgSpans.filter((o) => !parenSpans.some((p2) => p2.start <= o.start && o.end <= p2.end));
      const keepIds = new Set(orgSpans);
      const idx = spans.findIndex((x) => x === orgSpans[0]);
      for (let i = spans.length - 1; i >= 0; i -= 1) {
        const o = spans[i];
        if (o.kind === T.ORG && parenSpans.some((p2) => p2.start <= o.start && o.end <= p2.end)) {
          spans.splice(i, 1);
        }
      }
    }
    spans.push(...parenSpans);
    spans.push(...findOrgShorts(text, orgSpans.concat(spans.filter((x) => x.kind === T.ORG))));
    spans.push(...findTableNames(text));
    spans.push(...findEnumeratedNames(text, keep));
    spans.push(...findCiteAuthors(text, keep));
    spans.push(...findBylineNames(text, keep));
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

/* xlsx 文本抽取与脱敏回写：xlsx 就是 zip + XML，只动文字单元格，
   样式 / 列宽 / 合并 / 公式 / 数字格式都不碰（也就不可能被弄坏）。 */
(function (root) {
  const ZIP = root.LR_ZIP;
  const W = root.LR_XLSXWRITE;
  const D = root.LR_DETECT;
  const R = root.LR_RULES;
  const T = root.LR_TYPES;
  const dec = new TextDecoder("utf-8");
  const enc = new TextEncoder();

  const SHEET_RE = /<sheet\b[^>]*?(?:\/>|>)/g;
  const REL_RE = /<Relationship\b[^>]*?(?:\/>|>)/g;
  const SI_RE = /<si\b[^>]*?(?:\/>|>([\s\S]*?)<\/si>)/g;
  const CELL_RE = /<c\b([^>]*?)(?:\/>|>([\s\S]*?)<\/c>)/g;
  const V_RE = /<v\b[^>]*>([\s\S]*?)<\/v>/;
  const IS_RE = /<is\b([^>]*)>([\s\S]*?)<\/is>/;

  function normPath(p) {
    const parts = [];
    for (const seg of String(p).replace(/\\/g, "/").split("/")) {
      if (!seg || seg === ".") continue;
      if (seg === "..") { parts.pop(); continue; }
      parts.push(seg);
    }
    return parts.join("/");
  }

  /** zip 里的 Target 都是相对 xl/ 目录的 */
  function resolveTarget(target, baseDir) {
    const t = String(target || "").replace(/\\/g, "/");
    return normPath(t.charAt(0) === "/" ? t : baseDir + "/" + t);
  }

  function refToRC(ref) {
    const m = /^([A-Z]+)(\d+)$/.exec(String(ref || "").toUpperCase());
    if (!m) return { row: 0, col: 0 };
    let col = 0;
    for (const ch of m[1]) col = col * 26 + (ch.charCodeAt(0) - 64);
    return { row: parseInt(m[2], 10), col };
  }

  // ---------- 单元格级启发（只在 xlsx 通道用，不动全局检测器） ----------
  // 表格里「一格就是一个值」没有上下文（没有「原告张三」「住北京市…」这种锚点），
  // 检测器会漏。这里对「整格只有一个值」补一层保守判断，只在本通道生效。
  const ADDR_MARKS = new Set("省市区县镇路街巷号栋室楼".split(""));
  const NAME_EXTRA_CHARS = new Set("涵桐梦玥丰".split(""));   // 词库缺的常用名用字
  const HEADER_WORDS = new Set([
    "姓名", "名字", "名称", "姓名/名称", "身份证", "身份证号", "身份证号码", "证件号",
    "证件号码", "电话", "手机", "手机号", "手机号码", "联系电话", "联系方式", "座机",
    "住址", "地址", "现住址", "户籍地址", "通讯地址", "单位", "工作单位", "单位名称",
    "公司", "公司名称", "部门", "序号", "编号", "案号", "备注", "性别", "民族",
    "出生日期", "出生年月", "年龄", "籍贯", "职务", "职业", "当事人", "合计", "总计",
  ]);

  const isHan = (ch) => ch >= "\u4e00" && ch <= "\u9fff";

  function nameCharsOk(tok) {
    if (D.plausible_name(tok)) return true;
    const rest = R.set.COMPOUND_SURNAMES.has(tok.slice(0, 2)) ? tok.slice(2) : tok.slice(1);
    if (!rest) return false;
    for (const ch of rest) {
      if (!R.set.GIVEN_NAME_CHARS.has(ch) && !NAME_EXTRA_CHARS.has(ch)) return false;
    }
    return true;
  }

  /** 整格是法院/案号/法条这类必须保留的东西 */
  function preservedWhole(tok) {
    if (T.findPreserve(tok).length) return true;
    return R.ORG_KEEP_PATTERNS.some((p) => tok.includes(p));
  }

  /** 整格文本猜类型：返回 {kind, body}；猜不出返回 null */
  function cellGuess(text) {
    const body = String(text || "").trim();
    if (!body || HEADER_WORDS.has(body)) return null;

    // 1）整格人名：2~4 个汉字、首字是姓、其余是常用名用字
    if (body.length >= 2 && body.length <= 4 && [...body].every(isHan)
        && D.is_valid_name(body) && nameCharsOk(body)) {
      return { kind: T.PERSON, body };
    }
    // 2）整格地址：含 省市县区镇路街巷号栋室楼、长度 ≥ 6、汉字占一半以上
    if (body.length >= 6 && [...body].some((ch) => ADDR_MARKS.has(ch))
        && [...body].filter(isHan).length / body.length >= 0.5
        && !preservedWhole(body)) {
      return { kind: T.ADDRESS, body };
    }
    return null;
  }

  /** 孤儿共享串（没有任何格子引用）里也可能留着原文，清一遍：先用已认定的替身，
      再让引擎认独立 PII，最后按整格启发兜一次。返回 Map<序号, 新文字>。 */
  function scrubOrphans(redactor, shared, usedShared) {
    const updates = new Map();
    const known = [...redactor.renderer.byKey.values()]
      .sort((a, b) => b.original.length - a.original.length);
    shared.forEach((text, idx) => {
      if (usedShared.has(idx) || !text || !text.trim()) return;
      let now = text;
      for (const rec of known) {
        if (now.includes(rec.original)) now = now.split(rec.original).join(rec.replacement);
      }
      const spans = D.detect(now).sort((a, b) => b.start - a.start);
      for (const sp of spans) {
        if (redactor.disabled.has(sp.kind)) continue;
        const rec = redactor.renderer.assign(sp.kind, sp.text, now, sp.start, sp.sub);
        now = now.slice(0, sp.start) + rec.replacement + now.slice(sp.end);
      }
      const guess = cellGuess(now);
      if (guess && !redactor.disabled.has(guess.kind)) {
        const start = now.length - now.trimStart().length;
        const rec = redactor.renderer.assign(guess.kind, guess.body, now, start);
        now = now.slice(0, start) + rec.replacement + now.slice(start + guess.body.length);
      }
      if (now !== text) updates.set(idx, now);
    });
    return updates;
  }

  /** 先走引擎 prepare，再给引擎没认出来的「整格值」补一刀（补的实体同样走 renderer） */
  function redactCells(redactor, texts) {
    const results = redactor.prepare(texts);
    texts.forEach((text, i) => {
      const br = results[i];
      if (!br || br.spans.length) return;            // 引擎已经认出来了
      const guess = cellGuess(text);
      if (!guess || redactor.disabled.has(guess.kind)) return;
      const start = text.length - text.trimStart().length;
      const rec = redactor.renderer.assign(guess.kind, guess.body, text, start);
      br.spans = [[start, start + guess.body.length, rec.replacement, guess.kind]];
      br.redacted = text.slice(0, start) + rec.replacement
                    + text.slice(start + guess.body.length);
      br.posSpans = [[start, start + rec.replacement.length, guess.kind, guess.body]];
      redactor.rows.push({ block: i + 1, type: guess.kind, entity_id: rec.entity_id,
                           original: guess.body, replacement: rec.replacement,
                           rule: "cell-guess", start });
      redactor.blockSpans.push({ index: i, spans: br.posSpans });
    });
    return results;
  }

  function parseRels(xml) {
    const map = new Map();
    let m;
    REL_RE.lastIndex = 0;
    while ((m = REL_RE.exec(xml))) {
      const id = W.attr(m[0], "Id");
      if (id) map.set(id, { type: W.attr(m[0], "Type"), target: W.attr(m[0], "Target") });
    }
    return map;
  }

  /** sharedStrings.xml → ["字符串", ...]（顺序就是 t="s" 引用的序号，必须有空位）
      SI_RE 和 xlsxwrite.js 里的那个必须一模一样，否则改的时候会错位。 */
  function parseShared(xml) {
    const list = [];
    let m;
    SI_RE.lastIndex = 0;
    while ((m = SI_RE.exec(xml))) list.push(W.containerText(m[1]));
    const total = (xml.match(/<si\b/g) || []).length;
    if (list.length !== total) {
      throw new Error(`sharedStrings.xml 解析不对（${list.length}/${total} 条），先别用这个文件脱敏，发我看看`);
    }
    return list;
  }

  function parseSheets(wbXml, rels, files) {
    const out = [];
    let m;
    SHEET_RE.lastIndex = 0;
    while ((m = SHEET_RE.exec(wbXml))) {
      const tag = m[0];
      let rid = W.attr(tag, "r:id");
      if (!rid) {                                     // 万一关系前缀不叫 r
        const alt = tag.match(/\s[\w.-]+:id\s*=\s*"([^"]*)"/);
        rid = alt ? W.unescapeXml(alt[1]) : "";
      }
      const rel = rid ? rels.get(rid) : null;
      if (!rel || (rel.type && !/\/worksheet$/.test(rel.type))) continue;   // 图表页之类跳过
      const path = resolveTarget(rel.target, "xl");
      if (!files.has(path)) continue;
      out.push({ name: W.attr(tag, "name"), path, xml: dec.decode(files.get(path)) });
    }
    return out;
  }

  /** 扫一个 sheet 的文字单元格：{ref, text, kind: shared|inline, index, row, col}
      数字 / 日期 / 布尔 / 公式结果一律不返回，也就永远不会被改。 */
  function collectCells(xml, shared) {
    const out = [];
    let m;
    CELL_RE.lastIndex = 0;
    while ((m = CELL_RE.exec(xml))) {
      const attrs = m[1] || "", inner = m[2];
      if (!inner) continue;
      const ref = W.attr(attrs, "r");
      if (!ref) continue;
      const type = W.attr(attrs, "t");
      let text = null, kind = "", index = -1;
      if (type === "s") {
        const v = inner.match(V_RE);
        if (!v) continue;
        index = parseInt(v[1], 10);
        text = shared[index];
        kind = "shared";
      } else if (type === "inlineStr") {
        const is = inner.match(IS_RE);
        if (!is) continue;
        text = W.containerText(is[2]);
        kind = "inline";
      }
      if (typeof text !== "string" || !text.trim()) continue;
      const rc = refToRC(ref);
      out.push({ ref, text, kind, index, row: rc.row, col: rc.col });
    }
    return out;
  }

  async function loadPackage(bytes) {
    const u8 = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
    const ab = u8.buffer.slice(u8.byteOffset, u8.byteOffset + u8.byteLength);
    const files = await ZIP.readZip(ab);

    const wbBytes = files.get("xl/workbook.xml");
    if (!wbBytes) throw new Error("这个文件里找不到 xl/workbook.xml，可能不是 xlsx（.xls 请先另存为 xlsx）");
    const wbXml = dec.decode(wbBytes);

    const relsBytes = files.get("xl/_rels/workbook.xml.rels");
    const rels = relsBytes ? parseRels(dec.decode(relsBytes)) : new Map();

    let sharedPath = "";
    for (const rel of rels.values()) {
      if (rel.type && /\/sharedStrings$/.test(rel.type)) { sharedPath = resolveTarget(rel.target, "xl"); break; }
    }
    if (!sharedPath || !files.has(sharedPath)) {
      sharedPath = files.has("xl/sharedStrings.xml") ? "xl/sharedStrings.xml" : "";
    }
    const shared = sharedPath ? parseShared(dec.decode(files.get(sharedPath))) : [];

    const sheets = parseSheets(wbXml, rels, files);
    if (!sheets.length) throw new Error("这个 xlsx 里找不到工作表，可能文件损坏");
    return { files, sheets, shared, sharedPath };
  }

  // ---------- 对外：读 ----------

  async function read(bytes) {
    const pkg = await loadPackage(bytes);
    return pkg.sheets.map((s) => ({
      name: s.name,
      cells: collectCells(s.xml, pkg.shared).map((e) => ({ ref: e.ref, text: e.text })),
    }));
  }

  // ---------- 对外：脱敏 ----------

  /** redactor 是 LR_ENGINE.Redactor 实例；返回 {bytes, shapes, cells} */
  async function redact(bytes, redactor) {
    if (!redactor || typeof redactor.prepare !== "function") {
      throw new Error("redact(bytes, redactor) 需要传一个 LR_ENGINE.Redactor 实例");
    }
    const pkg = await loadPackage(bytes);
    const files = pkg.files;
    const shapes = [];
    const cells = [];
    const sharedUpdates = new Map();
    const usedShared = new Set();

    for (const sheet of pkg.sheets) {
      const entries = collectCells(sheet.xml, pkg.shared);
      if (!entries.length) continue;
      for (const e of entries) if (e.kind === "shared") usedShared.add(e.index);

      // 一个 sheet 的文字一次性交给 prepare：同表内实体口径统一
      // （整格值再走 xlsx 通道自己的启发，见 redactCells）
      const results = redactCells(redactor, entries.map((e) => e.text));
      const inlineUpdates = new Map();

      entries.forEach((e, i) => {
        const br = results[i];
        if (!br || !br.spans.length) return;
        if (e.kind === "shared") sharedUpdates.set(e.index, br.redacted);
        else inlineUpdates.set(e.ref, br.redacted);

        cells.push({ sheet: sheet.name, ref: e.ref, row: e.row, col: e.col,
                     before: e.text, after: br.redacted });
        for (const sp of br.spans) {
          shapes.push({ sheet: sheet.name, ref: e.ref, row: e.row, col: e.col,
                        start: sp[0], end: sp[1],
                        original: e.text.slice(sp[0], sp[1]),
                        replacement: sp[2], kind: sp[3] });
        }
      });

      if (inlineUpdates.size) {
        files.set(sheet.path, enc.encode(W.rewriteInlineCells(sheet.xml, inlineUpdates)));
      }
    }

    // 没被任何单元格引用的共享串也可能留着敏感原文，顺手清掉（防漏）
    for (const [idx, text] of scrubOrphans(redactor, pkg.shared, usedShared)) {
      sharedUpdates.set(idx, text);
    }

    if (sharedUpdates.size && pkg.sharedPath && files.has(pkg.sharedPath)) {
      const xml = dec.decode(files.get(pkg.sharedPath));
      files.set(pkg.sharedPath, enc.encode(W.rewriteSharedStrings(xml, sharedUpdates)));
    }

    return { bytes: await W.pack(files), shapes, cells };
  }

  root.LR_XLSX = { read, redact, collectCells, parseShared, parseSheets, refToRC,
                   cellGuess, redactCells };
})(typeof window !== "undefined" ? window : globalThis);

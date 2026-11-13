/* docx 文本抽取与回写：保留 run 级格式，把 docx 当 zip + XML 处理 */
(function (root) {
  const ZIP = root.LR_ZIP;
  const ENGINE = root.LR_ENGINE;
  const dec = new TextDecoder("utf-8");
  const enc = new TextEncoder();

  const T_RE = /<w:t\b([^>]*?)(?:\/>|>([\s\S]*?)<\/w:t>)/g;
  // 批注 / 脚注 / 尾注 / 页眉页脚：正文之外的文字，python-docx 不处理，这里补上
  const EXTRA_PART_RE = /^word\/(?:comments|commentsExtended|footnotes|endnotes|header\d*|footer\d*)\.xml$/;
  const PARA_RE = /<w:p\b[^>]*>[\s\S]*?<\/w:p>/g;

  function unescapeXml(s) {
    return s.replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"')
            .replace(/&apos;/g, "'").replace(/&#(\d+);/g, (_, d) => String.fromCodePoint(+d))
            .replace(/&#x([0-9a-fA-F]+);/g, (_, h) => String.fromCodePoint(parseInt(h, 16)))
            .replace(/&amp;/g, "&");
  }
  function escapeXml(s) {
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  /** 扫出一个段落里的所有 <w:t>，以及拼起来的正文 */
  function scanParagraph(pxml) {
    const items = [];
    T_RE.lastIndex = 0;
    let m, pos = 0, text = "";
    while ((m = T_RE.exec(pxml))) {
      const content = unescapeXml(m[2] || "");
      items.push({ index: m.index, length: m[0].length, attrs: m[1] || "",
                   start: pos, end: pos + content.length, content });
      text += content;
      pos += content.length;
    }
    return { items, text };
  }

  function rewriteParagraph(pxml, spans) {
    if (!spans || !spans.length) return pxml;
    const { items } = scanParagraph(pxml);
    if (!items.length) return pxml;

    const chars = [];
    items.forEach((it, ti) => {
      for (const ch of it.content) chars.push({ ti, ch });
    });

    const out = [];
    let pos = 0;
    for (const [start, end, rep] of spans) {
      if (start > chars.length) break;
      for (let k = pos; k < start && k < chars.length; k++) out.push(chars[k]);
      out.push({ ti: start < chars.length ? chars[start].ti : 0, ch: rep });
      pos = Math.min(end, chars.length);
    }
    for (let k = pos; k < chars.length; k++) out.push(chars[k]);

    const buckets = items.map(() => "");
    for (const o of out) buckets[o.ti] += o.ch;

    let result = "";
    let last = 0;
    items.forEach((it, i) => {
      result += pxml.slice(last, it.index);
      const attrs = it.attrs.includes("xml:space") ? it.attrs : it.attrs + ' xml:space="preserve"';
      result += `<w:t${attrs}>${escapeXml(buckets[i])}</w:t>`;
      last = it.index + it.length;
    });
    result += pxml.slice(last);
    return result;
  }

  function collectParagraphs(xml) {
    const paras = [];
    PARA_RE.lastIndex = 0;
    let m;
    while ((m = PARA_RE.exec(xml))) {
      paras.push({ xml: m[0], index: m.index, ...scanParagraph(m[0]) });
    }
    return paras;
  }

  function rebuildXml(xml, results) {
    PARA_RE.lastIndex = 0;
    let out = "", last = 0, i = 0, m;
    while ((m = PARA_RE.exec(xml))) {
      out += xml.slice(last, m.index);
      const br = results[i];
      out += br ? rewriteParagraph(m[0], br.spans) : m[0];
      last = m.index + m[0].length;
      i += 1;
    }
    out += xml.slice(last);
    return out;
  }

  /** 取出一个附加部件里所有 <w:t> 的文字 */
  function extraTexts(xml) {
    const out = [];
    T_RE.lastIndex = 0;
    let m;
    while ((m = T_RE.exec(xml))) out.push(unescapeXml(m[2] || ""));
    return out;
  }

  /** 用已经算好的结果回写附加部件（按名字排序，和收集顺序一致） */
  function rebuildExtra(xml, redacted) {
    let idx = 0;
    return xml.replace(T_RE, (whole, attrs, content) => {
      const after = redacted[idx] !== undefined ? redacted[idx] : unescapeXml(content || "");
      idx += 1;
      return "<w:t" + attrs + ">" + escapeXml(after) + "</w:t>";
    });
  }

  async function processDocx(bytes, config, opts) {
    opts = opts || {};
    const u8 = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
    const ab = u8.buffer.slice(u8.byteOffset, u8.byteOffset + u8.byteLength);
    const files = await ZIP.readZip(ab);
    const docXml = files.get("word/document.xml");
    if (!docXml) throw new Error("这个 docx 里找不到 document.xml，可能文件损坏");

    const xml = dec.decode(docXml);
    const paras = collectParagraphs(xml);

    // 附加部件（批注/脚注/页眉页脚）和正文一起 prepare，保证同一个实体是同一个替身
    const extraNames = [...files.keys()].filter((n) => EXTRA_PART_RE.test(n)).sort();
    const extraXml = extraNames.map((n) => dec.decode(files.get(n)));
    const extraAll = [];
    for (const x of extraXml) extraAll.push(...extraTexts(x));

    const redactor = new ENGINE.Redactor(config, opts.mode, opts.maskStyle, opts.redactLen);
    const results = redactor.prepare(paras.map((p) => p.text).concat(extraAll));
    const bodyResults = results.slice(0, paras.length);
    const extraResults = results.slice(paras.length);

    const newXml = rebuildXml(xml, bodyResults);
    files.set("word/document.xml", enc.encode(newXml));

    // 回写附加部件
    let idx = 0;
    extraNames.forEach((n, i) => {
      const count = extraTexts(extraXml[i]).length;
      const slice = extraResults.slice(idx, idx + count).map((r) => r.redacted);
      idx += count;
      files.set(n, enc.encode(rebuildExtra(extraXml[i], slice)));
    });

    return { bytes: await ZIP.writeZip(files), redactor, results: bodyResults };
  }

  function processText(text, config, opts) {
    opts = opts || {};
    const redactor = new ENGINE.Redactor(config, opts.mode, opts.maskStyle, opts.redactLen);
    const results = redactor.prepare([text]);
    let out = text;
    for (const [start, end, rep] of results[0].spans.slice().sort((a, b) => b[0] - a[0])) {
      out = out.slice(0, start) + rep + out.slice(end);
    }
    return { text: out, redactor, results };
  }

  /** 还原：把「替身」按映射表换回原文（逐段处理，保留格式） */
  async function restoreDocx(bytes, pairs) {
    const u8 = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
    const ab = u8.buffer.slice(u8.byteOffset, u8.byteOffset + u8.byteLength);
    const files = await ZIP.readZip(ab);
    const docXml = files.get("word/document.xml");
    const xml = dec.decode(docXml);

    const sorted = pairs.slice().sort((a, b) => b[0].length - a[0].length);
    PARA_RE.lastIndex = 0;
    let out = "", last = 0, m;
    while ((m = PARA_RE.exec(xml))) {
      out += xml.slice(last, m.index);
      const { text } = scanParagraph(m[0]);
      const spans = [];
      for (const [rep, orig] of sorted) {
        let from = 0;
        while (true) {
          const idx = text.indexOf(rep, from);
          if (idx < 0) break;
          if (!spans.some(([a, b]) => idx < b && a < idx + rep.length)) {
            spans.push([idx, idx + rep.length, orig]);
          }
          from = idx + rep.length;
        }
      }
      spans.sort((a, b) => a[0] - b[0]);
      out += spans.length ? rewriteParagraph(m[0], spans) : m[0];
      last = m.index + m[0].length;
    }
    out += xml.slice(last);
    files.set("word/document.xml", enc.encode(out));
    return await ZIP.writeZip(files);
  }

  root.LR_DOCX = { processDocx, processText, collectParagraphs, rebuildXml,
                   rewriteParagraph, scanParagraph, unescapeXml, escapeXml, restoreDocx };
})(typeof window !== "undefined" ? window : globalThis);

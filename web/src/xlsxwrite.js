/* xlsx 文字回写：只改 sharedStrings.xml 里的字符串 + 单元格里的 inline 字符串，
   其它 XML 一个字节都不动（样式/列宽/合并/公式/数字格式自然全部还在）。
   这一层只管 XML 手术，怎么找出要改哪些文字由 xlsx.js 决定。 */
(function (root) {
  const SI_RE = /<si\b[^>]*?(?:\/>|>([\s\S]*?)<\/si>)/g;
  const CELL_RE = /<c\b([^>]*?)(?:\/>|>([\s\S]*?)<\/c>)/g;
  const T_RE = /<t\b[^>]*>([\s\S]*?)<\/t>/g;

  function unescapeXml(s) {
    return s.replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"')
            .replace(/&apos;/g, "'").replace(/&#(\d+);/g, (_, d) => String.fromCodePoint(+d))
            .replace(/&#x([0-9a-fA-F]+);/g, (_, h) => String.fromCodePoint(parseInt(h, 16)))
            .replace(/&amp;/g, "&");
  }

  function escapeXml(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  /** 取标签属性：前面垫个空格，免得 r="A1" 匹配到 sr="..." 里面去 */
  function attr(tag, name) {
    const m = (" " + String(tag || "")).match(new RegExp("\\s" + name + '\\s*=\\s*"([^"]*)"'));
    return m ? unescapeXml(m[1]) : "";
  }

  /** <si> / <is> 里的可见文字：所有 <t> 拼起来，跳过拼音标注 <rPh> */
  function containerText(inner) {
    const clean = String(inner || "").replace(/<rPh\b[\s\S]*?<\/rPh>/g, "");
    let out = "", m;
    T_RE.lastIndex = 0;
    while ((m = T_RE.exec(clean))) out += unescapeXml(m[1]);
    return out;
  }

  /** 用新文字重建 <si> / <is> 的内容：普通串一个 <t>；富文本保住首段的字体 */
  function containerBody(inner, text) {
    const body = '<t xml:space="preserve">' + escapeXml(text) + "</t>";
    const runs = String(inner || "").match(/<r\b[^>]*>[\s\S]*?<\/r>/g);
    if (!runs || !runs.length) return body;
    const pr = (runs[0].match(/<rPr\s*\/>|<rPr>[\s\S]*?<\/rPr>/) || [""])[0];
    return "<r>" + pr + body + "</r>";
  }

  /** 改共享字符串：updates 是 Map<序号, 新文字>，序号 = sharedStrings 里 <si> 的顺序。
      原地换文字、条目不增不减，所以各 sheet 里的 t="s" 索引全都不用动。 */
  function rewriteSharedStrings(xml, updates) {
    if (!updates || !updates.size) return xml;
    let i = -1;
    return xml.replace(SI_RE, (node, inner) => {
      i += 1;
      if (!updates.has(i)) return node;
      const open = node.slice(0, node.indexOf(">") + 1);
      const attrs = open.replace(/^<si/, "").replace(/\/?>$/, "").trim();
      return "<si" + (attrs ? " " + attrs : "") + ">" + containerBody(inner, updates.get(i)) + "</si>";
    });
  }

  /** 改 inline 字符串：updates 是 Map<单元格坐标, 新文字>，只碰 t="inlineStr" 的单元格 */
  function rewriteInlineCells(xml, updates) {
    if (!updates || !updates.size) return xml;
    return xml.replace(CELL_RE, (node, attrStr, inner) => {
      if (!inner) return node;
      const ref = attr(attrStr, "r");
      if (!ref || attr(attrStr, "t") !== "inlineStr" || !updates.has(ref)) return node;
      const m = inner.match(/<is\b([^>]*)>([\s\S]*?)<\/is>/);
      if (!m) return node;
      const is = "<is" + m[1] + ">" + containerBody(m[2], updates.get(ref)) + "</is>";
      return "<c" + attrStr + ">" + inner.replace(m[0], is) + "</c>";
    });
  }

  /** 打包回 xlsx 字节（压缩策略交给 zip.js） */
  async function pack(files) {
    return await root.LR_ZIP.writeZip(files);
  }

  root.LR_XLSXWRITE = { escapeXml, unescapeXml, attr, containerText, containerBody,
                        rewriteSharedStrings, rewriteInlineCells, pack };
})(typeof window !== "undefined" ? window : globalThis);

/* 网页版图片脱敏：浏览器内 OCR（tesseract.js）→ 规则引擎 → 按字宽比例涂黑。
 * 依赖：window.LR_OCR（ocrweb.js）、window.LR_ENGINE
 * 坐标口径与 Python 版 legalredact/imgio.py 一致：
 *   中文 1.0、ASCII 0.5、空格 0.3 估宽，外扩 2px/1px。
 */
(function (root) {
  const PAD_X = 2;
  const PAD_Y = 1;

  /** OCR 文本会在数字/中文之间插空格，先清掉再交给检测器 */
  function cleanText(t) {
    return String(t || "").replace(/(?<=[\u4e00-\u9fa5\d])[ \u3000]+(?=[\u4e00-\u9fa5\d])/g, "");
  }

  function weights(text) {
    const out = [];
    for (const ch of text) {
      if (/\s/.test(ch)) out.push(0.3);
      else if (ch.charCodeAt(0) < 128) out.push(0.5);
      else out.push(1.0);
    }
    return out;
  }

  function sliceX(text, box, i0, i1) {
    const ws = weights(text);
    const total = ws.reduce((a, b) => a + b, 0) || 1;
    const left = box.x0, width = box.x1 - box.x0;
    const pre0 = ws.slice(0, Math.max(0, Math.min(i0, ws.length))).reduce((a, b) => a + b, 0);
    const pre1 = ws.slice(0, Math.max(0, Math.min(i1, ws.length))).reduce((a, b) => a + b, 0);
    return [left + (width * pre0) / total, left + (width * pre1) / total];
  }

  /**
   * 用「词级框」建一张「字符下标 → x 坐标」表。
   * tesseract 每个词都有真实 box，比按整行字数均分准得多。
   * 拼出来的字符串和行文本对不上时返回 null（调用方退回按比例切）。
   */
  function buildCharX(lineText, words) {
    if (!words || !words.length || !lineText) return null;

    // 先把所有词的字符和坐标摊平（词内按字宽比例）
    const wchars = [];
    const wx = [];
    let lastRight = null;
    for (const w of words) {
      const wt = cleanText(w.text || "");
      if (!wt) continue;
      const box = w.box || {};
      const left = box.x0, right = box.x1;
      const ws = weights(wt);
      const total = ws.reduce((a, b) => a + b, 0) || 1;
      let acc = 0;
      for (let i = 0; i < wt.length; i++) {
        wchars.push(wt[i]);
        wx.push(left + (right - left) * (acc / total));
        acc += ws[i];
      }
      lastRight = right;
    }
    if (!wchars.length || lastRight === null) return null;

    // 再按「行文本」对齐：空格算零宽，其余字符依次吃掉词字符
    const xs = [];
    let ci = 0;
    let prev = wx[0];
    for (let i = 0; i < lineText.length; i++) {
      const ch = lineText[i];
      if (ch === " " || ch === "\u3000") { xs.push(prev); continue; }
      if (ci >= wchars.length) return null;          // 词字符不够，放弃
      xs.push(wx[ci]);
      prev = wx[ci];
      ci += 1;
    }
    if (ci === 0) return null;
    xs.push(lastRight);
    return xs;
  }

  function loadImage(bytes, mime) {
    return new Promise((resolve, reject) => {
      const blob = bytes instanceof Blob ? bytes : new Blob([bytes], { type: mime || "image/png" });
      const url = URL.createObjectURL(blob);
      const img = new Image();
      img.onload = () => { URL.revokeObjectURL(url); resolve(img); };
      img.onerror = (e) => { URL.revokeObjectURL(url); reject(new Error("图片解码失败")); };
      img.src = url;
    });
  }

  function toBlob(canvas, type) {
    return new Promise((resolve) => {
      if (canvas.toBlob) canvas.toBlob((b) => resolve(b), type, 0.92);
      else resolve(null);
    });
  }

  const LR_IMG = {
    /** 判断浏览器能不能做图片脱敏（WASM SIMD + OCR 模块在位） */
    available() {
      return !!(root.LR_OCR && root.LR_OCR.recognize && typeof WebAssembly === "object");
    },

    /**
     * @param {Uint8Array|Blob} input 图片
     * @param {object} redactor LR_ENGINE.Redactor 实例
     * @param {object} opts { mime, style }
     * @returns {Promise<{ok, bytes, hits, text, reason}>}
     */
    async redact(input, redactor, opts) {
      opts = opts || {};
      if (!this.available()) return { ok: false, hits: [], reason: "浏览器不支持网页内 OCR" };

      const mime = opts.mime || "image/png";

      // 1) 先把图放到画布上：大图限宽 2400（Safari 画布面积上限），小图放大（实测能提升识别）
      const img = await loadImage(input, mime);
      const MAX_W = 2400;
      const MIN_W = 1600;
      let outScale = Math.min(1, MAX_W / img.naturalWidth);
      if (img.naturalWidth < MIN_W) outScale = Math.min(2, MIN_W / img.naturalWidth);
      const canvas = document.createElement("canvas");
      canvas.width = Math.round(img.naturalWidth * outScale);
      canvas.height = Math.round(img.naturalHeight * outScale);
      const ctx = canvas.getContext("2d");
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
      const workBlob = await new Promise((res) => canvas.toBlob(res, "image/png"));

      // 2) 识别这块画布（两种 psm 取中文更多的那份）
      let ocr = null, bestScore = -1, lastReason = "";
      for (const psm of [11, 6]) {
        let r;
        try { r = await root.LR_OCR.recognize(workBlob, { psm: psm }); }
        catch (e) { lastReason = e && e.message ? e.message : String(e); continue; }
        if (!r || r.ok === false) { lastReason = (r && r.reason) || "未知"; continue; }
        const score = String(r.text || "").replace(/[^\u4e00-\u9fa5]/g, "").length;
        if (score > bestScore) { bestScore = score; ocr = r; }
        if (bestScore > 20) break;
      }
      if (!ocr) return { ok: false, hits: [], reason: "OCR 失败：" + (lastReason || "未知") };

      const lines = (ocr.lines || []).map((l) => ({
        text: cleanText(l.text),
        box: l.box,
        confidence: l.confidence || 0,
      })).filter((l) => l.text.trim());

      if (!lines.length) return { ok: true, hits: [], text: "", bytes: null, reason: "图上没有识别到文字" };

      const full = lines.map((l) => l.text).join("\n");
      const br = redactor.prepare([full])[0];

      // 行内偏移表
      const offsets = [];
      let pos = 0;
      for (const l of lines) {
        offsets.push({ start: pos, end: pos + l.text.length });
        pos += l.text.length + 1;
      }

      // OCR 模块内部可能又缩放了一次，按它报告的 scale 还原到画布坐标
      const sx = (ocr.image && ocr.image.scaleX) || 1;
      const sy = (ocr.image && ocr.image.scaleY) || 1;

      // 每行建一张字符坐标表（词级框），建不出来就退回按比例切
      const charXs = lines.map((l, i) => {
        const idxs = (ocr.lines && ocr.lines[i] && ocr.lines[i].wordIndexes) || [];
        const words = idxs.map((k) => ocr[k]).filter(Boolean);
        return buildCharX(l.text, words);
      });

      const hits = [];
      for (const sp of br.spans) {
        const [start, end, repl, kind] = sp;
        for (let li = 0; li < lines.length; li++) {
          const { start: ls, end: le } = offsets[li];
          if (!(start < le && ls < end)) continue;
          const line = lines[li];
          const i0 = Math.max(start, ls) - ls;
          const i1 = Math.min(end, le) - ls;
          if (i1 <= i0) continue;

          const cx = charXs[li];
          const [x0, x1] = (cx && cx[i0] !== undefined && cx[i1] !== undefined)
            ? [cx[i0], cx[i1]]
            : sliceX(line.text, line.box, i0, i1);
          const bx0 = Math.max(0, x0 / sx - PAD_X);
          const bx1 = Math.min(canvas.width, x1 / sx + PAD_X);
          const by0 = Math.max(0, line.box.y0 / sy - PAD_Y);
          const by1 = Math.min(canvas.height, line.box.y1 / sy + PAD_Y);
          if (bx1 <= bx0 || by1 <= by0) continue;

          ctx.fillStyle = "#000";
          ctx.fillRect(bx0, by0, bx1 - bx0, by1 - by0);
          hits.push({ text: line.text.slice(i0, i1), type: kind, replacement: repl, box: [bx0, by0, bx1, by1] });
        }
      }

      let blob = await toBlob(canvas, mime === "image/jpeg" ? "image/jpeg" : "image/png");
      let bytes = blob ? new Uint8Array(await blob.arrayBuffer()) : null;
      if (!bytes) {
        // 兜底：有些浏览器 toBlob 会失败，用 dataURL 转
        try {
          const dataUrl = canvas.toDataURL(mime === "image/jpeg" ? "image/jpeg" : "image/png");
          const b64 = dataUrl.split(",")[1] || "";
          const bin = atob(b64);
          bytes = new Uint8Array(bin.length);
          for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
        } catch (e) { bytes = null; }
      }
      return { ok: true, bytes, hits, text: br.redacted, reason: hits.length ? "" : "没有识别到敏感信息" };
    },
  };

  root.LR_IMG = LR_IMG;
})(typeof window !== "undefined" ? window : globalThis);

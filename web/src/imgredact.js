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
      let ocr;
      try {
        ocr = await root.LR_OCR.recognize(input);
      } catch (e) {
        return { ok: false, hits: [], reason: "OCR 失败：" + (e && e.message ? e.message : e) };
      }
      if (!ocr || ocr.ok === false) {
        return { ok: false, hits: [], reason: "OCR 失败：" + ((ocr && ocr.reason) || "未知") };
      }

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

      const img = await loadImage(input, mime);
      const canvas = document.createElement("canvas");
      canvas.width = img.naturalWidth;
      canvas.height = img.naturalHeight;
      const ctx = canvas.getContext("2d");
      ctx.drawImage(img, 0, 0);

      // OCR 坐标基于预处理图，按 scale 还原到原图
      const sx = (ocr.image && ocr.image.scaleX) || 1;
      const sy = (ocr.image && ocr.image.scaleY) || 1;

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

          const [x0, x1] = sliceX(line.text, line.box, i0, i1);
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

      const blob = await toBlob(canvas, mime === "image/jpeg" ? "image/jpeg" : "image/png");
      const bytes = blob ? new Uint8Array(await blob.arrayBuffer()) : null;
      return { ok: true, bytes, hits, text: br.redacted, reason: hits.length ? "" : "没有识别到敏感信息" };
    },
  };

  root.LR_IMG = LR_IMG;
})(typeof window !== "undefined" ? window : globalThis);

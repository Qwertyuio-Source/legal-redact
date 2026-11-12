/* 页面逻辑：拖拽 → 浏览器内脱敏 → 下载 */
(function () {
  // 先占位：初始化万一失败，也不要让看门狗把界面藏起来
  window.LR_APP = window.LR_APP || { ready: false };

  const D = window.LR_DOCX;
  const KIND_CN = window.LR_TYPES.CN;
  const TYPES = ["person", "org", "address", "id", "phone", "email"];

  const state = { results: [], running: 0 };
  const $ = (s) => document.querySelector(s);
  const list = $("#list");
  const empty = $("#empty");
  const summary = $("#summary");
  const dlAll = $("#downloadAll");

  // 能力预检：老浏览器上手就说清楚，别等拖进文件才报错
  if (typeof DecompressionStream === "undefined") {
    const d = document.getElementById("bootwarn");
    if (d) d.style.display = "block";
  }

  /* ---------- 工具 ---------- */
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  function highlight(text, spans, cls) {
    let out = "", last = 0;
    for (const sp of spans) {
      if (sp[0] < last) continue;
      out += esc(text.slice(last, sp[0]));
      out += `<mark class="${cls}">${esc(text.slice(sp[0], sp[1]))}</mark>`;
      last = sp[1];
    }
    return out + esc(text.slice(last));
  }

  function download(name, blob) {
    if (window.LR_DESKTOP && window.LR_DESKTOP.available) {
      window.LR_DESKTOP.save(name, blob).then(function (r) {
        if (r && r.path) console.log("已保存：" + r.path);
      });
      return;
    }
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1500);
  }

  function csv(rows) {
    const body = rows.map((r) => r.map((v) => `"${String(v).replace(/"/g, '""')}"`).join(",")).join("\r\n");
    return new Blob(["\ufeff" + body], { type: "text/csv;charset=utf-8" });
  }

  function textBlob(s) { return new Blob([s], { type: "text/plain;charset=utf-8" }); }

  function decodeText(buf) {
    try { return new TextDecoder("utf-8", { fatal: true }).decode(buf); }
    catch (e) { return new TextDecoder("gb18030").decode(buf); }
  }

  function isDefaultRange() {
    const el = document.querySelector("input[name=range]:checked");
    return !el || el.value === "default";
  }

  function config() {
    const enable = {};
    const all = isDefaultRange();          // 默认模式：一律全开，不受勾选影响
    for (const t of TYPES) enable[t] = all ? true : $("#opt-" + t).checked;
    return { enable };
  }

  /* ---------- 处理 ---------- */
  async function processOne(file, name) {
    const buf = new Uint8Array(await file.arrayBuffer());
    const ext = (file.name.split(".").pop() || "").toLowerCase();

    const opts = {
      mode: (document.querySelector("input[name=mode]:checked") || {}).value || "mask",
      maskStyle: $("#opt-full").checked ? "full" : "surname",
    };
    if (ext === "pdf") {
      const redactor = new window.LR_ENGINE.Redactor(config(), opts.mode, opts.maskStyle);
      let out;
      try {
        out = await window.LR_PDFREDACT.redactToPdf(buf, redactor, { width: 1500 });
      } catch (err) {
        lastProcessError = friendlyError(err);
        showPdfGuide([file.name], "这个 PDF 读不出来（" + lastProcessError + "）");
        return null;
      }
      const text = out.results.map((r) => r.redacted).join("\n\n");
      if (text.replace(/\s/g, "").length < 20) {
        lastProcessError = "这份 PDF 没有文字层（扫描件），请先用 图片PDF转文字.bat 转成文字";
        showPdfGuide([file.name], lastProcessError);
        return null;
      }
      const outFiles = [
        { name: `${name}.脱敏.pdf`,
          blob: new Blob([out.pdf], { type: "application/pdf" }) },
      ];
      if ($("#opt-docx").checked) {
        const docxBytes = await window.LR_DOCXWRITE.makeDocx(text.split("\n"));
        outFiles.push({
          name: `${name}.脱敏.docx`,
          blob: new Blob([docxBytes], { type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document" }),
        });
        outFiles.push({ name: `${name}.脱敏.txt`, blob: textBlob(text) });
      }
      return { file, name, ext: "pdf", redactor, redactedTexts: [text], outFiles };
    }

    if (ext === "png" || ext === "jpg" || ext === "jpeg") {
      if (!window.LR_IMG || !window.LR_IMG.available()) {
        throw new Error("这个浏览器不支持网页内 OCR，请用桌面版处理图片");
      }
      const redactor = new window.LR_ENGINE.Redactor(config(), opts.mode, opts.maskStyle);
      const mime = ext === "png" ? "image/png" : "image/jpeg";
      const out = await window.LR_IMG.redact(buf, redactor, { mime });
      if (!out.ok) throw new Error(out.reason || "图片处理失败");
      if (!out.bytes) throw new Error("这张图没能生成涂黑版（浏览器画布限制），请换用桌面版处理");
      const outFiles = [
        { name: `${name}.脱敏.${ext}`, blob: new Blob([out.bytes], { type: mime }) },
        { name: `${name}.脱敏.txt`, blob: textBlob(out.text || "") },
      ];
      return { file, name, ext, redactor, redactedTexts: [out.text || ""], outFiles };
    }

    if (ext === "xlsx") {
      const redactor = new window.LR_ENGINE.Redactor(config(), opts.mode, opts.maskStyle);
      const out = await window.LR_XLSX.redact(buf, redactor);
      return {
        file, name, ext, redactor,
        redactedTexts: out.sheets ? out.sheets.map((sh) => sh.redacted || "") : [],
        outFiles: [{ name: `${name}.脱敏.xlsx`, blob: new Blob([out.bytes],
          { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" }) }],
      };
    }

    if (ext === "docx") {
      const { bytes, redactor } = await D.processDocx(buf, config(), opts);
      const outName = `${name}.脱敏.docx`;
      const redactedTexts = redactor.results.map((r) => r.redacted);
      return {
        file, name, ext, redactor, redactedTexts,
        outFiles: [{ name: outName,
                     blob: new Blob([bytes], { type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document" }) }],
      };
    }

    const text = decodeText(buf);
    const { text: out, redactor } = D.processText(text, config(), opts);
    const extOut = ext === "md" ? "md" : "txt";
    return {
      file, name, ext, redactor, redactedTexts: [out],
      outFiles: [{ name: `${name}.脱敏.${extOut}`, blob: textBlob(out) }],
    };
  }

  function previewItems(res) {
    const items = [];
    res.redactor.results.forEach((br, i) => {
      if (!br.spans.length) return;
      items.push({
        no: i + 1,
        original: br.text,
        redacted: br.redacted,
        spans: br.spans.map((s) => [s[0], s[1]]),
        posSpans: br.posSpans.map((s) => [s[0], s[1]]),
      });
    });
    return items;
  }

  function previewHtml(items) {
    const shown = items.slice(0, 8);
    let html = "";
    for (const it of shown) {
      html += `<div class="pv">
        <div class="row"><div class="tag">原文</div><div class="txt">${highlight(it.original, it.spans, "del")}</div></div>
        <div class="row"><div class="tag">脱敏</div><div class="txt">${highlight(it.redacted, it.posSpans, "ins")}</div></div>
      </div>`;
    }
    if (items.length > shown.length) {
      html += `<div class="pv" style="color:var(--muted);font-size:12.5px">… 另有 ${items.length - shown.length} 段有改动，看「对照.txt」更全</div>`;
    }
    return html;
  }

  function cardHtml(res, idx) {
    const rows = res.redactor.rows;
    const leftovers = res.redactor.issues.filter((i) => i.kind === "leftover");
    const candidates = res.redactor.issues.filter((i) => i.kind === "candidate");
    const counts = {};
    for (const r of rows) counts[r.kind] = (counts[r.kind] || 0) + 1;
    const detail = Object.entries(counts).map(([k, n]) => `${KIND_CN[k] || k} ${n}`).join(" · ") || "无";

    const stats = [
      `<span class="stat">脱敏 <b>${rows.length}</b> 处</span>`,
      `<span class="stat">${detail}</span>`,
      leftovers.length
        ? `<span class="stat bad">残留 <b>${leftovers.length}</b> 处</span>`
        : `<span class="stat ok">✓ 二次扫描无残留</span>`,
      candidates.length ? `<span class="stat warn">待确认 <b>${candidates.length}</b></span>` : "",
    ].join("");

    return `<div class="card" data-idx="${idx}">
      <div class="head">
        <span class="badge ${res.ext === "docx" ? "" : "txt"}">${res.ext.toUpperCase()}</span>
        <span class="name">${esc(res.name)}</span>
      </div>
      <div class="stats">${stats}</div>
      <div class="actions">
        <button class="btn small primary" data-act="out">${res.ext === "pdf" ? "下载脱敏 PDF" : "下载脱敏件"}</button>
        <button class="btn small" data-act="compare">对照.txt</button>
        <button class="btn small" data-act="mapping">映射表.json</button>
        <button class="btn small" data-act="csv">明细.csv</button>
      </div>
      <div class="preview">${previewHtml(previewItems(res))}</div>
    </div>`;
  }

  function render() {
    if (!state.results.length) {
      list.innerHTML = "";
      list.appendChild(empty);
      dlAll.disabled = true;
      summary.textContent = "";
      return;
    }
    list.innerHTML = state.results.map((r, i) => cardHtml(r, i)).join("");

    const total = state.results.reduce((s, r) => s + r.redactor.rows.length, 0);
    const left = state.results.reduce(
      (s, r) => s + r.redactor.issues.filter((i) => i.kind === "leftover").length, 0);
    summary.innerHTML = `共 <b>${state.results.length}</b> 个文件 · 脱敏 <b>${total}</b> 处 · ` +
      (left ? `<span style="color:var(--danger)">残留 ${left} 处</span>` : `<span style="color:var(--ok)">无残留</span>`);
    dlAll.disabled = false;
  }

  /* ---------- 下载 ---------- */
  function compareText(res) {
    const lines = ["原文  →  脱敏后", "=".repeat(50)];
    for (const it of previewItems(res)) {
      lines.push("原文：" + it.original);
      lines.push("脱敏：" + it.redacted);
      lines.push("");
    }
    return lines.join("\n");
  }

  function detailCsv(res) {
    const rows = [["文件", "段落", "类型", "原文", "替换为", "规则", "位置"]];
    for (const r of res.redactor.rows) {
      rows.push([res.file.name, r.block, KIND_CN[r.type] || r.type,
                 r.original, r.replacement, r.rule, r.start]);
    }
    return csv(rows);
  }

  function handleDownload(res, act) {
    if (act === "out") {
      for (const f of res.outFiles) download(f.name, f.blob);
    } else if (act === "compare") {
      download(`${res.name}.对照.txt`, textBlob(compareText(res)));
    } else if (act === "mapping") {
      download(`${res.name}.映射表.json`,
               textBlob(JSON.stringify(res.redactor.mapping(), null, 2)));
    } else if (act === "csv") {
      download(`${res.name}.明细.csv`, detailCsv(res));
    }
  }

  async function downloadAll() {
    const entries = [];
    const seen = new Set();
    const uniq = (n) => {
      let name = n, i = 2;
      while (seen.has(name)) { name = n.replace(/(\.[^.]+)$/, `-${i}$1`); i++; }
      seen.add(name);
      return name;
    };

    for (const res of state.results) {
      for (const f of res.outFiles) {
        entries.push([uniq(f.name), new Uint8Array(await f.blob.arrayBuffer())]);
      }
      entries.push([uniq(`${res.name}.对照.txt`),
                    new TextEncoder().encode(compareText(res))]);
      entries.push([uniq(`${res.name}.映射表.json`),
                    new TextEncoder().encode(JSON.stringify(res.redactor.mapping(), null, 2))]);
      entries.push([uniq(`${res.name}.明细.csv`),
                    new Uint8Array(await detailCsv(res).arrayBuffer())]);
    }

    const total = state.results.reduce((s, r) => s + r.redactor.rows.length, 0);
    const left = state.results.reduce(
      (s, r) => s + r.redactor.issues.filter((i) => i.kind === "leftover").length, 0);
    const report = ["法律文书脱敏 —— 运行报告", "=".repeat(40), "",
      `处理文件：${state.results.length} 个`,
      `脱敏片段：${total} 处`,
      `二次扫描残留：${left} 处`, ""];
    for (const res of state.results) {
      const n = res.redactor.rows.length;
      const l = res.redactor.issues.filter((i) => i.kind === "leftover").length;
      report.push(`· ${res.file.name}  脱敏 ${n} 处，${l ? "⚠ 有残留" : "✓ 无残留"}`);
    }
    entries.push([uniq("运行报告.txt"), new TextEncoder().encode(report.join("\n"))]);

    const bytes = await window.LR_ZIP.writeZip(entries);
    download("脱敏结果.zip", new Blob([bytes], { type: "application/zip" }));
  }

  /* ---------- 事件 ---------- */
  let lastProcessError = "";

  /** 把底层英文错误翻译成人话 */
  function friendlyError(err) {
    const m = String((err && err.message) || err || "");
    if (/password|encrypt/i.test(m)) return "PDF 有密码保护，请先解密再处理";
    if (/Invalid PDF|PDF structure|corrupt/i.test(m)) return "PDF 文件损坏或格式异常";
    if (/Unsupported|not supported/i.test(m)) return "这个文件格式暂不支持";
    return m || "处理失败";
  }

  async function addFiles(fileList) {
    const all = [...fileList];
    const files = all.filter((f) => /\.(docx|xlsx|txt|md|html|pdf|png|jpg|jpeg)$/i.test(f.name));
    if (!files.length) {
      if (!images.length) alert("支持 .docx / .txt / .md / .html / .pdf；图片请先用 图片PDF转文字.bat 转成文字。");
      return;
    }

    empty.remove();
    const placeholder = document.createElement("div");
    placeholder.className = "empty";
    placeholder.innerHTML = `<span class="spinner"></span>正在处理 ${files.length} 个文件…`;
    list.appendChild(placeholder);

    for (const file of files) {
      const base = file.name.replace(/\.[^.]+$/, "");
      try {
        const res = await processOne(file, base);
        if (res) { state.results.push(res); await saveRecord(res); }
      } catch (err) {
        lastProcessError = friendlyError(err);
        const div = document.createElement("div");
        div.className = "card";
        div.innerHTML = `<div class="head"><span class="badge" style="background:var(--danger-soft);color:var(--danger)">失败</span>
          <span class="name">${esc(file.name)}</span></div>
          <div class="stats"><span class="stat bad">${esc(err.message || String(err))}</span></div>`;
        list.appendChild(div);
      }
    }
    placeholder.remove();
    render();
  }

  function showPdfGuide(names, kind) {
    kind = kind || "PDF";
    if (document.getElementById("pdf-guide")) return;
    const div = document.createElement("div");
    div.id = "pdf-guide";
    div.className = "card";
    div.innerHTML = `<div class="head"><span class="badge" style="background:var(--warn-soft);color:var(--warn)">PDF / 图片</span>
      <span class="name">${names.map(esc).join("、")}</span></div>
      <div style="padding:0 18px 16px;font-size:14px;line-height:1.9">
        这个文件里没有可读的文字层（是扫描/拍照），浏览器没法直接读。不用装软件，两步就行：<br>
        <b>1.</b> 双击 <b>图片PDF转文字.bat</b>，把这个文件拖进黑窗口，回车<br>
        <b>2.</b> 转换完的文字会<b>自动复制到剪贴板</b>，回到这里点 <b>「粘贴文字」</b>，Ctrl+V 再点「脱敏这段文字」<br>
        <span style="color:var(--muted);font-size:13px">（用的是 Windows 自带中文 OCR，离线、不联网）</span>
      </div>`;
    list.insertBefore(div, list.firstChild);
  }

  async function redactPasted(text) {
    if (!text.trim()) { alert("先粘贴文字"); return; }
    empty.remove();
    const opts = {
      mode: (document.querySelector("input[name=mode]:checked") || {}).value || "mask",
      maskStyle: $("#opt-full").checked ? "full" : "surname",
    };
    const { text: out, redactor } = D.processText(text, config(), opts);
    state.results.push({
      file: { name: "粘贴的文本" }, name: "粘贴的文本", ext: "txt", redactor,
      redactedTexts: [out],
      outFiles: [{ name: "粘贴的文本.脱敏.txt", blob: textBlob(out) }],
    });
    render();
  }

  function syncRangeUI() {
    const box = $("#options");
    if (!box) return;
    box.style.display = isDefaultRange() ? "none" : "flex";
  }

  const drop = $("#drop");
  const input = $("#file");

  input.addEventListener("change", () => { addFiles(input.files); input.value = ""; });

  ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => {
    e.preventDefault(); drop.classList.add("over");
  }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => {
    e.preventDefault(); drop.classList.remove("over");
  }));
  drop.addEventListener("drop", (e) => { if (e.dataTransfer.files.length) addFiles(e.dataTransfer.files); });
  document.addEventListener("dragover", (e) => e.preventDefault());
  document.addEventListener("drop", (e) => e.preventDefault());

  list.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-act]");
    if (!btn) return;
    const card = btn.closest(".card");
    const res = state.results[Number(card.dataset.idx)];
    if (res) handleDownload(res, btn.dataset.act);
  });

  (function () {
    const btn = $("#folder-open");
    const inp = $("#folder-input");
    if (!btn || !inp) return;
    btn.addEventListener("click", function () { inp.click(); });
    inp.addEventListener("change", function (e) {
      const all = [...e.target.files];
      e.target.value = "";
      const ok = all.filter((f) => /\.(docx|xlsx|txt|md|html|pdf|png|jpg|jpeg)$/i.test(f.name));
      if (!ok.length) { alert("这个文件夹里没有可处理的文件（支持 docx/xlsx/pdf/txt/md/html/图片）"); return; }
      // 重名的用「上级目录_文件名」区分，避免输出互相覆盖
      const count = new Map();
      ok.forEach((f) => count.set(f.name, (count.get(f.name) || 0) + 1));
      const files = ok.map((f) => {
        if (count.get(f.name) < 2) return f;
        const rel = f.webkitRelativePath || "";
        const dir = rel.split("/").slice(-2, -1)[0] || "子目录";
        return new File([f], dir + "_" + f.name, { type: f.type });
      });
      addFiles(files);
    });
  })();

  $("#paste-open").addEventListener("click", () => {
    const panel = $("#paste-panel");
    panel.style.display = "block";
    const ta = $("#paste-text");
    ta.focus();
    if (navigator.clipboard && navigator.clipboard.readText) {
      navigator.clipboard.readText().then((t) => { if (t && !ta.value) ta.value = t; }).catch(() => {});
    }
  });
  $("#paste-close").addEventListener("click", () => { $("#paste-panel").style.display = "none"; });
  $("#paste-run").addEventListener("click", () => redactPasted($("#paste-text").value));

  $("#downloadAll").addEventListener("click", downloadAll);

  document.querySelectorAll(".tab").forEach((t) =>
    t.addEventListener("click", () => switchTab(t.dataset.tab)));

  document.querySelectorAll("input[name=range]").forEach((el) =>
    el.addEventListener("change", syncRangeUI));
  syncRangeUI();

  $("#restore-open").addEventListener("click", () => {
    const p = $("#restore-panel");
    p.style.display = p.style.display === "none" ? "block" : "none";
  });
  $("#rs-close").addEventListener("click", () => { $("#restore-panel").style.display = "none"; });
  $("#rs-run").addEventListener("click", doRestore);
  $("#rs-file").addEventListener("change", () => {
    const n = $("#rs-file").files[0];
    restoreTip(n ? "已选：" + n.name : "");
  });
  $("#lib-search").addEventListener("input", renderLibrary);
  $("#lib-cat").addEventListener("change", renderLibrary);
  $("#lib-refresh").addEventListener("click", renderLibrary);
  $("#lib-export").addEventListener("click", downloadAllRecords);
  $("#lib-list").addEventListener("click", handleLibClick);

  (function () {
    const btn = $("#lib-import-btn");
    const inp = $("#lib-import");
    if (!btn || !inp) return;
    btn.addEventListener("click", function () { inp.click(); });
    inp.addEventListener("change", importLocalLibrary);
  })();

  /** 把桌面版写的「文档\法律脱敏库」导入进来（选那个文件夹即可） */
  async function importLocalLibrary(e) {
    const picked = [...e.target.files];
    e.target.value = "";
    const groups = new Map();
    for (const f of picked) {
      const rel = f.webkitRelativePath || f.name;
      const dir = rel.split("/").slice(0, -1).join("/");
      if (!groups.has(dir)) groups.set(dir, []);
      groups.get(dir).push(f);
    }
    let n = 0;
    for (const list of groups.values()) {
      const metaFile = list.find((f) => f.name === "record.json");
      if (!metaFile) continue;
      let meta;
      try { meta = JSON.parse(await metaFile.text()); } catch (err) { continue; }
      const outs = list.filter((f) => f.name !== "record.json");
      if (!outs.length) continue;
      try {
        await window.LR_STORE.add({
          seq: meta.seq,
          name: meta.name || outs[0].name,
          ext: ((meta.name || "").split(".").pop() || "").toLowerCase(),
          mode: meta.mode || "mask",
          count: meta.count || 0,
          category: meta.category || "未分类",
          createdAt: meta.createdAt || Date.now(),
          files: outs.map((f) => ({ name: f.name, blob: f })),
        });
        n++;
      } catch (err) { /* 跳过重复 */ }
    }
    alert("已导入 " + n + " 条记录到文件夹");
    renderLibrary();
    updateLibCount();
  }
  $("#lib-list").addEventListener("change", handleLibClick);
  updateLibCount();

  /* ---------- 桌面版无界面模式（右键 → 直接脱敏） ---------- */
  function applyDesktopOptions(o) {
    if (!o) return;
    const range = document.querySelector("input[name=range][value=" + (o.range === "custom" ? "custom" : "default") + "]");
    if (range) { range.checked = true; syncRangeUI(); }
    const mode = document.querySelector("input[name=mode][value=" + (o.mode || "mask") + "]");
    if (mode) mode.checked = true;
    const full = $("#opt-full");
    if (full) full.checked = !!o.fullName;
    const map = { person: "opt-person", org: "opt-org", address: "opt-address", id: "opt-id", phone: "opt-phone", email: "opt-email" };
    Object.keys(map).forEach(function (k) {
      const el = document.getElementById(map[k]);
      if (el) el.checked = !!o[k];
    });
  }

  async function handleDesktopRun(msg) {
    const dbg = function (stage) { try { window.LR_DESKTOP.postError(""); } catch (e) {} };
    try {
      window.LR_DESKTOP.dbg("start");
      applyDesktopOptions(msg.options);
      const bin = atob(msg.data);
      const u8 = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
      window.LR_DESKTOP.dbg("file-ok " + msg.name + " " + u8.length);
      const before = state.results.length;
      await addFiles([new File([u8], msg.name)]);
      window.LR_DESKTOP.dbg("processed results=" + state.results.length);
      const res = state.results[before];
      if (!res || !res.outFiles || !res.outFiles.length) {
        throw new Error(lastProcessError || "没有生成任何文件（格式可能不支持）");
      }
      const hitCount = res.redactor ? res.redactor.rows.length : 0;
      const files = [];
      for (const f of res.outFiles) {
        const buf = await f.blob.arrayBuffer();
        files.push({ name: f.name, data: window.LR_DESKTOP.toBase64(new Uint8Array(buf)) });
      }
      if (hitCount === 0) {
        // 自动筛选：没检出敏感信息就不产出文件
        window.LR_DESKTOP.postResult([], { name: res.file.name, count: 0 }, "skipped");
        return;
      }
      window.LR_DESKTOP.postResult(files, {
        seq: nextSeq(),
        name: res.file.name,
        mode: res.redactor.mode,
        count: hitCount,
        warn: (res.redactor.issues || []).filter((i) => i.kind === "leftover").length,
      }, "ok");
    } catch (e) {
      window.LR_DESKTOP.postError((e && e.message) || e);
    }
  }

  // 桌面版：接管宿主推过来的文件
  if (window.LR_DESKTOP && window.LR_DESKTOP.available) {
    window.LR_DESKTOP.onFile(function (msg) {
      try {
        const bin = atob(msg.data);
        const u8 = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
        addFiles([new File([u8], msg.name)]);
      } catch (e) {
        console.warn("桌面文件接收失败", e);
      }
    });
    if (location.search.indexOf("desk=1") >= 0) {
      window.LR_DESKTOP.onRun(handleDesktopRun);
      window.LR_DESKTOP.ready();   // 告诉宿主：挂好了，可以发文件
    } else {
      window.LR_DESKTOP.ready();
    }
  }
  $("#clear").addEventListener("click", () => {
    state.results = [];
    list.innerHTML = "";
    list.appendChild(empty);
    render();
  });


  /* ---------- 任务记录 / 文件夹 ---------- */
  const CATS = ["未分类", "民事", "刑事", "行政", "合同纠纷", "劳动争议", "其他"];

  function nextSeq() {
    const n = Number(localStorage.getItem("lr-seq") || "10000") + 1;
    localStorage.setItem("lr-seq", String(n));
    return n;
  }

  function isDesktop() {
    return !!(window.LR_DESKTOP && window.LR_DESKTOP.available);
  }

  function b64ToBlob(b64, type) {
    const bin = atob(b64);
    const u8 = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
    return new Blob([u8], { type: type || "application/octet-stream" });
  }

  /** 统一存储：桌面版落盘（文档\法律脱敏库），网页版用 IndexedDB */
  const Store = {
    async list() {
      if (isDesktop()) return await window.LR_DESKTOP.libList();
      const rows = await window.LR_STORE.all();
      return rows.map((r) => Object.assign({}, r, {
        files: (r.files || []).map((f) => f.name),
      }));
    },
    async add(record, files) {
      if (isDesktop()) return await window.LR_DESKTOP.libSave(record, files);
      return await window.LR_STORE.add(Object.assign({}, record, { files: files }));
    },
    async remove(id) {
      if (isDesktop()) return await window.LR_DESKTOP.libDelete(id);
      return await window.LR_STORE.remove(Number(id));
    },
    async category(id, cat) {
      if (isDesktop()) return await window.LR_DESKTOP.libCategory(id, cat);
      return await window.LR_STORE.patch(Number(id), { category: cat });
    },
    async file(rec, name) {
      if (isDesktop()) {
        const r = await window.LR_DESKTOP.libFile(rec.id, name);
        return b64ToBlob(r.data);
      }
      const all = await window.LR_STORE.all();
      const full = all.find((x) => String(x.id) === String(rec.id));
      const f = full && full.files.find((y) => y.name === name);
      return f ? f.blob : null;
    },
  };

  async function saveRecord(res) {
    if (isDesktop()) return;   // 桌面版：宿主保存完直接建库，不用通过消息桥搬文件
    try {
      const files = [];
      for (const f of res.outFiles) files.push({ name: f.name, blob: f.blob });
      await Store.add({
        seq: nextSeq(),
        name: res.file.name,
        ext: res.ext,
        mode: res.redactor.mode,
        count: res.redactor.rows.length,
        category: "未分类",
        createdAt: Date.now(),
      }, files);
      updateLibCount();
    } catch (e) {
      console.warn("记录保存失败", e);
    }
  }

  async function updateLibCount() {
    try {
      const all = await window.LR_STORE.all();
      const el = $("#lib-count");
      if (el) el.textContent = all.length ? `(${all.length})` : "";
    } catch (e) { /* ignore */ }
  }

  function fmtTime(ts) {
    const d = new Date(ts);
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  }

  async function renderLibrary() {
    const box = $("#lib-list");
    const kw = ($("#lib-search").value || "").trim().toLowerCase();
    const cat = $("#lib-cat").value;
    let all = [];
    try { all = await Store.list(); } catch (e) { all = []; }

    // 分类下拉
    const sel = $("#lib-cat");
    const cats = [...new Set([...CATS, ...all.map((r) => r.category || "未分类")])];
    sel.innerHTML = `<option value="">全部分类</option>` +
      cats.map((c) => `<option value="${c}">${c}</option>`).join("");
    sel.value = cat;

    const rows = all
      .filter((r) => (!cat || (r.category || "未分类") === cat))
      .filter((r) => !kw || r.name.toLowerCase().includes(kw) || String(r.seq).includes(kw))
      .sort((a, b) => b.createdAt - a.createdAt);

    if (!rows.length) {
      box.innerHTML = `<div class="empty">${all.length ? "没有匹配的记录" : "文件夹还是空的。脱敏完的文件会自动存进来。"}</div>`;
      return;
    }

    box.innerHTML = `<div class="card">` + rows.map((r) => `
      <div class="rec" data-id="${r.id}">
        <span class="seq">#${r.seq}</span>
        <span class="nm">${esc(r.name)}</span>
        <span class="stat">${r.mode} · ${r.count} 处</span>
        <span class="stat">${fmtTime(r.createdAt)}</span>
        <select data-act="cat">${cats.map((c) =>
          `<option ${((r.category || "未分类") === c) ? "selected" : ""}>${c}</option>`).join("")}</select>
        <button class="btn small" data-act="dl">下载</button>
        <button class="btn small" data-act="del">删除</button>
      </div>`).join("") + `</div>`;
  }

  async function downloadAllRecords() {
    let all = [];
    try { all = await Store.list(); } catch (e) { return; }
    if (!all.length) { alert("文件夹是空的"); return; }
    const entries = [];
    const seen = new Set();
    const uniq = (n) => { let x = n, i = 2; while (seen.has(x)) { x = n.replace(/(\.[^.]+)$/, `-${i}$1`); i++; } seen.add(x); return x; };
    for (const r of all) {
      const dir = `#${r.seq}_${r.category || "未分类"}/`;
      for (const fname of r.files) {
        const blob = await Store.file(r, fname);
        if (blob) entries.push([uniq(dir + fname), new Uint8Array(await blob.arrayBuffer())]);
      }
    }
    const bytes = await window.LR_ZIP.writeZip(entries);
    download("文件夹导出.zip", new Blob([bytes], { type: "application/zip" }));
  }

  async function handleLibClick(e) {
    const btn = e.target.closest("[data-act]");
    const row = e.target.closest(".rec");
    if (!row) return;
    const id = row.dataset.id;   // 桌面版是字符串 id，不能转数字
    const act = (btn && btn.dataset.act) || (e.target.tagName === "SELECT" ? "cat" : null);

    if (act === "cat") {
      await Store.category(id, row.querySelector("select").value);
      return;
    }
    if (act === "del") {
      if (!confirm("从文件夹删除这条记录？（不会动原文件）")) return;
      await Store.remove(id);
      renderLibrary(); updateLibCount();
      return;
    }
    if (act === "dl") {
      const all = await Store.list();
      const rec = all.find((r) => String(r.id) === String(id));
      if (!rec) return;
      for (const fname of rec.files) {
        const blob = await Store.file(rec, fname);
        if (blob) download(fname, blob);
      }
    }
  }

  /* ---------- 一键还原 ---------- */
  function restoreTip(msg) { $("#rs-tip").textContent = msg || ""; }

  async function doRestore() {
    const fileInput = $("#rs-file");
    const mapInput = $("#rs-map");
    if (!fileInput.files.length || !mapInput.files.length) {
      restoreTip("两个文件都要选：脱敏件 + 对应的映射表.json");
      return;
    }
    try {
      const map = JSON.parse(await mapInput.files[0].text());
      const pairs = (map.entities || []).map((e) => [e.replacement, e.original]);
      if (!pairs.length) { restoreTip("映射表里没有实体，无法还原"); return; }

      const f = fileInput.files[0];
      const buf = new Uint8Array(await f.arrayBuffer());
      const base = f.name.replace(/\.[^.]+$/, "").replace(/\.(脱敏|还原)$/, "");

      if (/\.pdf$/i.test(f.name)) {
        restoreTip("PDF 涂黑版是把文字删掉重排成图，按设计不可还原。要还原请用 docx 版。");
        return;
      }
      if (/\.docx$/i.test(f.name)) {
        const out = await window.LR_DOCX.restoreDocx(buf, pairs);
        download(`${base}.还原.docx`, new Blob([out], {
          type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document" }));
        restoreTip("已还原，检查下载文件。");
        return;
      }
      let text = new TextDecoder().decode(buf);
      for (const [rep, orig] of pairs.slice().sort((a, b) => b[0].length - a[0].length)) {
        text = text.split(rep).join(orig);
      }
      download(`${base}.还原.txt`, textBlob(text));
      restoreTip("已还原，检查下载文件。");
    } catch (e) {
      restoreTip("还原失败：" + (e && e.message || e));
    }
  }

  /* ---------- 标签切换 ---------- */
  function switchTab(name) {
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
    $("#tab-work").style.display = name === "work" ? "" : "none";
    $("#tab-lib").style.display = name === "lib" ? "" : "none";
    if (name === "lib") renderLibrary();
  }

  // 便于自动化测试
  window.LR_APP = { addFiles, state, ready: true };

  // 就绪后收起提示条（Safari 解析慢时不会误报）
  var warnBox = document.getElementById("bootwarn");
  if (warnBox) warnBox.style.display = "none";
})();

# -*- coding: utf-8 -*-
"""把 src 下的文件内联成单个 index.html（双击即用，不依赖网络）。"""
import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
OUT = ROOT / "index.html"

# ocrweb.js 有 10MB（tesseract + 语言包），不内联，单独出 ocr.js，用到图片时才加载
LAZY = ["ocrweb.js"]
ORDER = ["rules.js", "types.js", "extras.js", "detect.js", "engine.js", "zip.js",
         "store.js", "docxwrite.js", "docx.js", "pdfread.js", "pdfredact.js", "xlsxwrite.js", "xlsx.js", "desktop.js", "imgredact.js", "app.js"]

TEMPLATE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<!-- 保密加固：禁止一切网络请求（fetch/XHR/外链资源），页面只能在本机算 -->
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline' 'self' blob: 'wasm-unsafe-eval'; worker-src blob:; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; connect-src 'none'; base-uri 'none'; form-action 'none'">
<title>法律文书脱敏</title>
<style>
__CSS__
</style>
</head>
<body>
<div id="bootwarn" style="display:none;margin:0;padding:14px 18px;background:#fdecea;color:#8a1c14;font-size:14px;line-height:1.8;border-bottom:1px solid #f5c2c0">
  <b id="bootwarn-title">页面没能启动</b><br>
  <span id="bootwarn-detail">请按 Ctrl+F5 强制刷新；仍不行的话，把这段提示截图发我。</span>
  <div style="margin-top:8px;font-size:12.5px;color:#b4554a">
    需要 Chrome / Edge 103+ 或 Safari 16.4+；360 / QQ 浏览器请切「极速模式」。
  </div>
</div>
<div class="wrap">

  <header>
    <div class="mark">脱</div>
    <h1>法律文书脱敏</h1>
    <p class="sub">当事人姓名、单位名、住址、身份证、电话换成替身 · 全部在本机浏览器内完成，文件不上传
      <span style="color:#9aa1ad;font-size:12px">· __VERSION__</span></p>
  </header>

  <div class="tabs">
    <button class="tab active" data-tab="work">脱敏</button>
    <button class="tab" data-tab="lib">文件夹 <span id="lib-count"></span></button>
  </div>

  <section id="tab-work">
  <label class="dropzone" id="drop" for="file">
    <div class="big">把文书拖进来，或 <span class="browse">点这里选择</span></div>
    <div class="hint">支持 .docx / .txt / .md / .html / .pdf；docx 保留格式，PDF 出涂黑版 + 可读版</div>
    <div class="hint" style="margin-top:8px">
      <b>扫描件 / 拍照</b>：直接把 <b>图片</b>（png / jpg）拖进来，会在图上涂黑，文件不出本机。<br>
      PDF 扫描件：用「预览」(Mac) 或「图片PDF转文字.bat」(Windows) 先转成图片或文字。
    </div>
  </label>
  <input type="file" id="file" class="file-input" multiple accept=".docx,.txt,.md,.html,.pdf,.png,.jpg,.jpeg,application/pdf,text/plain,application/vnd.openxmlformats-officedocument.wordprocessingml.document,image/*">

  <div class="toolbar" style="margin-top:14px">
    <button class="btn" id="folder-open">选文件夹</button>
    <input type="file" id="folder-input" class="file-input" webkitdirectory multiple>
    <button class="btn" id="paste-open">粘贴文字</button>
    <span style="font-size:12.5px;color:var(--muted)">从 PDF/图片转换出来的文字，可以直接粘在这里</span>
  </div>

  <div id="paste-panel" style="display:none;margin-top:12px">
    <textarea id="paste-text" placeholder="把文字粘贴到这里（Ctrl+V）……"
      style="width:100%;min-height:150px;padding:12px;border:1px solid var(--line);border-radius:12px;font:inherit;resize:vertical"></textarea>
    <div style="display:flex;gap:8px;margin-top:8px">
      <button class="btn primary" id="paste-run">脱敏这段文字</button>
      <button class="btn" id="paste-close">收起</button>
    </div>
  </div>

  <div class="options" id="modes">
    <span style="font-size:13px;color:var(--muted);align-self:center;margin-right:4px">脱敏模式</span>
    <label class="chip"><input type="radio" name="mode" value="mask" checked> 掩码</label>
    <label class="chip"><input type="radio" name="mode" value="role"> 角色化</label>
    <label class="chip"><input type="radio" name="mode" value="redact"> 涂黑</label>
    <label class="chip"><input type="checkbox" id="opt-full"> 姓名全掩码</label>
    <label class="chip"><input type="checkbox" id="opt-docx"> PDF 也输出 Word/TXT</label>
  </div>

  <div class="options" id="range">
    <span style="font-size:13px;color:var(--muted);align-self:center;margin-right:4px">脱敏范围</span>
    <label class="chip"><input type="radio" name="range" value="default" checked> 默认（全部脱敏）</label>
    <label class="chip"><input type="radio" name="range" value="custom"> 自定义</label>
  </div>

  <div class="options" id="options">
    <label class="chip"><input type="checkbox" id="opt-person" checked> 人名</label>
    <label class="chip"><input type="checkbox" id="opt-org" checked> 单位名</label>
    <label class="chip"><input type="checkbox" id="opt-address" checked> 地址</label>
    <label class="chip"><input type="checkbox" id="opt-id" checked> 身份证</label>
    <label class="chip"><input type="checkbox" id="opt-phone" checked> 电话</label>
    <label class="chip"><input type="checkbox" id="opt-email" checked> 邮箱</label>
  </div>

  <div class="toolbar">
    <span class="grow" id="summary"></span>
    <button class="btn" id="clear">清空</button>
    <button class="btn primary" id="downloadAll" disabled>全部下载</button>
  </div>

  <div class="toolbar" style="margin-top:10px">
    <button class="btn" id="restore-open">一键还原</button>
    <span style="font-size:12.5px;color:var(--muted)">把「脱敏件 + 映射表」放回来，还原成原文</span>
  </div>

  <div id="restore-panel" style="display:none;margin-top:12px">
    <div class="options" style="gap:14px">
      <label class="chip" style="border-style:dashed">① 脱敏件 <input type="file" id="rs-file" style="display:none"></label>
      <label class="chip" style="border-style:dashed">② 映射表.json <input type="file" id="rs-map" accept=".json" style="display:none"></label>
      <button class="btn primary" id="rs-run">还原</button>
      <button class="btn" id="rs-close">收起</button>
    </div>
    <div id="rs-tip" style="font-size:12.5px;color:var(--muted);margin-top:8px"></div>
  </div>

  <div id="list"><div class="empty" id="empty">还没有文件。丢一份裁判文书进来试试。</div></div>
  </section>

  <section id="tab-lib" style="display:none">
    <div class="toolbar" style="margin-top:16px">
      <input id="lib-search" placeholder="搜索文件名 / 流水号" style="flex:1;padding:8px 12px;border:1px solid var(--line);border-radius:10px;font:inherit">
      <select id="lib-cat" style="padding:8px 12px;border:1px solid var(--line);border-radius:10px;font:inherit">
        <option value="">全部分类</option>
      </select>
      <button class="btn" id="lib-refresh">刷新</button>
      <button class="btn primary" id="lib-export">全部导出</button>
      <button class="btn" id="lib-import-btn">导入本地库</button>
      <input type="file" id="lib-import" class="file-input" webkitdirectory multiple>
    </div>
    <div id="lib-list"><div class="empty">文件夹还是空的。脱敏完的文件会自动存进来。</div></div>
  </section>

  <details class="foot">
    <summary style="cursor:pointer;font-weight:600;color:#475467">脱敏口径与使用说明</summary>
    <p><b>脱掉的：</b>当事人姓名（原告/被告/被告人/被害人/上诉人/申请人/法定代表人/经营者/证人…）、
    单位与公司名、住址与经营场所、身份证号、手机与固定电话、邮箱。<br>
    同一份文书里同一个人/同一家公司全文用同一替身；不同实体自动加序号（某某某公司、某某某公司二）。<br>
    <b>保留的：</b>法院、检察院、公安局等机关；案号；法官、书记员、人民陪审员、律师；
    事实经过、证据、法条、判决结果。</p>
    <p><b>输出的四个文件：</b>脱敏件（可继续编辑）、对照.txt（逐段原文↔脱敏）、映射表.json（还原用，
    内含真实信息，别外传）、明细.csv（每处替换的清单）。</p>
    <p><b>合格标准：</b>卡片上显示「✓ 二次扫描无残留」。有「待确认」的，通常是按规则保留的法官/律师，
    过一眼即可。</p>
    <p><b>注意：</b>需要 Chrome / Edge 103+ 或 Safari 16.4+（用到浏览器自带的解压能力）。
    PDF 暂不支持，请先另存为 docx。还原请用命令行版 <code>restore.py</code>，映射表通用。</p>
  </details>

</div>


<script>
/* 启动看门狗：主程序没跑起来就把原因说出来，别让页面白屏 */
setTimeout(function () {
  if (!window.LR_APP) {
    var d = document.getElementById("bootwarn");
    if (d) d.style.display = "block";
  }
}, 1500);
</script>
<script>
/* 早期错误捕获：任何脚本出错都在顶部显示出来，不再静默 */
(function () {
  function show(msg) {
    var box = document.getElementById("bootwarn");
    if (!box) return;
    box.style.display = "block";
    var t = document.getElementById("bootwarn-title");
    var d = document.getElementById("bootwarn-detail");
    if (t && msg && msg.indexOf("browser") >= 0) t.textContent = "浏览器版本太旧";
    if (d && msg) d.textContent = msg;
    window.__lastError = msg || "";
  }
  window.__lrFail = show;

  // 微信/QQ 内置浏览器：提示用系统浏览器打开
  var ua = navigator.userAgent || "";
  if (/MicroMessenger|QQ\//i.test(ua)) {
    show("检测到你在微信/QQ 里打开。请点右上角「…」→「在浏览器中打开」，或复制链接到 Chrome/Edge 里打开。");
  }
  // ?diag=1 诊断页：纯 ES5，任何浏览器都能跑
  if (/[?&]diag/.test(location.search)) {
    var diagMode = true;
    window.__diag = true;
    document.addEventListener("DOMContentLoaded", function () {
      var rows = [];
      function has(name, fn) {
        var ok = false;
        try { ok = !!fn(); } catch (e) { ok = false; }
        rows.push((ok ? "✓ " : "✗ ") + name);
      }
      rows.push("地址：" + location.href);
      rows.push("UA字符串：" + navigator.userAgent);
      var m = /(Chrome|Edg|Firefox|Version|Safari)\/([\d.]+)/.exec(navigator.userAgent);
      rows.push("识别：" + (m ? m[1] + " " + m[2] : "未知"));
      rows.push("屏幕：" + screen.width + "x" + screen.height + " | 像素比：" + (window.devicePixelRatio || 1));
      rows.push("—— 能力检测 ——");
      has("DecompressionStream（解压 docx/PDF 需要）", function () { return window.DecompressionStream; });
      has("CompressionStream", function () { return window.CompressionStream; });
      has("Promise", function () { return window.Promise; });
      has("Symbol", function () { return window.Symbol; });
      has("Object.entries", function () { return Object.entries; });
      has("Array.from", function () { return Array.from; });
      has("String.padStart", function () { return "".padStart; });
      has("Array.includes", function () { return [].includes; });
      has("Element.closest", function () { return Element.prototype.closest; });
      has("canvas.toBlob", function () { return HTMLCanvasElement.prototype.toBlob; });
      has("URL.createObjectURL", function () { return URL.createObjectURL; });
      has("IndexedDB（文件夹需要）", function () { return window.indexedDB; });
      has("Blob", function () { return window.Blob; });
      has("pdf.js 已加载", function () { return window.pdfjsLib; });
      has("主程序已就绪", function () { return window.LR_APP && window.LR_APP.ready; });

      var box = document.createElement("pre");
      box.style.cssText = "position:fixed;inset:0;z-index:99999;margin:0;padding:20px;background:#fff;" +
        "color:#111;font:13px/1.7 Menlo,Consolas,monospace;white-space:pre-wrap;overflow:auto";
      var NL = String.fromCharCode(10);
      box.textContent = ["法律文书脱敏 · 兼容性诊断", ""].concat(rows)
        .concat(["", "把这个截图发我即可。"]).join(NL);
      document.body.appendChild(box);
    });
  }

  // 启动看门狗：3 秒还没就绪，把浏览器信息亮出来（方便截图排查）
  setTimeout(function () {
    if (window.__diag) return;
    if (window.LR_APP && window.LR_APP.ready) return;
    var loaded = (window.__boot || []).join(" → ");
    var all = ["rules.js","types.js","extras.js","detect.js","engine.js","zip.js","store.js",
               "docxwrite.js","docx.js","pdfread.js","pdfredact.js","app.js"];
    var missing = all.filter(function (x) { return (window.__boot || []).indexOf(x) < 0; });
    show("页面初始化未完成。浏览器：" + ua.slice(0, 90) +
         "｜已加载：" + (loaded || "无") +
         "｜未加载：" + (missing.length ? missing.join(",") : "（都加载了）"));
  }, 8000);
  window.addEventListener("error", function (e) {
    var msg = (e && e.message) ? e.message : "脚本出错";
    var src = e && e.filename ? ("（" + String(e.filename).split("/").pop() + "）") : "";
    show(msg + src);
  }, true);
  window.addEventListener("unhandledrejection", function (e) {
    var r = e && e.reason;
    show("异步错误：" + (r && r.message ? r.message : String(r)));
  });
})();
</script>

<script>
__PDFJS__
</script>
<script>
__WORKER__
</script>











<script>
/* OCR（tesseract，约 10MB）不随页面加载，第一次处理图片时才拉过来 */
window.LR_OCR_LOAD = function () {
  if (window.LR_OCR) return Promise.resolve(window.LR_OCR);
  if (window.__lrOcrLoading) return window.__lrOcrLoading;
  window.__lrOcrLoading = new Promise(function (resolve, reject) {
    var s = document.createElement("script");
    s.src = "ocr.js";
    s.onload = function () {
      if (window.LR_OCR) resolve(window.LR_OCR);
      else reject(new Error("ocr.js 加载了但没注册 LR_OCR"));
    };
    s.onerror = function () {
      window.__lrOcrLoading = null;
      reject(new Error("OCR 组件加载失败（ocr.js 找不到或被网络拦了）"));
    };
    document.head.appendChild(s);
  });
  return window.__lrOcrLoading;
};
</script>
__JS__
</body>
</html>
"""


def main():
    css = (SRC / "styles.css").read_text(encoding="utf-8")

    parts = []
    for f in ORDER:
        code = (SRC / f).read_text(encoding="utf-8")
        parts.append(
            "<script>window.__boot=(window.__boot||[]);window.__boot.push(" + repr(f) + ");\n"
            + code + "\n</script>"
        )
    js = "\n".join(parts)

    pdfjs = (SRC / "pdf.min.js").read_text(encoding="utf-8")
    worker = (SRC / "pdfjs.worker.b64.js").read_text(encoding="utf-8")
    ver = "v0.3.5 · " + datetime.datetime.now().strftime("%m-%d %H:%M")

    html = (TEMPLATE.replace("__VERSION__", ver)
            .replace("__CSS__", css)
            .replace("__PDFJS__", pdfjs)
            .replace("__WORKER__", worker)
            .replace("__JS__", js))
    OUT.write_text(html, encoding="utf-8")
    print(f"生成 {OUT}（{OUT.stat().st_size / 1024:.0f} KB）")

    # 懒加载包：OCR（tesseract）单独出文件，用到图片时才拉
    for f in LAZY:
        code = (SRC / f).read_text(encoding="utf-8")
        target = ROOT / f.replace("ocrweb", "ocr")
        target.write_text(code, encoding="utf-8")
        print(f"生成 {target}（{target.stat().st_size / 1024 / 1024:.2f} MB，按需加载）")


if __name__ == "__main__":
    main()

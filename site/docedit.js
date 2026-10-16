/* 文档编辑器（docedit.html）：打开 / 导入 .md、.html，源码编辑 + 实时预览，导出（.md / .html / Word / PDF）。纯前端，不经过后端。
   依赖：icons.js、marked + DOMPurify（Markdown 预览与导出）。
   安全：
   - Markdown：marked → DOMPurify 净化，渲染进 sandbox="allow-same-origin"（无 allow-scripts）的 iframe，
     帧内脚本和事件处理器都不会执行；同源只是为了直接改 body、保留滚动位置。
   - HTML：原样写进 sandbox="allow-scripts"（无 allow-same-origin）的 iframe srcdoc，脚本能跑但是不透明源，
     拿不到本站 cookie / localStorage / API。两个 sandbox 属性都不要随手放宽。
   草稿存 localStorage（仅本机），读写都包 try/catch。 */
(() => {
  'use strict';

  const MAX_BYTES = 2 * 1024 * 1024;           // 打开文件上限，与帖子导入 Markdown 一致
  const DRAFT_KEY = 'cl-docedit-draft';
  const VIEW_KEY = 'cl-docedit-view';
  const DEFAULT_NAME = '未命名';

  const STARTER = {
    md: '# 未命名文档\n\n左边写 Markdown，右边实时预览。\n\n' +
        '- 点「打开文件」，或者直接把 `.md` / `.html` 文件拖进来\n' +
        '- 编辑内容会自动保存为本机草稿\n' +
        '- 「导出」下载 `.md`，也可以导出成独立的 HTML 页面\n',
    html: '<!DOCTYPE html>\n<html lang="zh-CN">\n<head>\n<meta charset="UTF-8">\n' +
          '<meta name="viewport" content="width=device-width, initial-scale=1.0">\n<title>未命名页面</title>\n' +
          '<style>\n  body { font-family: system-ui, sans-serif; max-width: 720px; margin: 48px auto; padding: 0 20px; line-height: 1.7; }\n</style>\n' +
          '</head>\n<body>\n  <h1>未命名页面</h1>\n  <p>左边改 HTML 源码，右边实时预览。</p>\n</body>\n</html>\n',
  };

  const $ = id => document.getElementById(id);
  const src = $('src'), nameInput = $('docName'), panes = $('panes');
  const pvMd = $('pvMd'), pvHtml = $('pvHtml');
  const btnExport = $('btnExport'), btnExportAlt = $('btnExportAlt');
  const narrow = window.matchMedia('(max-width: 860px)');

  const state = { fmt: 'md', view: 'split', dirty: false };

  let tt;
  function toast(msg, err) {
    const t = $('toast');
    t.textContent = msg; t.classList.toggle('err', !!err); t.classList.add('show');
    clearTimeout(tt); tt = setTimeout(() => t.classList.remove('show'), 3000);
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  // ---------- Markdown → HTML ----------
  // 预览和「导出 HTML」共用同一份样式，导出结果就是预览里看到的样子
  const MD_CSS = `
*{box-sizing:border-box}
body{margin:0;padding:32px 40px 64px;background:#fff;color:#1f1f23;font:15px/1.75 -apple-system,BlinkMacSystemFont,"PingFang SC","Hiragino Sans GB","Noto Sans SC","Microsoft YaHei",sans-serif;word-wrap:break-word}
main{max-width:760px;margin:0 auto}
h1,h2,h3,h4,h5,h6{line-height:1.3;margin:1.6em 0 .6em;font-weight:600;letter-spacing:-.01em}
h1{font-size:1.9em;margin-top:.4em}h2{font-size:1.45em;padding-bottom:.3em;border-bottom:1px solid #ececea}h3{font-size:1.2em}
p,ul,ol,blockquote,pre,table{margin:0 0 1em}
ul,ol{padding-left:1.6em}li+li{margin-top:.25em}
a{color:#2b59ff;text-decoration:none}a:hover{text-decoration:underline}
blockquote{margin-left:0;padding:.2em 1em;border-left:3px solid #d6d6d1;color:#52525b}
code{font:.88em/1.6 "JetBrains Mono","SF Mono",Menlo,Consolas,monospace;background:#f4f4f2;border-radius:4px;padding:.15em .35em}
pre{background:#f4f4f2;border-radius:8px;padding:14px 16px;overflow:auto}
pre code{background:none;padding:0;font-size:.86em}
img{max-width:100%;height:auto;border-radius:6px}
hr{border:none;border-top:1px solid #e7e7e4;margin:2em 0}
table{border-collapse:collapse;display:block;overflow:auto}
th,td{border:1px solid #e7e7e4;padding:6px 12px;text-align:left}th{background:#fafaf9;font-weight:600}
@media(max-width:600px){body{padding:20px 16px 48px}}
@page{margin:18mm 16mm}
@media print{body{padding:0}pre,pre code{white-space:pre-wrap;word-break:break-word}h1,h2,h3,h4{break-after:avoid}pre,table,img,blockquote{break-inside:avoid}}`;

  // Word 只认一部分 CSS（不支持 flex / 变量 / max-width 等），单独给一份保守的样式
  const WORD_CSS = `
@page WordSection1{size:595.3pt 841.9pt;margin:72pt 64pt}
div.WordSection1{page:WordSection1}
body{font-family:"Microsoft YaHei","PingFang SC",sans-serif;font-size:11pt;line-height:1.6;color:#1f1f23}
h1{font-size:20pt}h2{font-size:16pt}h3{font-size:13pt}h4,h5,h6{font-size:11pt}
a{color:#2b59ff}
code{font-family:Consolas,"Courier New",monospace;font-size:10pt;background:#f4f4f2}
pre{font-family:Consolas,"Courier New",monospace;font-size:9.5pt;background:#f4f4f2;padding:8pt;white-space:pre-wrap}
blockquote{margin-left:0;padding-left:8pt;border-left:3pt solid #d6d6d1;color:#52525b}
table{border-collapse:collapse}th,td{border:1px solid #bfbfbf;padding:4pt 8pt}th{background:#f2f2f2}`;

  // 返回已净化的 HTML 片段；没有 DOMPurify 时退回转义纯文本，绝不输出未净化内容
  function mdToSafeHtml(md) {
    if (!window.DOMPurify || !window.marked) return '<pre>' + escapeHtml(md) + '</pre>';
    try { return window.DOMPurify.sanitize(window.marked.parse(md || '')); }
    catch { return '<pre>' + escapeHtml(md) + '</pre>'; }
  }

  function mdDocument(title, bodyHtml) {
    return '<!DOCTYPE html>\n<html lang="zh-CN">\n<head>\n<meta charset="UTF-8">\n' +
      '<meta name="viewport" content="width=device-width, initial-scale=1.0">\n' +
      '<title>' + escapeHtml(title) + '</title>\n<style>' + MD_CSS + '\n</style>\n</head>\n<body>\n<main>\n' +
      bodyHtml + '\n</main>\n</body>\n</html>\n';
  }

  // ---------- 预览 ----------
  let mdReady = false;
  pvMd.addEventListener('load', () => {
    if (!pvMd.contentDocument || !pvMd.contentDocument.querySelector('main')) return;   // 跳过初始的 about:blank
    mdReady = true;
    if (state.fmt === 'md' && state.view !== 'code') renderMd();
  });
  pvMd.srcdoc = mdDocument('预览', '');

  function renderMd() {
    if (!mdReady) return;
    const main = pvMd.contentDocument && pvMd.contentDocument.querySelector('main');
    if (main) main.innerHTML = mdToSafeHtml(src.value);   // 已净化，且该帧不执行脚本
  }

  let lastHtml = null;
  function renderHtml() {
    if (src.value === lastHtml) return;
    lastHtml = src.value;
    pvHtml.srcdoc = src.value;     // 每次整页重载：页面里的脚本会重新执行
  }

  let pvTimer;
  function schedulePreview() {
    clearTimeout(pvTimer);
    if (state.view === 'code') return;    // 预览不可见时不渲染，切回来再补
    // HTML 每次重载整页，间隔放长一点，避免边打字边反复执行页面脚本
    pvTimer = setTimeout(() => (state.fmt === 'md' ? renderMd() : renderHtml()), state.fmt === 'md' ? 150 : 600);
  }

  function previewNow() {
    clearTimeout(pvTimer);
    if (state.fmt === 'md') renderMd(); else renderHtml();
  }

  // ---------- 格式 / 视图 ----------
  function setFmt(fmt) {
    state.fmt = fmt === 'html' ? 'html' : 'md';
    document.querySelectorAll('#fmtSeg button').forEach(b => b.classList.toggle('on', b.dataset.fmt === state.fmt));
    pvMd.hidden = state.fmt !== 'md';
    pvHtml.hidden = state.fmt !== 'html';
    btnExport.lastChild.textContent = state.fmt === 'md' ? '导出 .md' : '导出 .html';
    btnExportAlt.hidden = state.fmt !== 'md';
    $('stFmt').textContent = state.fmt === 'md' ? 'Markdown' : 'HTML';
    lastHtml = null;
    if (state.view !== 'code') previewNow();
  }

  function setView(view) {
    if (!['code', 'split', 'preview'].includes(view)) view = 'split';
    if (view === 'split' && narrow.matches) view = 'code';     // 窄屏没有分栏
    state.view = view;
    panes.dataset.view = view;
    document.querySelectorAll('#viewSeg button').forEach(b => b.classList.toggle('on', b.dataset.view === view));
    if (view !== 'code') previewNow();
  }

  // ---------- 文件名 ----------
  function baseName() {
    const n = nameInput.value.trim().replace(/\.(md|markdown|html?|txt)$/i, '').replace(/[\\/:*?"<>|\u0000-\u001f]/g, '_');
    return n || DEFAULT_NAME;
  }

  // ---------- 草稿 ----------
  let saveTimer;
  function scheduleSave() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(saveDraft, 600);
  }
  function saveDraft() {
    const st = $('stSave');
    try {
      localStorage.setItem(DRAFT_KEY, JSON.stringify({
        fmt: state.fmt, name: nameInput.value, src: src.value, dirty: state.dirty, ts: Date.now(),
      }));
      const d = new Date();
      st.textContent = '草稿已保存 ' + String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
    } catch {
      st.textContent = '草稿未保存（本机存储不可用或内容过大）';
    }
  }
  function loadDraft() {
    try {
      const d = JSON.parse(localStorage.getItem(DRAFT_KEY) || 'null');
      if (d && typeof d.src === 'string') return d;
    } catch { /* 隐私模式 / 数据损坏：当作没有草稿 */ }
    return null;
  }

  // ---------- 内容变化 ----------
  function updateCount() {
    const v = src.value;
    const lines = v ? v.split('\n').length : 0;
    $('stCount').textContent = v.length.toLocaleString() + ' 字符 · ' + lines.toLocaleString() + ' 行';
  }

  function onEdit() {
    state.dirty = true;
    updateCount();
    schedulePreview();
    scheduleSave();
  }

  // 整体替换文档（新建 / 打开）
  function loadDoc({ fmt, name, text }) {
    src.value = text;
    nameInput.value = name;
    state.dirty = false;
    setFmt(fmt);
    updateCount();
    src.scrollTop = 0;
    src.setSelectionRange(0, 0);
    saveDraft();
  }

  function confirmDiscard() {
    return !state.dirty || !src.value.trim() || confirm('当前内容还没有导出，继续会覆盖它（本机草稿也会被替换）。确定继续？');
  }

  // ---------- 新建 / 打开 ----------
  $('btnNew').addEventListener('click', () => {
    if (!confirmDiscard()) return;
    loadDoc({ fmt: state.fmt, name: DEFAULT_NAME, text: STARTER[state.fmt] });
    src.focus();
  });

  const fileInput = $('fileInput');
  $('btnOpen').addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', () => {
    const f = fileInput.files && fileInput.files[0];
    fileInput.value = '';
    if (f) openFile(f);
  });

  async function openFile(file) {
    const m = /\.([a-z0-9]+)$/i.exec(file.name);
    const ext = m ? m[1].toLowerCase() : '';
    if (!['md', 'markdown', 'html', 'htm', 'txt'].includes(ext)) { toast('只支持 .md / .html / .txt 文件', true); return; }
    if (file.size > MAX_BYTES) { toast('文件过大（上限 2MB）', true); return; }
    if (!confirmDiscard()) return;
    let text;
    try { text = await file.text(); }
    catch { toast('读取文件失败', true); return; }
    loadDoc({ fmt: ext === 'html' || ext === 'htm' ? 'html' : 'md', name: file.name.replace(/\.[^.]+$/, ''), text });
    toast('已打开 ' + file.name);
  }

  // 拖拽打开：只响应带文件的拖拽，避免拖动选中文字时也弹遮罩
  const de = $('de'), drop = $('drop');
  let dragDepth = 0;
  const hasFiles = e => e.dataTransfer && Array.from(e.dataTransfer.types || []).includes('Files');
  de.addEventListener('dragenter', e => { if (!hasFiles(e)) return; e.preventDefault(); dragDepth++; drop.hidden = false; });
  de.addEventListener('dragover', e => { if (hasFiles(e)) e.preventDefault(); });
  de.addEventListener('dragleave', e => { if (!hasFiles(e)) return; if (--dragDepth <= 0) { dragDepth = 0; drop.hidden = true; } });
  de.addEventListener('drop', e => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    dragDepth = 0; drop.hidden = true;
    const f = e.dataTransfer.files && e.dataTransfer.files[0];
    if (f) openFile(f);
  });
  // 落在编辑区以外（如顶栏）时，别让浏览器直接跳转去打开这个文件
  window.addEventListener('dragover', e => { if (hasFiles(e)) e.preventDefault(); });
  window.addEventListener('drop', e => { if (hasFiles(e)) e.preventDefault(); });

  // ---------- 导出 ----------
  function download(text, filename, type) {
    const url = URL.createObjectURL(new Blob([text], { type }));
    const a = document.createElement('a');
    a.href = url; a.download = filename;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function exportPrimary() {
    const name = baseName();
    if (state.fmt === 'md') download(src.value, name + '.md', 'text/markdown;charset=utf-8');
    else download(src.value, name + '.html', 'text/html;charset=utf-8');
    state.dirty = false;
    saveDraft();
  }

  // Markdown → 独立 HTML 页面（内容已净化，自带样式，不依赖本站）
  function exportMdAsHtml() {
    const name = baseName();
    download(mdDocument(name, mdToSafeHtml(src.value)), name + '.html', 'text/html;charset=utf-8');
  }

  // Word：生成 Word 可识别的 HTML，存为 .doc（Word / WPS 都能直接打开编辑，不依赖第三方库）
  // HTML 文档用 DOMParser 解析（惰性文档，不执行脚本），只取 body 和内联 <style>
  function exportWord() {
    if (!src.value.trim()) { toast('内容为空，没有可导出的内容', true); return; }
    const name = baseName();
    let css = WORD_CSS, body;
    if (state.fmt === 'md') {
      body = mdToSafeHtml(src.value);
    } else {
      const doc = new DOMParser().parseFromString(src.value, 'text/html');
      doc.querySelectorAll('script,noscript,template,iframe,object,embed,base,link,meta').forEach(n => n.remove());
      doc.querySelectorAll('style').forEach(s => { css += '\n' + s.textContent; s.remove(); });
      body = doc.body.innerHTML;
    }
    const html = '﻿<html xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:w="urn:schemas-microsoft-com:office:word" xmlns="http://www.w3.org/TR/REC-html40">\n' +
      '<head>\n<meta charset="UTF-8">\n<title>' + escapeHtml(name) + '</title>\n' +
      '<!--[if gte mso 9]><xml><w:WordDocument><w:View>Print</w:View><w:Zoom>100</w:Zoom></w:WordDocument></xml><![endif]-->\n' +
      '<style>' + css + '\n</style>\n</head>\n<body>\n<div class="WordSection1">\n' + body + '\n</div>\n</body>\n</html>\n';
    download(html, name + '.doc', 'application/msword');
  }

  // PDF：交给浏览器打印，在打印对话框里选「另存为 PDF」。文字可选可搜，中文排版也最好。
  // 打印用单独的隐藏 iframe：sandbox="allow-same-origin allow-modals"，不给 allow-scripts，
  // 所以 HTML 文档里的脚本不会执行（靠脚本生成的内容不会出现在 PDF 里）；不要为了打印去放宽两个预览帧的 sandbox。
  function exportPdf() {
    if (!src.value.trim()) { toast('内容为空，没有可导出的内容', true); return; }
    const old = $('pvPrint');
    if (old) old.remove();
    const f = document.createElement('iframe');
    f.id = 'pvPrint';
    f.setAttribute('sandbox', 'allow-same-origin allow-modals');
    f.setAttribute('aria-hidden', 'true');
    f.tabIndex = -1;
    f.style.cssText = 'position:fixed;right:0;bottom:0;width:0;height:0;border:0';
    // Markdown 文档的 <title> 用文件名，浏览器会拿它当 PDF 的默认文件名
    f.srcdoc = state.fmt === 'md' ? mdDocument(baseName(), mdToSafeHtml(src.value)) : src.value;
    f.addEventListener('load', () => {
      // load 已等图片加载完；稍等一下让字体排版稳定
      setTimeout(() => {
        try { f.contentWindow.focus(); f.contentWindow.print(); }
        catch { toast('浏览器拦截了打印，请稍后重试', true); }
      }, 200);
    }, { once: true });
    document.body.appendChild(f);
  }

  btnExport.addEventListener('click', exportPrimary);
  btnExportAlt.addEventListener('click', exportMdAsHtml);
  $('btnWord').addEventListener('click', exportWord);
  $('btnPdf').addEventListener('click', exportPdf);

  // ---------- 编辑区 ----------
  src.addEventListener('input', onEdit);
  nameInput.addEventListener('input', scheduleSave);

  src.addEventListener('keydown', e => {
    // Tab 插入两个空格；按 Esc 后再按 Tab 可以正常移出编辑区
    if (e.key === 'Escape') { src.dataset.tabOut = '1'; return; }
    if (e.key === 'Tab' && !e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
      if (src.dataset.tabOut) { delete src.dataset.tabOut; return; }
      e.preventDefault();
      // execCommand 能进撤销栈；不支持时退回 setRangeText
      if (!document.execCommand('insertText', false, '  ')) {
        src.setRangeText('  ', src.selectionStart, src.selectionEnd, 'end');
        onEdit();
      }
      return;
    }
    delete src.dataset.tabOut;
  });

  document.addEventListener('keydown', e => {
    if (!(e.metaKey || e.ctrlKey) || e.altKey || e.shiftKey) return;
    const k = e.key.toLowerCase();
    if (k === 's') { e.preventDefault(); exportPrimary(); }
    else if (k === 'o') { e.preventDefault(); fileInput.click(); }
    else if (k === 'p') { e.preventDefault(); exportPdf(); }     // 打印文档本身，而不是整个编辑器页面
  });

  document.querySelectorAll('#fmtSeg button').forEach(b => b.addEventListener('click', () => {
    if (b.dataset.fmt === state.fmt) return;
    setFmt(b.dataset.fmt);
    scheduleSave();
    toast('已按 ' + (state.fmt === 'md' ? 'Markdown' : 'HTML') + ' 预览，内容没有转换');
  }));

  document.querySelectorAll('#viewSeg button').forEach(b => b.addEventListener('click', () => chooseView(b.dataset.view)));

  // 窗格标题条上的「收起」和收起后的竖条：改的是同一个视图状态
  function chooseView(v) {
    setView(v);
    try { localStorage.setItem(VIEW_KEY, state.view); } catch { /* 忽略 */ }
  }
  document.querySelectorAll('[data-view-to]').forEach(b => b.addEventListener('click', () => chooseView(b.dataset.viewTo)));

  // ---------- 全屏 ----------
  // 页内铺满（.fs）一定生效；浏览器全屏是锦上添花，不支持或被拒绝时忽略
  const btnFs = $('btnFs');
  function setFs(on) {
    de.classList.toggle('fs', on);
    btnFs.setAttribute('aria-pressed', String(on));
    btnFs.title = on ? '退出全屏（Esc）' : '全屏（Esc 退出）';
    btnFs.setAttribute('aria-label', on ? '退出全屏' : '全屏');
    try {
      if (on && !document.fullscreenElement && document.documentElement.requestFullscreen) {
        document.documentElement.requestFullscreen().catch(() => {});
      } else if (!on && document.fullscreenElement && document.exitFullscreen) {
        document.exitFullscreen().catch(() => {});
      }
    } catch { /* 忽略 */ }
  }
  btnFs.addEventListener('click', () => setFs(!de.classList.contains('fs')));
  // 浏览器全屏被 Esc 退出时，页内铺满一起退
  document.addEventListener('fullscreenchange', () => {
    if (!document.fullscreenElement && de.classList.contains('fs')) setFs(false);
  });
  // 没进浏览器全屏（不支持 / 被拒绝）时，Esc 由这里处理
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && de.classList.contains('fs') && !document.fullscreenElement) setFs(false);
  });

  narrow.addEventListener('change', () => {
    let v = state.view;
    try { v = localStorage.getItem(VIEW_KEY) || v; } catch { /* 忽略 */ }
    setView(v);
  });

  // ---------- 初始化 ----------
  const draft = loadDraft();
  if (draft) {
    src.value = draft.src;
    nameInput.value = draft.name || DEFAULT_NAME;
    state.dirty = !!draft.dirty;
    setFmt(draft.fmt);
    if (draft.src.trim()) toast('已恢复上次的草稿');
  } else {
    src.value = STARTER.md;
    nameInput.value = DEFAULT_NAME;
    setFmt('md');
  }
  let view = 'split';
  try { view = localStorage.getItem(VIEW_KEY) || view; } catch { /* 忽略 */ }
  setView(view);
  updateCount();
})();

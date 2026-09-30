/* 知识库管理页：列表、创建/编辑/删除、上传文档、查看切片、检索测试 */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const KID_RE = /^[a-f0-9]{32}$/;
  const fmtSize = n => n < 1024 ? n + ' B' : n < 1048576 ? (n / 1024).toFixed(1) + ' KB' : (n / 1048576).toFixed(1) + ' MB';
  const fmtNum = n => n >= 10000 ? (n / 10000).toFixed(1) + ' 万' : String(n);
  const fmtDate = ts => { const d = new Date(ts * 1000); return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); };
  const FILE_KIND = {
    pdf: ['PDF', '#B91C1C'], docx: ['W', '#185ABD'], xlsx: ['X', '#107C41'], pptx: ['P', '#C43E1C'], csv: ['CSV', '#107C41'],
    md: ['MD', '#27272A'], markdown: ['MD', '#27272A'], txt: ['TXT', '#71717A'], json: ['{}', '#A16207'], html: ['<>', '#C2410C'],
    htm: ['<>', '#C2410C'], xml: ['XML', '#0E7490'], yaml: ['YML', '#0E7490'], yml: ['YML', '#0E7490'], log: ['LOG', '#71717A'],
  };

  let kbs = [], limits = {}, vector = {}, current = null;

  let tt;
  function toast(msg, err) {
    const t = $('toast'); t.textContent = msg; t.classList.toggle('err', !!err); t.classList.add('show');
    clearTimeout(tt); tt = setTimeout(() => t.classList.remove('show'), 3200);
  }
  async function api(path, opts = {}) {
    const r = await fetch(path, {credentials: 'same-origin', ...opts});
    let d = null; try { d = await r.json(); } catch {}
    if (!r.ok) {
      let msg = d && d.detail;
      if (Array.isArray(msg)) msg = msg.map(x => x.msg).join('；');
      if (r.status === 413 && !msg) msg = '文件过大';
      throw new Error(msg || ('请求失败（' + r.status + '）'));
    }
    return d;
  }
  const jsend = (path, body, method = 'POST') => api(path, {method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  const btn = (label, fn, cls, ic) => {
    const b = el('button', 'iconbtn' + (cls ? ' ' + cls : '')); b.type = 'button';
    if (ic) b.innerHTML = icon(ic);
    b.append(label); b.addEventListener('click', fn); return b;
  };

  /* ---------- 列表 ---------- */
  async function loadList(select) {
    try { const d = await api('/api/kb'); kbs = d.kbs; limits = d.limits; vector = d.vector || {}; }
    catch (e) { toast(e.message, true); return; }
    renderList();
    if (select) openKb(select);
  }

  function renderList() {
    const box = $('kbList'); box.replaceChildren();
    const groups = [['我的知识库', k => k.editable], ['公共知识库', k => !k.editable]];
    for (const [title, fn] of groups) {
      const items = kbs.filter(fn);
      if (title === '公共知识库' && !items.length) continue;
      box.append(el('div', 'group', title));
      if (!items.length) box.append(el('div', 'empty', '还没有，点击"新建知识库"并上传文档'));
      for (const k of items) {
        const b = el('button', 'kb' + (current && current.id === k.id ? ' active' : '')); b.type = 'button';
        const n = el('div', 'n'); n.append(el('span', '', k.name));
        if (k.visibility === 'public') n.append(el('span', 'badge public', '公共'));
        b.append(n);
        if (k.description) b.append(el('div', 'ds', k.description));
        b.append(el('div', 'meta', k.docs + ' 篇文档 · ' + fmtNum(k.chars) + ' 字'));
        b.addEventListener('click', () => openKb(k.id));
        box.append(b);
      }
    }
    const mine = kbs.filter(k => k.editable).length;
    const used = limits.used_bytes || 0, cap = (limits.quota_mb || 1) * 1048576;
    const q = $('quota'); q.replaceChildren();
    q.append(`已建 ${mine} / ${limits.max_kbs} 个 · 空间 ${fmtSize(used)} / ${limits.quota_mb} MB`);
    const bar = el('div', 'barq'); const i = el('i'); i.style.width = Math.min(100, used / cap * 100).toFixed(1) + '%'; bar.append(i); q.append(bar);
    const mode = el('div', 'vmode');
    if (vector.enabled) {
      mode.append('检索：关键词 + 语义向量' + (vector.rerank ? ' + 重排' : ''));
      mode.title = '向量模型 ' + vector.model + (vector.rerank ? '，重排模型 ' + vector.rerank : '') + '（' + vector.provider + '）';
      if (vector.pending) mode.append(el('span', 'warn', ' · 向量化排队 ' + vector.pending + ' 段'));
      if (vector.error) mode.append(el('span', 'warn', ' · 向量服务暂不可用，已退回关键词检索'));
    } else mode.append('检索：关键词（未配置向量模型）');
    q.append(mode);
  }

  /* ---------- 详情 ---------- */
  async function openKb(id) {
    try { current = await api('/api/kb/' + id); }
    catch (e) { toast(e.message, true); history.replaceState(null, '', location.pathname); return; }
    history.replaceState(null, '', '#' + id);
    renderList();
    renderDetail();
  }

  let vecTimer = null;
  function scheduleVecRefresh() {
    clearTimeout(vecTimer);
    const v = current && current.vector;
    if (!v || !v.enabled || !current.documents.some(d => d.vec_chunks != null && d.vec_chunks < d.chunks)) return;
    const id = current.id;
    vecTimer = setTimeout(async () => {
      if (!current || current.id !== id || document.hidden) return scheduleVecRefresh();
      try {
        const fresh = await api('/api/kb/' + id);
        if (current && current.id === id) {
          current.documents.forEach(d => { const f = fresh.documents.find(x => x.id === d.id); if (f) d.vec_chunks = f.vec_chunks; });
          document.querySelectorAll('.doc[data-id]').forEach(row => {
            const d = current.documents.find(x => x.id === row.dataset.id);
            const fs = row.querySelector('.fs');
            if (d && fs) fs.textContent = fs.textContent.replace(/ · (已向量化|向量化中 \d+\/\d+)$/, '') + vecLabel(d);
          });
        }
      } catch {}
      scheduleVecRefresh();
    }, 4000);
  }

  function renderDetail() {
    const kb = current, d = $('detail'); d.replaceChildren();
    scheduleVecRefresh();
    const head = el('div', 'detail-head');
    const left = el('div');
    const h = el('h2'); h.append(kb.name);
    h.append(el('span', 'badge ' + (kb.visibility === 'public' ? 'public' : 'user'), kb.visibility === 'public' ? '公共' : '私有'));
    left.append(h);
    if (kb.description) left.append(el('p', '', kb.description));
    left.append(el('div', 'stats', kb.docs + ' 篇文档 · ' + fmtNum(kb.chars) + ' 字 · 更新于 ' + fmtDate(kb.updated_at)));
    const btns = el('div', 'btns');
    btns.append(btn('在对话中使用', () => { location.href = 'agent.html?kb=' + kb.id; }, 'primary', 'message'));
    if (kb.editable) {
      btns.append(btn('编辑信息', () => editDlg(kb), '', 'save'));
      btns.append(btn('删除', removeKb, 'danger', 'trash'));
    }
    head.append(left, btns);
    d.append(head);

    if (kb.editable) d.append(uploadSection());
    d.append(docsSection());
    d.append(searchSection());
    if (!kb.editable) d.append(el('div', 'readonly', '公共知识库由管理员维护，只读。'));
  }

  function uploadSection() {
    const sec = el('section', 'sec');
    const h = el('h3', '', '上传文档'); h.append(el('small', '', '单个文件 ≤ ' + limits.upload_max_mb + ' MB'));
    const drop = el('div', 'drop'); drop.tabIndex = 0; drop.setAttribute('role', 'button');
    drop.setAttribute('aria-label', '选择或拖入文件上传');
    drop.innerHTML = icon('upload');
    drop.append('点击选择或拖入文件（可多选）');
    drop.append(el('small', '', (limits.exts || []).join(' ')));
    const input = el('input'); input.type = 'file'; input.multiple = true; input.hidden = true;
    input.accept = (limits.exts || []).join(',');
    const list = el('div', 'uploads');
    drop.addEventListener('click', () => input.click());
    drop.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input.click(); } });
    drop.addEventListener('dragover', e => { e.preventDefault(); drop.classList.add('over'); });
    drop.addEventListener('dragleave', () => drop.classList.remove('over'));
    drop.addEventListener('drop', e => { e.preventDefault(); drop.classList.remove('over'); uploadFiles([...e.dataTransfer.files], list); });
    input.addEventListener('change', () => { const fs = [...input.files]; input.value = ''; uploadFiles(fs, list); });
    sec.append(h, drop, input, list);
    if (vector.enabled) sec.append(el('div', 'privacy', '提示：为支持语义检索，文档文字会发送到第三方向量服务（' + vector.provider + '）计算向量，请勿上传涉密资料。'));
    return sec;
  }

  async function uploadFiles(files, list) {
    if (!files.length) return;
    const kid = current.id;
    let okCount = 0;
    // 串行上传：解析在沙箱中进行，并发会挤占执行资源
    for (const f of files) {
      const row = el('div', 'up'); const st = el('span', 'st', '等待中');
      row.append(el('span', 'nm', f.name), st); list.append(row);
    }
    const rows = [...list.children].slice(-files.length);
    for (let i = 0; i < files.length; i++) {
      const f = files[i], row = rows[i], st = row.querySelector('.st');
      if (f.size > limits.upload_max_mb * 1048576) { row.classList.add('err'); st.textContent = '文件过大'; continue; }
      st.textContent = '解析中…';
      try {
        const fd = new FormData(); fd.append('file', f);
        const r = await api('/api/kb/' + kid + '/documents', {method: 'POST', body: fd});
        row.classList.add('ok');
        st.textContent = r.chunks + ' 个片段' + (r.truncated ? '（内容过长已截断）' : '');
        okCount++;
      } catch (e) { row.classList.add('err'); st.textContent = e.message; }
    }
    if (okCount && current && current.id === kid) {
      toast('已添加 ' + okCount + ' 篇文档');
      current = await api('/api/kb/' + kid).catch(() => current);
      const keep = list; // 保留上传结果
      await loadList();
      renderDetail();
      const nl = document.querySelector('.uploads'); if (nl) nl.replaceChildren(...keep.children);
    }
  }

  function vecLabel(doc) {
    const v = current.vector || {};
    if (!v.enabled || doc.vec_chunks == null) return '';
    return doc.vec_chunks >= doc.chunks ? ' · 已向量化' : ' · 向量化中 ' + doc.vec_chunks + '/' + doc.chunks;
  }

  function docsSection() {
    const kb = current, sec = el('section', 'sec');
    const h = el('h3', '', '文档'); h.append(el('small', '', kb.documents.length + ' / ' + limits.max_docs));
    const box = el('div', 'docs');
    if (!kb.documents.length) box.append(el('div', 'doc-empty', kb.editable ? '还没有文档，上传后即可在对话中检索' : '暂无文档'));
    for (const doc of kb.documents) {
      const row = el('div', 'doc'); row.dataset.id = doc.id;
      const [label, color] = FILE_KIND[(doc.ext || '').slice(1)] || ['FILE', '#71717A'];
      const fk = el('span', 'fk', label); fk.style.background = color;
      const info = el('div'); info.style.minWidth = '0';
      info.append(el('div', 'fn', doc.filename), el('div', 'fs', fmtSize(doc.size) + ' · ' + fmtNum(doc.chars) + ' 字 · ' + doc.chunks + ' 个片段 · ' + fmtDate(doc.created_at) + vecLabel(doc)));
      const ops = el('div', 'ops');
      let open = null;
      const vlabel = el('span', '', '查看片段');
      const view = btn('', async () => {
        if (open) { open.remove(); open = null; vlabel.textContent = '查看片段'; view.setAttribute('aria-expanded', 'false'); return; }
        try {
          const r = await api('/api/kb/' + kb.id + '/documents/' + doc.id + '/chunks?start=0&count=20');
          open = el('div', 'chunks');
          for (const c of r.chunks) { const ch = el('div', 'chunk'); ch.append(el('b', '', '#' + c.seq), c.content); open.append(ch); }
          if (doc.chunks > r.chunks.length) open.append(el('div', 'empty', '仅显示前 ' + r.chunks.length + ' 个片段，共 ' + doc.chunks + ' 个'));
          row.append(open); vlabel.textContent = '收起'; view.setAttribute('aria-expanded', 'true');
        } catch (e) { toast(e.message, true); }
      });
      view.append(vlabel);
      ops.append(view);
      const dl = el('a', 'iconbtn'); dl.href = '/api/kb/' + kb.id + '/documents/' + doc.id + '/raw';
      dl.innerHTML = icon('download'); dl.title = '下载原文件'; dl.setAttribute('aria-label', '下载 ' + doc.filename);
      ops.append(dl);
      if (kb.editable) {
        const del = btn('', async () => {
          if (!confirm('从知识库删除「' + doc.filename + '」？')) return;
          try {
            await api('/api/kb/' + kb.id + '/documents/' + doc.id, {method: 'DELETE'});
            toast('已删除'); current = await api('/api/kb/' + kb.id); await loadList(); renderDetail();
          } catch (e) { toast(e.message, true); }
        }, 'danger', 'trash');
        del.title = '删除'; del.setAttribute('aria-label', '删除 ' + doc.filename);
        ops.append(del);
      }
      row.append(fk, info, ops);
      box.append(row);
    }
    sec.append(h, box);
    return sec;
  }

  // 高亮检索词（纯 DOM，按查询中的连续片段匹配）
  function highlight(text, query) {
    const frag = document.createDocumentFragment();
    const words = [...new Set((query.toLowerCase().match(/[a-z0-9]+|[^\sa-z0-9，。！？、,.!?;；:："'“”‘’()（）]{2,}/g) || []))]
      .flatMap(w => /^[a-z0-9]+$/.test(w) ? [w] : Array.from({length: Math.max(1, w.length - 1)}, (_, i) => w.slice(i, i + 2)))
      .filter(Boolean);
    if (!words.length) { frag.append(text); return frag; }
    const esc = words.map(w => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).sort((a, b) => b.length - a.length);
    const re = new RegExp('(' + esc.join('|') + ')', 'gi');
    let last = 0;
    for (const m of text.matchAll(re)) {
      if (m.index > last) frag.append(text.slice(last, m.index));
      frag.append(el('mark', '', m[0])); last = m.index + m[0].length;
    }
    frag.append(text.slice(last));
    return frag;
  }

  function searchSection() {
    const sec = el('section', 'sec');
    sec.append(el('h3', '', '检索测试'));
    const row = el('form', 'searchrow');
    const inp = el('input', 'field'); inp.type = 'search'; inp.placeholder = '输入问题或关键词，查看 AI 会检索到哪些片段'; inp.maxLength = 500;
    inp.setAttribute('aria-label', '检索测试');
    const go = el('button', 'iconbtn primary'); go.type = 'submit'; go.innerHTML = icon('search'); go.append('检索');
    row.append(inp, go);
    const hits = el('div', 'hits'); hits.setAttribute('aria-live', 'polite');
    row.addEventListener('submit', async e => {
      e.preventDefault();
      const q = inp.value.trim(); if (!q) return;
      go.disabled = true; hits.replaceChildren(el('div', 'empty', '检索中…'));
      try {
        const r = await jsend('/api/kb/' + current.id + '/search', {query: q, top_k: 8});
        hits.replaceChildren();
        if (r.mode) hits.append(el('div', 'empty', '检索方式：' + r.mode));
        if (!r.hits.length) hits.append(el('div', 'empty', '没有匹配的片段，换个关键词试试'));
        r.hits.forEach((x, i) => {
          const h = el('div', 'hit');
          h.append(el('div', 'h', '[' + (i + 1) + '] ' + x.filename + ' · 片段 #' + x.seq));
          const c = el('div', 'c'); c.append(highlight(x.content, q)); h.append(c);
          hits.append(h);
        });
      } catch (err) { hits.replaceChildren(); toast(err.message, true); }
      finally { go.disabled = false; }
    });
    sec.append(row, hits);
    return sec;
  }

  /* ---------- 新建 / 编辑 / 删除 ---------- */
  let dlgSubmit = null;
  function editDlg(kb) {
    $('dlgTitle').textContent = kb ? '编辑知识库' : '新建知识库';
    $('f_name').value = kb ? kb.name : ''; $('f_desc').value = kb ? kb.description : '';
    $('dlgErr').textContent = '';
    dlgSubmit = async () => {
      const body = {name: $('f_name').value.trim(), description: $('f_desc').value.trim()};
      if (!body.name) throw new Error('名称不能为空');
      if (kb) { await jsend('/api/kb/' + kb.id, body, 'PATCH'); toast('已保存'); await loadList(kb.id); }
      else { const r = await jsend('/api/kb', body); toast('已创建 ' + r.name); await loadList(r.id); }
    };
    $('dlg').showModal(); $('f_name').focus();
  }
  $('dlgForm').addEventListener('submit', async e => {
    if (e.submitter && e.submitter.value === 'cancel') return;
    e.preventDefault();
    $('dlgOk').disabled = true;
    try { await dlgSubmit(); $('dlg').close(); }
    catch (err) { $('dlgErr').textContent = err.message; }
    finally { $('dlgOk').disabled = false; }
  });
  $('newBtn').addEventListener('click', () => editDlg(null));

  async function removeKb() {
    if (!confirm('确定删除知识库「' + current.name + '」及其全部文档？此操作不可恢复。')) return;
    try {
      await api('/api/kb/' + current.id, {method: 'DELETE'});
      toast('已删除'); location.hash = ''; location.reload();
    } catch (e) { toast(e.message, true); }
  }

  const initial = location.hash.slice(1);
  loadList(KID_RE.test(initial) ? initial : null);
})();

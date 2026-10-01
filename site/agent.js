(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const chat = $('chat'), inner = $('chatInner'), input = $('input'), sendBtn = $('send');
  const sessionsEl = $('sessions'), side = $('side'), scrim = $('scrim');
  const attachList = $('attachList'), fileInput = $('fileInput');

  let sid = null;          // 当前会话 ID
  let busy = false;
  let abortCtl = null;
  let pendingUploads = []; // 本轮已上传但尚未发送的文件路径
  let kbAll = [];          // 可用知识库
  let kbSel = [];          // 当前会话选择的知识库 ID
  let kbMax = 5;
  let modelAll = [];       // 可选对话模型
  let modelSel = null;     // 当前会话使用的模型 ID
  const MODEL_PREF = 'cl_model';

  /* ---------------- 工具函数 ---------------- */
  const el = (tag, cls, text) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  };
  const fmtSize = n => n < 1024 ? n + ' B' : n < 1048576 ? (n / 1024).toFixed(1) + ' KB' : (n / 1048576).toFixed(1) + ' MB';
  const baseName = p => String(p || '').split('/').pop();
  const nearBottom = () => chat.scrollHeight - chat.scrollTop - chat.clientHeight < 120;
  const scrollDown = (force) => { if (force || nearBottom()) chat.scrollTop = chat.scrollHeight; };

  let toastTimer;
  function toast(msg, isErr) {
    const t = $('toast');
    t.textContent = msg; t.classList.toggle('err', !!isErr); t.classList.add('show');
    clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove('show'), 3200);
  }

  async function api(path, opts = {}) {
    const r = await fetch(path, {credentials: 'same-origin', ...opts});
    let data = null;
    try { data = await r.json(); } catch {}
    if (!r.ok) throw new Error((data && data.detail) || ('请求失败（' + r.status + '）'));
    return data;
  }
  const jsend = (path, body, method = 'POST') =>
    api(path, {method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});

  /* ---------------- Markdown（净化后渲染） ---------------- */
  marked.setOptions({gfm: true, breaks: true});
  DOMPurify.addHook('afterSanitizeAttributes', node => {
    if (node.tagName === 'A') { node.setAttribute('target', '_blank'); node.setAttribute('rel', 'noopener noreferrer'); }
    if (node.tagName === 'IMG' && !/^\/api\/files\//.test(node.getAttribute('src') || '')) node.remove(); // 禁止外链图片
  });
  const renderMd = text => DOMPurify.sanitize(marked.parse(text || ''), {FORBID_TAGS: ['style', 'form', 'input'], FORBID_ATTR: ['style']});

  /* ---------------- 文件卡片 ---------------- */
  const FILE_KIND = {
    docx: ['W', '#185ABD'], doc: ['W', '#185ABD'], xlsx: ['X', '#107C41'], xls: ['X', '#107C41'], csv: ['CSV', '#107C41'],
    pptx: ['P', '#C43E1C'], pdf: ['PDF', '#B91C1C'], png: ['IMG', '#6D28D9'], jpg: ['IMG', '#6D28D9'], jpeg: ['IMG', '#6D28D9'],
    gif: ['IMG', '#6D28D9'], webp: ['IMG', '#6D28D9'], md: ['MD', '#27272A'], txt: ['TXT', '#71717A'], json: ['{}', '#A16207'],
    py: ['PY', '#1E40AF'], html: ['<>', '#C2410C'], zip: ['ZIP', '#52525B'],
  };
  function fileCard(f) {
    const ext = (f.path.split('.').pop() || '').toLowerCase();
    const [label, color] = FILE_KIND[ext] || ['FILE', '#71717A'];
    const a = el('a', 'file');
    a.href = f.url; a.setAttribute('download', baseName(f.path)); a.dataset.path = f.path;
    a.title = '下载 ' + f.path;
    const fi = el('span', 'fi', label); fi.style.background = color;
    const info = el('span');
    info.append(el('div', 'fn', baseName(f.path)), el('div', 'fs', fmtSize(f.size) + (f.path.includes('/') ? ' · ' + f.path : '')));
    const dl = el('span', 'dl'); dl.innerHTML = icon('download');
    a.append(fi, info, dl);
    const wrap = el('div');
    wrap.style.display = 'contents';
    wrap.append(a);
    if (['png', 'jpg', 'jpeg', 'gif', 'webp'].includes(ext)) {
      const img = el('img', 'preview'); img.src = f.url; img.alt = baseName(f.path); img.loading = 'lazy';
      const box = el('div'); box.style.flexBasis = '100%'; box.append(img);
      wrap.append(box);
    }
    return wrap;
  }

  /* ---------------- 消息渲染 ---------------- */
  function addUser(text) {
    const m = el('div', 'msg user');
    const body = el('div', 'body');
    body.append(el('div', 'bubble', text));
    m.append(el('div', 'av', '我'), body);
    inner.append(m);
    scrollDown(true);
  }

  function botMessage() {
    const m = el('div', 'msg bot');
    const body = el('div', 'body');
    m.append(el('div', 'av', 'AI'), body);
    inner.append(m);

    let cur = null, raw = '', raf = 0, typing = null, thinkEl = null;
    const steps = {};
    const caret = el('span', 'caret');

    const clearTyping = () => { if (typing) { typing.remove(); typing = null; } };
    const flush = () => {
      raf = 0;
      if (!cur) return;
      cur.innerHTML = renderMd(raw);
      if (busy) cur.append(caret);
      scrollDown();
    };

    return {
      el: m,
      typing() {
        clearTyping();
        typing = el('div', 'bubble'); typing.innerHTML = '<span class="dots"><i></i><i></i><i></i></span>';
        body.append(typing); scrollDown(true);
      },
      text(delta, whole) {
        clearTyping(); this.endThink();
        if (!cur) { cur = el('div', 'bubble md'); raw = ''; body.append(cur); }
        raw += delta;
        if (whole) flush(); else if (!raf) raf = requestAnimationFrame(flush);
      },
      endText() {
        if (raf) { cancelAnimationFrame(raf); flush(); }
        caret.remove();
        if (cur && !raw.trim()) cur.remove();
        cur = null;
      },
      think(delta) {
        clearTyping();
        if (!thinkEl) {
          if (cur) this.endText();
          thinkEl = el('details', 'think'); thinkEl.open = busy;
          thinkEl.append(el('summary', '', '思考过程'), el('div', 'tb'));
          body.append(thinkEl);
        }
        thinkEl.lastChild.textContent += delta;
        scrollDown();
      },
      endThink() { if (thinkEl) { thinkEl.open = false; thinkEl = null; } },
      step(call) {
        this.endText(); clearTyping(); this.endThink();
        const d = el('details', 'step run');
        const sm = el('summary');
        const s = el('span', 's', '执行中…');
        sm.append(el('span', 'ico'), el('span', 't', call.title || call.name), s);
        const det = el('div', 'd');
        let args = null;
        try { args = JSON.parse(call.args || '{}'); } catch { args = call.args; }
        if (args && typeof args === 'object') {
          const {code, stdin, files, ...rest} = args;
          if (Object.keys(rest).length) { det.append(el('div', 'lbl', '参数')); det.append(el('pre', '', JSON.stringify(rest, null, 2))); }
          if (code) { det.append(el('div', 'lbl', '代码')); det.append(el('pre', '', code)); }
          if (stdin) { det.append(el('div', 'lbl', '输入')); det.append(el('pre', '', prettyJson(stdin))); }
          if (Array.isArray(files)) {
            for (const f of files) { det.append(el('div', 'lbl', f.path)); det.append(el('pre', '', f.content)); }
          }
        } else if (args) { det.append(el('pre', '', String(args))); }
        d.append(sm, det);
        body.append(d);
        steps[call.id] = {d, s, det};
        scrollDown();
        if (busy) this.typing();
      },
      result(r) {
        const st = steps[r.tool_call_id || r.id];
        if (!st) return;
        st.d.classList.remove('run');
        st.d.classList.add(r.ok ? 'ok' : 'fail');
        st.d.querySelector('.ico').innerHTML = r.ok ? icon('check') : '!';
        st.s.textContent = r.summary || (r.ok ? '完成' : '失败');
        const dt = r.detail || {};
        if (Array.isArray(dt.hits) && dt.hits.length) {
          st.det.append(el('div', 'lbl', dt.query ? '检索结果' + (dt.mode ? '（' + dt.mode + '）' : '') : '读取内容'));
          const hits = el('div', 'hits');
          dt.hits.forEach((h, i) => {
            const x = el('div', 'hit');
            x.append(el('div', 'h', (dt.query ? '[' + (i + 1) + '] ' : '') + (h.kb ? h.kb + ' · ' : '') + h.file + ' · 片段 #' + h.seq));
            x.append(el('div', 'c', h.snippet));
            hits.append(x);
          });
          st.det.append(hits);
        }
        if (dt.stdout) { st.det.append(el('div', 'lbl', 'stdout')); st.det.append(el('pre', '', dt.stdout)); }
        if (dt.stderr) { st.det.append(el('div', 'lbl', 'stderr')); st.det.append(el('pre', 'e', dt.stderr)); }
        if (r.files && r.files.length) {
          const box = el('div', 'files');
          r.files.forEach(f => box.append(fileCard(f)));
          st.d.after(box);
        }
        scrollDown();
      },
      interruptPending() {
        for (const st of Object.values(steps)) {
          if (st.d.classList.contains('run')) {
            st.d.classList.replace('run', 'fail');
            st.d.querySelector('.ico').textContent = '!';
            st.s.textContent = '已中断';
          }
        }
      },
      error(msg) { this.endText(); clearTyping(); body.append(el('div', 'bubble err', '出错了：' + msg)); scrollDown(true); },
      note(msg) { this.endText(); clearTyping(); body.append(el('div', 'bubble note', msg)); },
      done() {
        this.endText(); clearTyping(); this.interruptPending();
        if (!body.children.length) body.append(el('div', 'bubble note', '（没有返回内容）'));
      },
    };
  }

  function prettyJson(s) {
    try { return JSON.stringify(JSON.parse(s), null, 2); } catch { return s; }
  }

  function showWelcome() {
    inner.replaceChildren($('welcomeTpl').content.cloneNode(true));
    inner.querySelectorAll('.chip').forEach(c => c.addEventListener('click', () => send(c.textContent)));
  }

  /* ---------------- 会话 ---------------- */
  async function loadSessions() {
    let list = [];
    try { list = (await api('/api/sessions')).sessions; } catch { return; }
    sessionsEl.replaceChildren();
    if (!list.length) { sessionsEl.append(el('li', 'side-empty', '还没有会话')); return; }
    for (const s of list) {
      const li = el('li'); if (s.id === sid) li.classList.add('active');
      const open = el('button', 's-open', s.title || '新对话'); open.title = s.title;
      open.addEventListener('click', () => { location.hash = 's=' + s.id; closeSide(); });
      const del = el('button', 's-del'); del.innerHTML = icon('x'); del.title = '删除会话'; del.setAttribute('aria-label', '删除会话 ' + s.title);
      del.addEventListener('click', async () => {
        if (!confirm('删除该会话及其生成的文件？')) return;
        try {
          await api('/api/sessions/' + s.id, {method: 'DELETE'});
          if (s.id === sid) newChat(); else loadSessions();
        } catch (e) { toast(e.message, true); }
      });
      li.append(open, del);
      sessionsEl.append(li);
    }
  }

  function setSid(id) {
    sid = id;
    const h = id ? '#s=' + id : location.pathname + location.search;
    history.replaceState(null, '', h);
    sessionsEl.querySelectorAll('li').forEach(li => li.classList.remove('active'));
  }

  function newChat() {
    if (busy) return;
    setSid(null); pendingUploads = []; renderAttach();
    kbSel = []; renderKb();
    modelSel = preferredModel(); renderModel();
    showWelcome(); loadSessions(); input.focus(); closeSide();
  }

  async function openSession(id) {
    if (busy) return;
    let data;
    try { data = await api('/api/sessions/' + id + '/messages'); }
    catch (e) { toast(e.message, true); newChat(); return; }
    sid = id; pendingUploads = []; renderAttach();
    kbSel = (data.session && data.session.kb_ids) || []; renderKb();
    const sm = data.session && data.session.model_id;
    // 模型列表可能尚未加载完：先采用会话的模型，loadModels 完成后再校验
    modelSel = sm && (!modelAll.length || modelAll.some(m => m.id === sm)) ? sm : preferredModel(); renderModel();
    inner.replaceChildren();
    const existing = new Set(data.existing_files || []);
    let bot = null;
    for (const m of data.messages) {
      if (m.role === 'user') { if (bot) bot.done(); bot = null; addUser(m.content || ''); continue; }
      if (!bot) bot = botMessage();
      if (m.role === 'assistant') {
        if (m.reasoning) { bot.think(m.reasoning); bot.endThink(); }
        if (m.content) { bot.text(m.content, true); bot.endText(); }
        (m.tool_calls || []).forEach(c => bot.step(c));
      } else if (m.role === 'tool') {
        bot.result(m);
      }
    }
    if (bot) bot.done();
    inner.querySelectorAll('.file').forEach(a => {
      if (!existing.has(a.dataset.path)) { a.classList.add('gone'); a.querySelector('.fs').textContent = '已过期'; }
    });
    if (!data.messages.length) showWelcome();
    loadSessions();
    scrollDown(true);
  }

  function sidFromHash() {
    const m = location.hash.match(/^#s=([a-f0-9]{32})$/);
    return m ? m[1] : null;
  }
  addEventListener('hashchange', () => {
    const id = sidFromHash();
    if (id && id !== sid) openSession(id);
  });

  /* ---------------- 知识库选择 ---------------- */
  const kbToggle = $('kbToggle'), kbPop = $('kbPop');

  async function loadKbs() {
    try { const d = await api('/api/kb'); kbAll = d.kbs; kbMax = d.limits.max_per_session || 5; }
    catch { kbAll = []; }
    renderKb();
  }

  function renderKb() {
    const bar = $('kbBar');
    bar.querySelectorAll('.kb-chip').forEach(c => c.remove());
    kbSel = kbSel.filter(id => kbAll.some(k => k.id === id) || !kbAll.length);
    for (const id of kbSel) {
      const kb = kbAll.find(k => k.id === id);
      if (!kb) continue;
      const chip = el('span', 'kb-chip'); chip.title = kb.name + (kb.description ? '：' + kb.description : '');
      chip.innerHTML = icon('book');
      chip.append(el('span', '', kb.name));
      const x = el('button'); x.type = 'button'; x.innerHTML = icon('x'); x.setAttribute('aria-label', '取消使用知识库 ' + kb.name);
      x.addEventListener('click', () => setKbs(kbSel.filter(k => k !== id)));
      chip.append(x);
      bar.insertBefore(chip, kbPop);
    }
    kbToggle.lastChild.textContent = kbSel.length ? '' : '知识库';
    kbToggle.title = kbSel.length ? '添加 / 管理知识库' : '选择知识库，AI 将基于其中的文档回答';
    kbToggle.setAttribute('aria-label', kbToggle.title);
    input.placeholder = kbSel.length ? '基于所选知识库提问… Enter 发送，Shift+Enter 换行'
      : (matchMedia('(max-width: 600px)').matches ? '输入你的问题…' : '输入你的问题，或让我用技能生成文档… Enter 发送，Shift+Enter 换行');
    if (!kbPop.hidden) renderKbPop();
  }

  function renderKbPop() {
    kbPop.replaceChildren();
    const ph = el('div', 'ph', '为本会话选择知识库（最多 ' + kbMax + ' 个）');
    const manage = el('a', '', '管理'); manage.href = 'knowledge.html';
    ph.append(manage); kbPop.append(ph);
    if (!kbAll.length) {
      const n = el('div', 'none', '还没有可用的知识库。');
      const a = el('a', '', '去创建知识库并上传文档 →'); a.href = 'knowledge.html';
      n.append(el('br'), a); kbPop.append(n); return;
    }
    for (const kb of kbAll) {
      const lab = el('label', 'kb-opt');
      const cb = el('input'); cb.type = 'checkbox'; cb.checked = kbSel.includes(kb.id);
      cb.disabled = !cb.checked && kbSel.length >= kbMax;
      cb.addEventListener('change', () => setKbs(cb.checked ? [...kbSel, kb.id] : kbSel.filter(k => k !== kb.id)));
      const info = el('div');
      const t = el('div', 't', kb.name);
      if (!kb.editable) t.append(el('span', 'badge public', '公共'));
      info.append(t, el('div', 'm', kb.docs + ' 篇文档' + (kb.description ? ' · ' + kb.description.slice(0, 40) : '')));
      lab.append(cb, info);
      kbPop.append(lab);
    }
  }

  async function setKbs(ids) {
    const prev = kbSel;
    kbSel = ids.slice(0, kbMax);
    renderKb();
    if (!sid) return; // 新会话：随首条消息一起提交
    try { kbSel = (await jsend('/api/sessions/' + sid + '/kbs', {kb_ids: kbSel}, 'PUT')).kb_ids; renderKb(); }
    catch (e) { kbSel = prev; renderKb(); toast(e.message, true); }
  }

  function toggleKbPop(open) {
    open = open != null ? open : kbPop.hidden;
    if (open) { renderKbPop(); loadKbs(); }
    togglePop(kbPop, kbToggle, open);
  }
  kbToggle.addEventListener('click', e => { e.stopPropagation(); toggleKbPop(); });
  document.addEventListener('click', e => { if (!kbPop.hidden && !$('kbBar').contains(e.target)) toggleKbPop(false); });
  kbPop.addEventListener('keydown', e => { if (e.key === 'Escape') { toggleKbPop(false); kbToggle.focus(); } });

  /* ---------------- 模型选择 ---------------- */
  const modelToggle = $('modelToggle'), modelPop = $('modelPop');
  const curModel = () => modelAll.find(m => m.id === modelSel);

  // 新对话用：上次手动选择的模型（仍可用时），否则默认模型
  function preferredModel() {
    let pref = null; try { pref = localStorage.getItem(MODEL_PREF); } catch {}
    if (pref && modelAll.some(m => m.id === pref)) return pref;
    const d = modelAll.find(m => m.is_default) || modelAll[0];
    return d ? d.id : null;
  }

  async function loadModels() {
    try { modelAll = (await api('/api/models')).models; } catch { modelAll = []; }
    if (!modelSel || !modelAll.some(m => m.id === modelSel)) modelSel = preferredModel();
    renderModel();
  }

  function renderModel() {
    const m = curModel();
    modelToggle.hidden = !m;
    if (!m) return;
    modelToggle.replaceChildren();
    modelToggle.insertAdjacentHTML('beforeend', icon('zap'));
    modelToggle.append(el('span', '', m.name), el('span', 'car', '▾'));
    modelToggle.title = '当前模型：' + m.name + (m.description ? '（' + m.description + '）' : '') + '，点击切换';
    modelToggle.setAttribute('aria-label', modelToggle.title);
    const st = $('statusText'); if (!$('dot').classList.contains('off')) st.textContent = '在线 · ' + m.name;
    if (!modelPop.hidden) renderModelPop();
  }

  function renderModelPop() {
    modelPop.replaceChildren(el('div', 'ph', '选择模型（对当前会话生效）'));
    for (const m of modelAll) {
      const lab = el('label', 'kb-opt');
      const rb = el('input'); rb.type = 'radio'; rb.name = 'model'; rb.checked = m.id === modelSel;
      rb.addEventListener('change', () => pickModel(m.id));
      const info = el('div');
      const t = el('div', 't', m.name);
      if (m.is_default) t.append(el('span', 'badge user', '默认'));
      if (!m.supports_tools) t.append(el('span', 'badge', '仅对话'));
      info.append(t);
      if (m.description) info.append(el('div', 'm', m.description));
      lab.append(rb, info);
      modelPop.append(lab);
    }
  }

  function pickModel(id) {
    modelSel = id;
    try { localStorage.setItem(MODEL_PREF, id); } catch {}
    renderModel(); togglePop(modelPop, modelToggle, false); input.focus();
    const m = curModel();
    if (m && !m.supports_tools) toast('「' + m.name + '」不支持工具：技能、文件生成和知识库检索将不可用');
    else if (m) toast('已切换到 ' + m.name + (sid ? '，下一条消息生效' : ''));
  }

  function togglePop(pop, btn, open) {
    open = open != null ? open : pop.hidden;
    for (const [p, b] of [[modelPop, modelToggle], [kbPop, kbToggle]]) {   // 同时只开一个
      if (p !== pop && !p.hidden) { p.hidden = true; b.setAttribute('aria-expanded', 'false'); }
    }
    pop.hidden = !open;
    btn.setAttribute('aria-expanded', String(open));
    if (open) { const f = pop.querySelector('input:checked, input:not(:disabled)'); if (f) f.focus(); }
  }
  modelToggle.addEventListener('click', e => {
    e.stopPropagation();
    if (modelPop.hidden) { renderModelPop(); loadModels(); }
    togglePop(modelPop, modelToggle);
  });
  modelPop.addEventListener('keydown', e => { if (e.key === 'Escape') { togglePop(modelPop, modelToggle, false); modelToggle.focus(); } });
  document.addEventListener('click', e => { if (!modelPop.hidden && !$('kbBar').contains(e.target)) togglePop(modelPop, modelToggle, false); });

  /* ---------------- 上传 ---------------- */
  function renderAttach(uploading) {
    attachList.replaceChildren();
    pendingUploads.forEach(p => { const a = el('span', 'att'); a.innerHTML = icon('file'); a.append(baseName(p)); attachList.append(a); });
    if (uploading) attachList.append(el('span', 'att up', '上传中：' + uploading + '…'));
  }
  $('attachBtn').addEventListener('click', () => { if (!busy) fileInput.click(); });
  fileInput.addEventListener('change', async () => {
    const f = fileInput.files[0]; fileInput.value = '';
    if (!f) return;
    if (f.size > 10 * 1024 * 1024) { toast('文件过大（上限 10 MB）', true); return; }
    try {
      renderAttach(f.name);
      if (!sid) { const s = await api('/api/sessions', {method: 'POST'}); setSid(s.id); loadSessions(); }
      const fd = new FormData(); fd.append('file', f);
      const r = await api('/api/sessions/' + sid + '/files', {method: 'POST', body: fd});
      pendingUploads.push(r.path);
      toast('已上传 ' + r.path);
    } catch (e) { toast(e.message, true); }
    renderAttach();
  });

  /* ---------------- 发送 ---------------- */
  function setBusy(b) {
    busy = b;
    sendBtn.classList.toggle('stop', b);
    sendBtn.innerHTML = icon(b ? 'square' : 'arrow-up');
    sendBtn.title = b ? '停止' : '发送';
    sendBtn.setAttribute('aria-label', sendBtn.title);
    $('newBtn').disabled = b;
  }

  async function send(text) {
    if (busy) return;
    text = (text != null ? text : input.value).trim();
    if (!text) return;
    if (pendingUploads.length) text += '\n\n（我上传了文件：' + pendingUploads.join('、') + '）';
    pendingUploads = []; renderAttach();

    const w = $('welcome'); if (w) w.remove();
    addUser(text);
    input.value = ''; autosize();
    setBusy(true);
    const bot = botMessage();
    bot.typing();
    abortCtl = new AbortController();

    try {
      const resp = await fetch('/api/chat', {
        method: 'POST', credentials: 'same-origin', signal: abortCtl.signal,
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({session_id: sid, message: text, kb_ids: kbSel, model_id: modelSel}),
      });
      if (!resp.ok || !resp.body) {
        let msg = '请求失败（' + resp.status + '）';
        try { const j = await resp.json(); if (j.detail) msg = typeof j.detail === 'string' ? j.detail : msg; } catch {}
        if (resp.status === 429 && msg.startsWith('请求失败')) msg = '请求过于频繁，请稍后再试';
        throw new Error(msg);
      }
      const reader = resp.body.getReader();
      const dec = new TextDecoder();
      let buf = '';
      for (;;) {
        const {value, done} = await reader.read();
        if (done) break;
        buf += dec.decode(value, {stream: true});
        let i;
        while ((i = buf.indexOf('\n\n')) >= 0) {
          const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
          for (const line of chunk.split('\n')) {
            if (!line.startsWith('data: ')) continue;
            let ev; try { ev = JSON.parse(line.slice(6)); } catch { continue; }
            handleEvent(ev, bot);
          }
        }
      }
    } catch (e) {
      if (e.name === 'AbortError') bot.note('已停止生成');
      else bot.error(e.message || String(e));
    } finally {
      setBusy(false);
      bot.done();
      abortCtl = null;
      loadSessions();
      input.focus();
    }
  }

  function handleEvent(ev, bot) {
    switch (ev.type) {
      case 'session':
        if (ev.id !== sid) setSid(ev.id);
        if (ev.model && ev.model.id !== modelSel) {   // 所选模型已被停用，服务端改用了默认模型
          const was = curModel();
          modelSel = ev.model.id; loadModels();
          toast((was ? '「' + was.name + '」已不可用，' : '') + '本次使用 ' + ev.model.name);
        }
        loadSessions(); break;
      case 'reasoning': bot.think(ev.text); break;
      case 'delta': bot.text(ev.text); break;
      case 'tool_call': bot.step(ev); break;
      case 'tool_result': bot.result(ev); break;
      case 'skills_changed': toast('技能已保存 · 可在「技能」页面查看和编辑'); break;
      case 'done': setQuota(ev.quota); break;
      case 'error': bot.error(ev.message || '未知错误'); break;
    }
  }

  function setQuota(q) {
    const box = $('quota');
    if (!q) { box.textContent = ''; return; }
    box.replaceChildren();
    if (q.unlimited) {
      box.append(el('div', 'q-line', '今日对话：不限次数'));
    } else {
      box.append(el('div', 'q-line', `今日剩余 ${q.turns_left} / ${q.turns_per_day} 轮对话`));
      if (q.turns_left === 0 && q.reset_hint) box.append(el('div', 'q-sub', q.reset_hint));
    }
    // 未登录时给出登录引导（额度更高）
    if (!q.logged_in) {
      const b = el('button', 'q-login', q.turns_left === 0 ? '登录获取更多额度' : '登录 / 注册');
      b.type = 'button';
      b.addEventListener('click', () => window.clAuth && window.clAuth.openAuth('login'));
      box.append(b);
    }
  }

  /* ---------------- 登录态（复用全站共享 window.clAuth） ---------------- */
  let lastLoggedIn = null;   // 记录上一次登录态，仅在真正翻转时重置会话

  if (window.clAuth) {
    window.clAuth.onChange(st => {
      setQuota(st.quota);
      if (!st.loaded) return;                      // 尚未拉到登录态，忽略
      const now = !!st.logged_in;
      if (lastLoggedIn === null) { lastLoggedIn = now; return; }  // 首次加载完成：设基线，不重置
      if (now !== lastLoggedIn) {                   // 登录/登出翻转：owner 改变，重置到新对话
        lastLoggedIn = now;
        if (!busy) { setSid(null); newChat(); }
      }
    });
  }

  /* ---------------- 交互绑定 ---------------- */
  const autosize = () => { input.style.height = 'auto'; input.style.height = Math.min(input.scrollHeight, 180) + 'px'; };
  input.addEventListener('input', autosize);
  if (matchMedia('(max-width: 600px)').matches) input.placeholder = '输入你的问题…';
  input.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); }
  });
  sendBtn.addEventListener('click', () => { if (busy) { abortCtl && abortCtl.abort(); } else send(); });
  $('newBtn').addEventListener('click', newChat);

  const closeSide = () => { side.classList.remove('open'); scrim.hidden = true; $('sideToggle').setAttribute('aria-expanded', 'false'); };
  $('sideToggle').addEventListener('click', () => {
    const open = side.classList.toggle('open'); scrim.hidden = !open;
    $('sideToggle').setAttribute('aria-expanded', String(open));
  });
  scrim.addEventListener('click', closeSide);

  /* ---------------- 启动 ---------------- */
  api('/api/health').then(d => {
    const ok = d && d.status === 'ok' && d.key_configured;
    $('dot').classList.toggle('off', !ok);
    $('statusText').textContent = ok ? '在线 · ' + ((curModel() || {}).name || d.model) : '暂无可用模型';
  }).catch(() => { $('dot').classList.add('off'); $('statusText').textContent = '离线'; });

  // 登录态与配额由全站共享 window.clAuth 统一管理（见上方 onChange 订阅）

  loadModels();
  const initial = sidFromHash();
  const params = new URLSearchParams(location.search);
  const kbParam = params.get('kb');
  if (initial) openSession(initial); else { showWelcome(); loadSessions(); }
  // 从知识库页"在对话中使用"跳转过来：新会话预选该知识库
  loadKbs().then(() => {
    if (kbParam && /^[a-f0-9]{32}$/.test(kbParam) && !initial && kbAll.some(k => k.id === kbParam)) {
      kbSel = [kbParam]; renderKb();
      const kb = kbAll.find(k => k.id === kbParam);
      toast('已选择知识库「' + kb.name + '」，直接提问即可');
    }
  });
  const q = params.get('q');
  if (q) {
    input.value = q.slice(0, 2000); autosize();
  }
  if (q || kbParam) history.replaceState(null, '', location.pathname + location.hash);
  input.focus();
})();

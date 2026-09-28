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

  /* ---------------- Markdown（净化后渲染） ---------------- */
  marked.setOptions({gfm: true, breaks: true});
  DOMPurify.addHook('afterSanitizeAttributes', node => {
    if (node.tagName === 'A') { node.setAttribute('target', '_blank'); node.setAttribute('rel', 'noopener noreferrer'); }
    if (node.tagName === 'IMG' && !/^\/api\/files\//.test(node.getAttribute('src') || '')) node.remove(); // 禁止外链图片
  });
  const renderMd = text => DOMPurify.sanitize(marked.parse(text || ''), {FORBID_TAGS: ['style', 'form', 'input'], FORBID_ATTR: ['style']});

  /* ---------------- 文件卡片 ---------------- */
  const FILE_KIND = {
    docx: ['W', '#5a9bff'], doc: ['W', '#5a9bff'], xlsx: ['X', '#55e6b1'], xls: ['X', '#55e6b1'], csv: ['CSV', '#55e6b1'],
    pptx: ['P', '#ffa860'], pdf: ['PDF', '#ff7b7b'], png: ['IMG', '#a97cff'], jpg: ['IMG', '#a97cff'], jpeg: ['IMG', '#a97cff'],
    gif: ['IMG', '#a97cff'], webp: ['IMG', '#a97cff'], md: ['MD', '#68e7ff'], txt: ['TXT', '#8fa0bb'], json: ['{}', '#ffd37a'],
    py: ['PY', '#ffd37a'], html: ['<>', '#ffa860'], zip: ['ZIP', '#8fa0bb'],
  };
  function fileCard(f) {
    const ext = (f.path.split('.').pop() || '').toLowerCase();
    const [label, color] = FILE_KIND[ext] || ['FILE', '#8fa0bb'];
    const a = el('a', 'file');
    a.href = f.url; a.setAttribute('download', baseName(f.path)); a.dataset.path = f.path;
    a.title = '下载 ' + f.path;
    const fi = el('span', 'fi', label); fi.style.background = color;
    const info = el('span');
    info.append(el('div', 'fn', baseName(f.path)), el('div', 'fs', fmtSize(f.size) + (f.path.includes('/') ? ' · ' + f.path : '')));
    a.append(fi, info, el('span', 'dl', '⤓'));
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

    let cur = null, raw = '', raf = 0, typing = null;
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
        clearTyping();
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
      step(call) {
        this.endText(); clearTyping();
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
        st.d.querySelector('.ico').textContent = r.ok ? '✓' : '!';
        st.s.textContent = r.summary || (r.ok ? '完成' : '失败');
        const dt = r.detail || {};
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
      const del = el('button', 's-del', '✕'); del.title = '删除会话'; del.setAttribute('aria-label', '删除会话 ' + s.title);
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
    showWelcome(); loadSessions(); input.focus(); closeSide();
  }

  async function openSession(id) {
    if (busy) return;
    let data;
    try { data = await api('/api/sessions/' + id + '/messages'); }
    catch (e) { toast(e.message, true); newChat(); return; }
    sid = id; pendingUploads = []; renderAttach();
    inner.replaceChildren();
    const existing = new Set(data.existing_files || []);
    let bot = null;
    for (const m of data.messages) {
      if (m.role === 'user') { if (bot) bot.done(); bot = null; addUser(m.content || ''); continue; }
      if (!bot) bot = botMessage();
      if (m.role === 'assistant') {
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

  /* ---------------- 上传 ---------------- */
  function renderAttach(uploading) {
    attachList.replaceChildren();
    pendingUploads.forEach(p => attachList.append(el('span', 'att', '📄 ' + baseName(p))));
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
    sendBtn.textContent = b ? '■' : '➤';
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
        body: JSON.stringify({session_id: sid, message: text}),
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
      case 'session': if (ev.id !== sid) setSid(ev.id); loadSessions(); break;
      case 'delta': bot.text(ev.text); break;
      case 'tool_call': bot.step(ev); break;
      case 'tool_result': bot.result(ev); break;
      case 'skills_changed': toast('技能已保存 · 可在「🧩 技能」页面查看和编辑'); break;
      case 'done': setQuota(ev.quota); break;
      case 'error': bot.error(ev.message || '未知错误'); break;
    }
  }

  function setQuota(q) {
    if (q) $('quota').textContent = '今日剩余 ' + q.turns_left + ' / ' + q.turns_per_day + ' 轮对话';
  }

  /* ---------------- 交互绑定 ---------------- */
  const autosize = () => { input.style.height = 'auto'; input.style.height = Math.min(input.scrollHeight, 180) + 'px'; };
  input.addEventListener('input', autosize);
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
    $('statusText').textContent = ok ? '在线 · ' + (d.model || 'deepseek') : '未配置密钥';
    setQuota(d.quota);
  }).catch(() => { $('dot').classList.add('off'); $('statusText').textContent = '离线'; });

  const initial = sidFromHash();
  if (initial) openSession(initial); else { showWelcome(); loadSessions(); }
  const q = new URLSearchParams(location.search).get('q');
  if (q) {
    input.value = q.slice(0, 2000); autosize();
    history.replaceState(null, '', location.pathname + location.hash);
  }
  input.focus();
})();

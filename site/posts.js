/* 社区帖子前端：按页面上的 data-page 分流到 list / view / edit。
   依赖：icons.js(window.icon)、auth.js(window.clAuth)、marked + DOMPurify（详情/预览渲染）。
   - list：/api/posts 列表 + /api/posts/categories 分类筛选，卡片点进 post-view.html?id=
   - view：/api/posts/{id} 详情，marked+DOMPurify 渲染正文
   - edit：发帖 / 编辑 / 我的帖子（/api/posts, /api/posts/mine, submit, delete） */
(() => {
  'use strict';
  const ic = n => (window.icon ? window.icon(n) : '');
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };

  async function api(path, opts = {}) {
    const r = await fetch(path, { credentials: 'same-origin', ...opts });
    let d = null; try { d = await r.json(); } catch {}
    if (!r.ok) {
      let msg = d && d.detail;
      if (Array.isArray(msg)) msg = msg.map(x => x.msg).join('；');
      throw new Error(msg || ('请求失败（' + r.status + '）'));
    }
    return d;
  }
  const jsend = (path, body, method = 'POST') =>
    api(path, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

  let tt;
  function toast(msg, err) {
    let t = document.getElementById('toast');
    if (!t) { t = el('div', 'toast'); t.id = 'toast'; document.body.appendChild(t); }
    t.textContent = msg; t.classList.toggle('err', !!err); t.classList.add('show');
    clearTimeout(tt); tt = setTimeout(() => t.classList.remove('show'), 3000);
  }

  const STATUS = {
    draft: { label: '草稿', cls: 'draft' },
    pending: { label: '待审核', cls: 'pending' },
    published: { label: '已发布', cls: 'published' },
    rejected: { label: '已驳回', cls: 'rejected' },
  };
  const fmtDate = ts => ts ? new Date(ts * 1000).toLocaleDateString('zh-CN') : '';

  // 分类 → 稳定色（与 cards.js 一致的调色思路）
  const PALETTE = [
    ['#EEF2FF', '#3730A3'], ['#ECFDF5', '#047857'], ['#FFF7ED', '#C2410C'], ['#F0F9FF', '#0369A1'],
    ['#FDF2F8', '#BE185D'], ['#F5F3FF', '#6D28D9'], ['#FEFCE8', '#A16207'], ['#F0FDFA', '#0F766E'],
  ];
  function colorOf(cat) {
    if (!cat) return ['#F4F4F2', '#3F3F46'];
    let h = 0; for (const ch of cat) h = (h * 31 + ch.codePointAt(0)) >>> 0;
    return PALETTE[h % PALETTE.length];
  }
  function initialOf(t) {
    t = (t || '').trim(); if (!t) return '·';
    const w = t.match(/[A-Za-z0-9]+/g);
    if (/^[A-Za-z0-9]/.test(t) && w) return (w[0][0] + (w[1] ? w[1][0] : '')).toUpperCase();
    return [...t][0];
  }

  // 转义为纯文本（当净化库不可用时的安全兜底）
  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function renderMd(md) {
    try {
      // 安全前提：必须有 DOMPurify 才渲染 HTML；否则退回转义纯文本，绝不输出未净化内容
      if (!window.DOMPurify) return '<pre class="md-plain">' + escapeHtml(md) + '</pre>';
      const html = window.marked ? window.marked.parse(md || '') : escapeHtml(md);
      return window.DOMPurify.sanitize(html);
    } catch { return '<pre class="md-plain">' + escapeHtml(md) + '</pre>'; }
  }

  // 渲染富文本正文（Quill 输出的 HTML）。安全前提：必须有 DOMPurify，否则退回转义纯文本。
  function renderHtml(html) {
    try {
      if (!window.DOMPurify) return '<pre class="md-plain">' + escapeHtml(html) + '</pre>';
      return window.DOMPurify.sanitize(html || '', {
        ALLOWED_TAGS: ['p', 'br', 'span', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
          'strong', 'b', 'em', 'i', 'u', 's', 'strike', 'del', 'sub', 'sup',
          'blockquote', 'pre', 'code', 'ol', 'ul', 'li', 'a', 'img', 'hr',
          'table', 'thead', 'tbody', 'tr', 'th', 'td'],
        ALLOWED_ATTR: ['href', 'title', 'target', 'rel', 'src', 'alt', 'width', 'height', 'class'],
        ALLOW_DATA_ATTR: false,
      });
    } catch { return '<pre class="md-plain">' + escapeHtml(html) + '</pre>'; }
  }

  // 优先用 body_html（新帖），否则回退 body_md（旧帖，marked 渲染）
  function renderBody(p) {
    if (p && p.body_html && p.body_html.trim()) return renderHtml(p.body_html);
    return renderMd(p ? p.body_md : '');
  }

  function emptyState(icn, title, text) {
    const e = el('div', 'empty-state');
    const i = el('div', 'es-ic'); i.innerHTML = ic(icn);
    e.append(i, el('h3', null, title), el('p', null, text));
    return e;
  }

  /* ====================== 列表页 ====================== */
  const PAGE = 24;
  async function mountList(box) {
    const params = new URLSearchParams(location.search);
    const state = { q: '', cat: params.get('cat') || '', offset: 0, done: false, loading: false, items: [] };
    box.replaceChildren(el('div', 'cat-loading', '加载中…'));

    let cats = [], pub = {};
    try {
      const cd = await api('/api/posts/categories');
      cats = cd.categories || []; pub = cd.published || {};
    } catch {
      box.replaceChildren(emptyState('alert', '加载失败', '无法获取帖子，请稍后刷新重试。'));
      return;
    }

    const countEl = document.querySelector('[data-count]');
    const totalPub = Object.values(pub).reduce((a, b) => a + b, 0);
    if (countEl) countEl.textContent = totalPub ? '共 ' + totalPub + ' 篇' : '';

    const bar = el('div', 'toolbar');
    const search = el('label', 'search'); search.innerHTML = ic('search');
    const inp = el('input'); inp.type = 'search'; inp.placeholder = '搜索已加载帖子的标题或标签'; inp.setAttribute('aria-label', '搜索');
    search.append(inp, el('kbd', null, '/'));
    inp.addEventListener('input', () => { state.q = inp.value.trim(); render(); });
    bar.append(search);

    const chips = el('div', 'chips');
    const addChip = (value, label, n) => {
      const b = el('button', 'chip'); b.type = 'button'; b.dataset.v = value;
      if (value) { const [bg, fg] = colorOf(value); b.style.setProperty('--cv-bg', bg); b.style.setProperty('--cv-fg', fg); b.append(el('i', 'sw')); }
      b.append(label); if (n != null) b.append(el('span', 'n', String(n)));
      b.addEventListener('click', () => { if (state.cat === value) return; state.cat = value; reset(); });
      chips.append(b);
    };
    addChip('', '全部', totalPub);
    for (const c of cats) addChip(c, c, pub[c] || 0);

    const meta = el('div', 'result'); meta.setAttribute('aria-live', 'polite');
    const grid = el('div', 'cards');
    const moreWrap = el('div', 'more-wrap');
    const moreBtn = el('button', 'iconbtn', '加载更多'); moreBtn.type = 'button';
    moreBtn.addEventListener('click', () => loadMore());
    moreWrap.append(moreBtn);
    box.replaceChildren(bar, chips, meta, grid, moreWrap);

    addEventListener('keydown', e => {
      if (e.key === '/' && !/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName)) { e.preventDefault(); inp.focus(); }
    });

    function card(p) {
      const a = el('a', 'ccard'); a.href = 'post-view.html?id=' + encodeURIComponent(p.id);
      const [bg, fg] = colorOf(p.category);
      const cv = el('div', 'cv'); cv.style.setProperty('--cv-bg', bg); cv.style.setProperty('--cv-fg', fg);
      cv.append(el('span', 'cv-l', initialOf(p.title))); cv.setAttribute('aria-hidden', 'true');
      const body = el('div', 'cc-body');
      const top = el('div', 'cc-top'); top.append(el('h3', null, p.title));
      body.append(top);
      const tags = (p.tags || []).slice(0, 3).join(' · ');
      body.append(el('p', null, tags || ('作者 ' + (p.author_name || '匿名'))));
      const foot = el('div', 'cc-foot');
      if (p.category) { const cat = el('span', 'cat', p.category); cat.style.setProperty('--cv-bg', bg); cat.style.setProperty('--cv-fg', fg); foot.append(cat); }
      const go = el('span', 'go'); go.append(fmtDate(p.published_at || p.updated_at));
      foot.append(go);
      a.append(cv, body, foot);
      return a;
    }

    function reset() {
      state.offset = 0; state.done = false; state.items = []; state.q = ''; inp.value = '';
      const p = new URLSearchParams(); if (state.cat) p.set('cat', state.cat);
      history.replaceState(null, '', location.pathname + (p.toString() ? '?' + p : ''));
      loadMore();
    }

    async function loadMore() {
      if (state.loading || state.done) return;
      state.loading = true; moreBtn.disabled = true; moreBtn.textContent = '加载中…';
      try {
        const qs = new URLSearchParams({ limit: String(PAGE), offset: String(state.offset) });
        if (state.cat) qs.set('category', state.cat);
        const d = await api('/api/posts?' + qs.toString());
        const batch = d.posts || [];
        state.items.push(...batch);
        state.offset += batch.length;
        if (batch.length < PAGE) state.done = true;
      } catch (e) {
        toast(e.message, true);
      } finally {
        state.loading = false; moreBtn.disabled = false; moreBtn.textContent = '加载更多';
        render();
      }
    }

    function render() {
      const q = state.q.toLowerCase();
      const list = q
        ? state.items.filter(p => (p.title || '').toLowerCase().includes(q) || (p.tags || []).join(' ').toLowerCase().includes(q))
        : state.items;
      chips.querySelectorAll('.chip').forEach(b => { const on = b.dataset.v === state.cat; b.classList.toggle('on', on); b.setAttribute('aria-pressed', on); });

      let metaText;
      if (q) metaText = `搜索到 ${list.length} 篇（已加载 ${state.items.length}）`;
      else metaText = `已加载 ${state.items.length} 篇` + (state.done ? '（全部）' : '');
      meta.replaceChildren(document.createTextNode(metaText));

      if (!list.length) {
        grid.replaceChildren(emptyState('search', q ? '没有匹配的帖子' : '还没有帖子', q ? '换个关键词，或加载更多后再搜。' : '成为第一个发帖的人吧。'));
      } else {
        grid.replaceChildren(...list.map(card));
      }
      // 搜索态或已到末尾时隐藏"加载更多"
      moreWrap.style.display = (state.done || q) ? 'none' : '';
    }

    loadMore();
  }

  /* ====================== 详情页 ====================== */
  async function mountView(box) {
    const id = new URLSearchParams(location.search).get('id') || '';
    if (!/^[a-f0-9]{32}$/.test(id)) { box.replaceChildren(emptyState('alert', '帖子不存在', '链接无效。')); return; }
    box.replaceChildren(el('div', 'cat-loading', '加载中…'));
    let p;
    try { p = await api('/api/posts/' + id); }
    catch (e) { box.replaceChildren(emptyState('alert', '无法打开帖子', e.message)); return; }

    const head = el('div', 'post-head');
    const h1 = el('h1', null, p.title); head.append(h1);
    const meta = el('div', 'post-meta');
    meta.append(el('span', null, '作者 ' + (p.author_name || '匿名')));
    if (p.category) meta.append(el('span', 'cat-sm', p.category));
    meta.append(el('span', null, fmtDate(p.published_at || p.updated_at)));
    if (p.mine && p.status) { const s = STATUS[p.status] || {}; meta.append(el('span', 'st-badge ' + (s.cls || ''), s.label || p.status)); }
    head.append(meta);
    if ((p.tags || []).length) {
      const tg = el('div', 'post-tags');
      for (const t of p.tags) tg.append(el('span', 'tagchip', '#' + t));
      head.append(tg);
    }
    if (p.mine) {
      const ops = el('div', 'post-ops');
      const edit = el('a', 'iconbtn'); edit.href = 'post-edit.html?id=' + encodeURIComponent(p.id); edit.innerHTML = ic('file'); edit.append('编辑');
      ops.append(edit);
      if (p.status === 'rejected' && p.reject_reason) {
        head.append(el('div', 'reject-note', '驳回原因：' + p.reject_reason));
      }
      head.append(ops);
    }

    const article = el('article', 'post-body md ql-editor');
    article.innerHTML = renderBody(p);

    box.replaceChildren(head, article);
    document.title = p.title + '｜社区帖子';
  }

  /* ====================== 发帖 / 编辑 / 我的帖子 ====================== */
  async function mountEdit(root) {
    // 需要登录
    const auth = window.clAuth;
    function ensureLogin() {
      if (auth && auth.state.logged_in) return true;
      root.replaceChildren(gate());
      return false;
    }
    function gate() {
      const g = emptyState('user', '请先登录', '登录后才能发帖和管理自己的帖子。');
      const b = el('button', 'iconbtn primary', '登录 / 注册'); b.type = 'button';
      b.addEventListener('click', () => auth && auth.openAuth('login'));
      g.append(b); return g;
    }

    const id = new URLSearchParams(location.search).get('id') || '';
    let cats = [];
    try { cats = (await api('/api/posts/categories')).categories || []; } catch {}

    let form, titleIn, catSel, tagsIn, quillEl, quill, errBox, curId = /^[a-f0-9]{32}$/.test(id) ? id : null, curStatus = 'draft';

    function buildForm() {
      form = el('div', 'editor');
      const row1 = el('div', 'ed-row');
      titleIn = el('input', 'ed-title'); titleIn.maxLength = 120; titleIn.placeholder = '标题';
      row1.append(titleIn);
      const row2 = el('div', 'ed-row ed-row2');
      // 分类下拉
      const sl = el('label', 'sel'); const sel = el('select'); sel.setAttribute('aria-label', '分类');
      sel.append(el('option', null, '选择分类…'));
      sel.firstChild.value = '';
      for (const c of cats) { const o = el('option', null, c); o.value = c; sel.append(o); }
      sl.append(sel); catSel = sel;
      // 标签输入
      tagsIn = el('input', 'ed-tags'); tagsIn.maxLength = 160; tagsIn.placeholder = '标签，用逗号分隔（最多 8 个）';
      row2.append(sl, tagsIn);
      // 正文：Quill 富文本编辑器（输出 HTML）
      const edcols = el('div', 'ed-cols');
      const editorWrap = el('div', 'ed-quill-wrap');
      quillEl = el('div', 'ed-quill');
      editorWrap.append(quillEl);
      edcols.append(editorWrap);
      errBox = el('div', 'ed-err');
      const actions = el('div', 'ed-actions');
      const save = el('button', 'iconbtn', '保存草稿'); save.type = 'button';
      save.addEventListener('click', () => doSave(false));
      const submit = el('button', 'iconbtn primary', '保存并提交审核'); submit.type = 'button';
      submit.addEventListener('click', () => doSave(true));
      const back = el('a', 'iconbtn ghost'); back.href = 'posts.html'; back.innerHTML = ic('arrow-left'); back.append('返回');
      actions.append(back, el('span', 'spacer'), save, submit);
      form.append(row1, row2, edcols, errBox, actions);
      return form;
    }

    function collect() {
      const tags = tagsIn.value.split(/[,，]/).map(s => s.trim()).filter(Boolean).slice(0, 8);
      // Quill 空内容时 root.innerHTML 为 '<p><br></p>'，视为空
      let html = quill ? quill.root.innerHTML : '';
      if (html === '<p><br></p>') html = '';
      return { title: titleIn.value.trim(), category: catSel.value, tags, body_md: '', body_html: html };
    }

    async function doSave(submitAfter) {
      errBox.textContent = '';
      const data = collect();
      if (!data.title) { errBox.textContent = '标题不能为空'; return; }
      if (!data.category) { errBox.textContent = '请选择分类'; return; }
      try {
        let p;
        if (curId) p = await jsend('/api/posts/' + curId, data, 'PUT');
        else { p = await jsend('/api/posts', data); curId = p.id; history.replaceState(null, '', 'post-edit.html?id=' + p.id); }
        curStatus = p.status;
        if (submitAfter) {
          p = await jsend('/api/posts/' + curId + '/submit', {});
          curStatus = p.status;
          toast('已提交审核，等待管理员通过');
        } else {
          toast('已保存草稿');
        }
        loadMine();
      } catch (e) { errBox.textContent = e.message; }
    }

    async function fillForEdit() {
      if (!curId) return;
      try {
        const p = await api('/api/posts/' + curId);
        titleIn.value = p.title || ''; catSel.value = cats.includes(p.category) ? p.category : '';
        tagsIn.value = (p.tags || []).join(', ');
        // 优先载入 body_html（新帖）；旧帖只有 body_md 时转成 HTML 载入
        const html = (p.body_html && p.body_html.trim()) ? renderHtml(p.body_html) : renderMd(p.body_md);
        if (quill) quill.setContents(quill.clipboard.convert({ html: html || '<p></p>' }), 'silent');
        curStatus = p.status || 'draft';
      } catch (e) { toast(e.message, true); }
    }

    // 我的帖子列表
    const mineBox = el('div', 'mine-list');
    async function loadMine() {
      try {
        const d = await api('/api/posts/mine');
        const list = d.posts || [];
        mineBox.replaceChildren(el('div', 'mine-title', '我的帖子（' + list.length + '）'));
        if (!list.length) { mineBox.append(el('div', 'empty', '还没有帖子，在左侧开始写第一篇。')); return; }
        for (const p of list) {
          const row = el('div', 'mine-row');
          const s = STATUS[p.status] || {};
          const a = el('a', 'mine-link'); a.href = 'post-edit.html?id=' + p.id; a.textContent = p.title || '(无标题)';
          a.addEventListener('click', e => { e.preventDefault(); curId = p.id; history.replaceState(null, '', 'post-edit.html?id=' + p.id); fillForEdit(); });
          const badge = el('span', 'st-badge ' + (s.cls || ''), s.label || p.status);
          const info = el('div', 'mine-info'); info.append(a, badge);
          if (p.status === 'rejected' && p.reject_reason) info.append(el('div', 'mine-reason', '驳回：' + p.reject_reason));
          const del = el('button', 'iconbtn sq'); del.type = 'button'; del.innerHTML = ic('trash'); del.title = '删除';
          del.addEventListener('click', async () => {
            if (!confirm('确定删除《' + (p.title || '') + '》？')) return;
            try { await api('/api/posts/' + p.id, { method: 'DELETE' }); toast('已删除'); if (curId === p.id) { curId = null; titleIn.value = ''; if (quill) quill.setText(''); tagsIn.value = ''; catSel.value = ''; } loadMine(); }
            catch (e) { toast(e.message, true); }
          });
          const view = el('a', 'iconbtn sq'); view.href = 'post-view.html?id=' + p.id; view.innerHTML = ic('arrow-up-right'); view.title = '查看';
          row.append(info, view, del);
          mineBox.append(row);
        }
      } catch (e) { mineBox.replaceChildren(el('div', 'empty', '加载失败：' + e.message)); }
    }

    // 初始化 Quill 富文本编辑器 + 本地图片上传（工具栏 / 粘贴 / 拖拽）
    function initQuill() {
      if (!window.Quill || !quillEl) return;
      const toolbar = [
        [{ header: [1, 2, 3, false] }],
        ['bold', 'italic', 'underline', 'strike'],
        [{ list: 'ordered' }, { list: 'bullet' }],
        [{ indent: '-1' }, { indent: '+1' }],
        [{ align: [] }],
        ['blockquote', 'code-block'],
        ['link', 'image'],
        ['clean'],
      ];
      quill = new window.Quill(quillEl, {
        theme: 'snow',
        placeholder: '开始写正文…支持标题、列表、引用、代码、图片等（可直接粘贴或拖拽图片）',
        modules: { toolbar: { container: toolbar, handlers: { image: pickImage } } },
      });
      bindImageDropPaste();
      // 拦截粘贴的 HTML 中内联的 base64 图片：不写入正文，改为异步上传后插入
      quill.clipboard.addMatcher('IMG', (node, delta) => {
        const src = (node.getAttribute && node.getAttribute('src')) || '';
        if (/^data:image\//i.test(src)) {
          const file = dataUrlToFile(src);
          if (file) { setTimeout(() => insertImage(file, null), 0); }
          return { ops: [] };   // 丢弃内联 base64，避免撑爆正文
        }
        return delta;           // 普通 URL 图片保留
      });
    }

    // data:URL → File（用于把粘贴的 base64 图片转存上传）
    function dataUrlToFile(dataUrl) {
      try {
        const m = /^data:(image\/[a-z]+);base64,(.*)$/i.exec(dataUrl);
        if (!m) return null;
        const mime = m[1].toLowerCase();
        const bin = atob(m[2]);
        const arr = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
        const ext = mime.split('/')[1].replace('jpeg', 'jpg');
        return new File([arr], 'pasted.' + ext, { type: mime });
      } catch { return null; }
    }

    const IMG_TYPES = new Set(['image/png', 'image/jpeg', 'image/gif', 'image/webp']);

    // 校验并上传单张图片，返回其 URL（失败抛错）
    async function uploadImage(file) {
      if (!IMG_TYPES.has(file.type)) throw new Error('仅支持 PNG / JPEG / GIF / WebP 图片');
      const fd = new FormData();
      fd.append('file', file);
      const r = await fetch('/api/posts/images', { method: 'POST', credentials: 'same-origin', body: fd });
      let d = null; try { d = await r.json(); } catch {}
      if (!r.ok) throw new Error((d && d.detail) || ('上传失败（' + r.status + '）'));
      return d.url;
    }

    // 上传并在指定位置插入图片；index 为 null 时插到当前光标/末尾
    async function insertImage(file, index) {
      try {
        toast('图片上传中…');
        const url = await uploadImage(file);
        let idx = index;
        if (idx == null) { const range = quill.getSelection(true); idx = range ? range.index : quill.getLength(); }
        quill.insertEmbed(idx, 'image', url, 'user');
        quill.setSelection(idx + 1, 0);
        toast('图片已插入');
      } catch (e) { toast(e.message, true); }
    }

    // 工具栏图片按钮：选本地文件 → 上传 → 在光标处插入
    function pickImage() {
      const input = document.createElement('input');
      input.type = 'file';
      input.accept = 'image/png,image/jpeg,image/gif,image/webp';
      input.onchange = () => { const f = input.files && input.files[0]; if (f) insertImage(f, null); };
      input.click();
    }

    // 粘贴 / 拖拽图片 → 拦截默认（避免 base64 内联）→ 走上传
    function bindImageDropPaste() {
      const rootEl = quill.root;
      rootEl.addEventListener('paste', (e) => {
        const items = (e.clipboardData && e.clipboardData.items) || [];
        const files = [];
        for (const it of items) {
          if (it.kind === 'file' && it.type && it.type.indexOf('image/') === 0) {
            const f = it.getAsFile(); if (f) files.push(f);
          }
        }
        if (!files.length) return;        // 非图片粘贴走 Quill 默认处理
        e.preventDefault();               // 阻止默认内联 base64
        files.forEach(f => insertImage(f, null));
      });
      rootEl.addEventListener('drop', (e) => {
        const dt = e.dataTransfer;
        const files = dt && dt.files ? Array.from(dt.files).filter(f => f.type && f.type.indexOf('image/') === 0) : [];
        if (!files.length) return;
        e.preventDefault();
        // 若落点可定位，则插到落点，否则插到末尾
        let idx = null;
        if (document.caretRangeFromPoint) {
          const rng = document.caretRangeFromPoint(e.clientX, e.clientY);
          if (rng && quill.root.contains(rng.startContainer)) {
            const blot = window.Quill.find(rng.startContainer);
            if (blot && quill.getIndex) { try { idx = quill.getIndex(blot) + rng.startOffset; } catch { idx = null; } }
          }
        }
        files.forEach((f, i) => insertImage(f, idx == null ? null : idx + i));
      });
    }

    function render() {
      if (!ensureLogin()) return;
      const wrap2 = el('div', 'edit-layout');
      wrap2.append(buildForm(), mineBox);
      root.replaceChildren(wrap2);
      initQuill();
      fillForEdit();
      loadMine();
    }

    if (auth) auth.onChange(() => render());
    else render();
  }

  /* ====================== 分流 ====================== */
  document.addEventListener('DOMContentLoaded', () => {
    const page = document.body.getAttribute('data-page');
    const box = document.querySelector('[data-posts]');
    if (!box) return;
    if (page === 'posts-list') mountList(box);
    else if (page === 'posts-view') mountView(box);
    else if (page === 'posts-edit') mountEdit(box);
  });
})();

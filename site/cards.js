/* 展示页目录组件：从 /api/cards?section=xxx 拉取卡片，前端完成搜索 / 分类 / 状态筛选、排序与网格/列表切换。
   页面用 <div class="catalog" data-section="tools|works|courses"></div> 声明区域。
   筛选条件同步到 URL（?q=&cat=&status=&sort=），视图偏好存 localStorage。 */
(() => {
  'use strict';

  // URL 规范化：
  //  - 完整 URL（http:// 或 https://）→ 原样
  //  - "//host..." 协议相对 → 原样
  //  - ":18081" 或 "18081"（纯端口）→ 当前主机 + 该端口
  //  - "/xxx" 相对路径 → 站内
  //  - 其他非空字符串 → 当作 https:// 前缀补全
  window.normalizeCardUrl = function (raw) {
    const s = (raw || '').trim();
    if (!s) return '';
    if (/^https?:\/\//i.test(s)) return s;
    if (/^\/\//.test(s)) return s;
    const m = s.match(/^:?(\d{2,5})$/);          // 纯端口
    if (m) return location.protocol + '//' + location.hostname + ':' + m[1] + '/';
    if (s.startsWith('/')) return s;             // 站内相对路径
    return 'https://' + s;                       // 兜底：域名/路径
  };

  const ic = n => (window.icon ? window.icon(n) : '');
  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  // 分类 → 色块（同一分类始终同色）
  const PALETTE = [
    ['#EEF2FF', '#3730A3'], ['#ECFDF5', '#047857'], ['#FFF7ED', '#C2410C'], ['#F0F9FF', '#0369A1'],
    ['#FDF2F8', '#BE185D'], ['#F5F3FF', '#6D28D9'], ['#FEFCE8', '#A16207'], ['#F0FDFA', '#0F766E'],
  ];
  const NEUTRAL = ['#F4F4F2', '#3F3F46'];
  function colorOf(cat) {
    if (!cat || cat === '__none') return NEUTRAL;
    let h = 0;
    for (const ch of cat) h = (h * 31 + ch.codePointAt(0)) >>> 0;
    return PALETTE[h % PALETTE.length];
  }
  // 首字母：英文取前两个字母的首字母（如 "Sales Agent" → SA），中文取首字
  function initialOf(title) {
    const t = (title || '').trim();
    if (!t) return '·';
    const words = t.match(/[A-Za-z0-9]+/g);
    if (/^[A-Za-z0-9]/.test(t) && words) return (words[0][0] + (words[1] ? words[1][0] : '')).toUpperCase();
    return [...t][0];
  }

  // 高亮搜索词（纯 DOM 构建，不拼 HTML）
  function highlight(text, q) {
    const frag = document.createDocumentFragment();
    if (!q) { frag.append(text); return frag; }
    const lower = text.toLowerCase(), ql = q.toLowerCase();
    let i = 0, j;
    while ((j = lower.indexOf(ql, i)) >= 0) {
      if (j > i) frag.append(text.slice(i, j));
      frag.append(el('mark', null, text.slice(j, j + q.length)));
      i = j + q.length;
    }
    frag.append(text.slice(i));
    return frag;
  }

  function cover(c, size) {
    const [bg, fg] = colorOf(c.category);
    const cv = el('div', 'cv' + (size ? ' ' + size : ''));
    cv.style.setProperty('--cv-bg', bg);
    cv.style.setProperty('--cv-fg', fg);
    cv.setAttribute('aria-hidden', 'true');
    cv.append(el('span', 'cv-l', initialOf(c.title)));
    return cv;
  }

  function renderCard(c, q) {
    const href = window.normalizeCardUrl(c.url);
    const card = el(href ? 'a' : 'div', 'ccard');
    if (href) { card.href = href; card.target = '_blank'; card.rel = 'noopener'; }
    else card.classList.add('soon');

    const body = el('div', 'cc-body');
    const h = el('h3'); h.append(highlight(c.title || '', q));
    const top = el('div', 'cc-top'); top.append(h);
    if (c.tag) top.append(el('span', 'st', c.tag));
    const p = el('p'); p.append(highlight(c.description || '', q));
    body.append(top, p);

    const foot = el('div', 'cc-foot');
    if (c.category) {
      const [bg, fg] = colorOf(c.category);
      const cat = el('span', 'cat', c.category);
      cat.style.setProperty('--cv-bg', bg); cat.style.setProperty('--cv-fg', fg);
      foot.append(cat);
    }
    const go = el('span', 'go');
    if (href) { go.append('进入'); const a = el('span', 'arrow'); a.innerHTML = ic('arrow-up-right'); go.append(a); }
    else go.textContent = '敬请期待';
    foot.append(go);

    card.append(cover(c), body, foot);
    return card;
  }

  function select(label, options, value, onChange) {
    const wrap = el('label', 'sel');
    wrap.append(el('span', 'sr', label));
    const s = el('select');
    s.setAttribute('aria-label', label);
    for (const [v, t] of options) { const o = el('option', null, t); o.value = v; s.append(o); }
    s.value = value;
    s.addEventListener('change', () => onChange(s.value));
    wrap.append(s);
    return wrap;
  }

  async function mount(box) {
    const section = box.getAttribute('data-section');
    const params = new URLSearchParams(location.search);
    const VIEW_KEY = 'cl_catalog_view';
    const state = {
      q: params.get('q') || '',
      cat: params.get('cat') || '',
      status: params.get('status') || '',
      sort: params.get('sort') || 'default',
      view: localStorage.getItem(VIEW_KEY) === 'list' ? 'list' : 'grid',
    };

    box.replaceChildren(el('div', 'cat-loading', '加载中…'));
    let cards = [];
    try {
      const r = await fetch('/api/cards?section=' + encodeURIComponent(section), {credentials: 'same-origin'});
      if (!r.ok) throw new Error(r.status);
      cards = ((await r.json()) || {}).cards || [];
    } catch {
      box.replaceChildren(emptyState('alert', '加载失败', '无法获取数据，请稍后刷新重试。'));
      return;
    }

    const countEl = document.querySelector('[data-count]');
    if (countEl) countEl.textContent = cards.length ? '共 ' + cards.length + ' 项' : '';

    if (!cards.length) {
      box.replaceChildren(emptyState('sparkle', '内容筹备中', '这里很快会有新内容，敬请关注。'));
      return;
    }

    const cats = countBy(cards, 'category');
    const tags = countBy(cards, 'tag');
    if (state.cat && !cats.has(state.cat)) state.cat = '';
    if (state.status && !tags.has(state.status)) state.status = '';

    /* ---------- 工具栏 ---------- */
    const bar = el('div', 'toolbar');
    const search = el('label', 'search');
    search.innerHTML = ic('search');
    const inp = el('input');
    inp.type = 'search'; inp.placeholder = '搜索名称或描述'; inp.value = state.q;
    inp.setAttribute('aria-label', '搜索');
    search.append(inp, el('kbd', null, '/'));
    let t;
    inp.addEventListener('input', () => { clearTimeout(t); t = setTimeout(() => { state.q = inp.value.trim(); update(); }, 120); });

    const right = el('div', 'tb-right');
    if (tags.size) {
      right.append(select('状态', [['', '全部状态'], ...[...tags].map(([k, n]) => [k, k + '（' + n + '）'])], state.status, v => { state.status = v; update(); }));
    }
    right.append(select('排序', [['default', '默认排序'], ['updated', '最近更新'], ['name', '按名称']], state.sort, v => { state.sort = v; update(); }));
    const seg = el('div', 'seg');
    seg.setAttribute('role', 'group'); seg.setAttribute('aria-label', '视图');
    const vb = {};
    for (const [v, icn, label] of [['grid', 'grid', '网格视图'], ['list', 'menu', '列表视图']]) {
      const b = el('button'); b.type = 'button'; b.innerHTML = ic(icn); b.title = label; b.setAttribute('aria-label', label);
      b.addEventListener('click', () => { state.view = v; localStorage.setItem(VIEW_KEY, v); update(); });
      vb[v] = b; seg.append(b);
    }
    right.append(seg);
    bar.append(search, right);

    /* ---------- 分类标签 ---------- */
    const chips = el('div', 'chips');
    chips.setAttribute('role', 'group'); chips.setAttribute('aria-label', '分类');
    const chipBtns = [];
    const addChip = (value, label, n) => {
      const b = el('button', 'chip'); b.type = 'button'; b.dataset.v = value;
      if (value) {
        const [bg, fg] = colorOf(value);
        b.style.setProperty('--cv-bg', bg); b.style.setProperty('--cv-fg', fg);
        b.append(el('i', 'sw'));
      }
      b.append(label, el('span', 'n', String(n)));
      b.addEventListener('click', () => { state.cat = state.cat === value ? '' : value; update(); });
      chipBtns.push(b); chips.append(b);
    };
    if (cats.size) {
      addChip('', '全部', cards.length);
      for (const [k, n] of cats) addChip(k, k, n);
      const none = cards.filter(c => !c.category).length;
      if (none) addChip('__none', '未分类', none);
    }

    const meta = el('div', 'result');
    meta.setAttribute('aria-live', 'polite');
    const grid = el('div', 'cards');

    box.replaceChildren(bar, ...(cats.size ? [chips] : []), meta, grid);

    // 按 "/" 聚焦搜索框
    addEventListener('keydown', e => {
      if (e.key === '/' && !/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName)) { e.preventDefault(); inp.focus(); }
      if (e.key === 'Escape' && document.activeElement === inp && inp.value) { inp.value = ''; state.q = ''; update(); }
    });

    function update() {
      const q = state.q.toLowerCase();
      let list = cards.filter(c =>
        (!state.cat || (state.cat === '__none' ? !c.category : c.category === state.cat)) &&
        (!state.status || c.tag === state.status) &&
        (!q || (c.title || '').toLowerCase().includes(q) || (c.description || '').toLowerCase().includes(q)));
      if (state.sort === 'updated') list = [...list].sort((a, b) => (b.updated_at || 0) - (a.updated_at || 0));
      if (state.sort === 'name') list = [...list].sort((a, b) => (a.title || '').localeCompare(b.title || '', 'zh-CN'));

      chipBtns.forEach(b => { const on = b.dataset.v === state.cat; b.classList.toggle('on', on); b.setAttribute('aria-pressed', on); });
      for (const v in vb) { const on = v === state.view; vb[v].classList.toggle('on', on); vb[v].setAttribute('aria-pressed', on); }
      grid.classList.toggle('list', state.view === 'list');

      const filtered = state.q || state.cat || state.status;
      meta.replaceChildren();
      meta.append(filtered ? `显示 ${list.length} / ${cards.length} 项` : `${cards.length} 项`);
      if (filtered) {
        const clr = el('button', 'clear', '清除筛选'); clr.type = 'button';
        clr.addEventListener('click', reset); meta.append(clr);
      }

      grid.replaceChildren(...(list.length ? list.map(c => renderCard(c, state.q)) : [noMatch()]));
      syncUrl();
    }

    function reset() { state.q = ''; state.cat = ''; state.status = ''; inp.value = ''; const s = bar.querySelector('select'); if (s && tags.size) s.value = ''; update(); }
    function noMatch() {
      const e = emptyState('search', '没有匹配的结果', '换个关键词，或调整分类与状态。');
      const b = el('button', 'iconbtn', '清除筛选'); b.type = 'button'; b.addEventListener('click', reset); e.append(b);
      return e;
    }
    function syncUrl() {
      const p = new URLSearchParams();
      if (state.q) p.set('q', state.q);
      if (state.cat) p.set('cat', state.cat);
      if (state.status) p.set('status', state.status);
      if (state.sort !== 'default') p.set('sort', state.sort);
      const s = p.toString();
      history.replaceState(null, '', location.pathname + (s ? '?' + s : '') + location.hash);
    }

    update();
  }

  function countBy(cards, key) {
    const m = new Map();
    for (const c of cards) if (c[key]) m.set(c[key], (m.get(c[key]) || 0) + 1);
    return new Map([...m].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], 'zh-CN')));
  }

  function emptyState(icn, title, text) {
    const e = el('div', 'empty-state');
    const i = el('div', 'es-ic'); i.innerHTML = ic(icn);
    e.append(i, el('h3', null, title), el('p', null, text));
    return e;
  }

  document.querySelectorAll('.catalog[data-section]').forEach(mount);
})();

/* 内置课程前端：
   - courses.html：内置课程入口（AI入门 / AI知识普及），挂载点 [data-builtin-courses]
   - course.html?id=<课程>：课程画布（阶段色带 + 路线 + 课程卡抽屉 + 工具箱）
   - lesson.html?course=<课程>&lesson=<课>[&mode=text|video][&login=1]：课程首屏 + 三种学法
   访问规则：未登录只能看画布和课程首屏；正文 / 视频 / 互动课 / 工具卡要点 / 进度需登录。
   依赖：icons.js、auth.js（window.clAuth）；lesson.html 另需 marked + DOMPurify 渲染正文。
   所有课程文本都通过 textContent 写入；正文 Markdown 经 DOMPurify 净化后才插入。 */
(() => {
  'use strict';

  const ic = n => (window.icon ? window.icon(n) : '');
  const qs = new URLSearchParams(location.search);
  const MODE_META = {
    video: { icon: 'play', name: '看视频', verb: '看完' },
    text: { icon: 'book', name: '读正文', verb: '读完' },
    interactive: { icon: 'target', name: '互动课', verb: '学完' },
  };
  const MODE_ORDER = ['video', 'text', 'interactive'];

  /* ---------- 小工具 ---------- */
  function h(tag, attrs, ...kids) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === 'class') e.className = v;
      else if (k === 'html') e.innerHTML = v;            // 仅用于内置图标 SVG
      else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
      else e.setAttribute(k, v === true ? '' : v);
    }
    for (const c of kids.flat()) if (c != null && c !== false) e.append(c);
    return e;
  }

  async function api(path, opts = {}) {
    const r = await fetch(path, { credentials: 'same-origin', ...opts });
    let d = null; try { d = await r.json(); } catch {}
    if (!r.ok) { const e = new Error((d && d.detail) || ('请求失败（' + r.status + '）')); e.status = r.status; throw e; }
    return d;
  }
  const putProgress = (cid, lid, body) => api(`/api/courses/${encodeURIComponent(cid)}/lessons/${encodeURIComponent(lid)}/progress`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

  const lessonUrl = (cid, lid, mode) => `lesson.html?course=${encodeURIComponent(cid)}&lesson=${encodeURIComponent(lid)}` + (mode ? '&mode=' + mode : '');
  const interactiveUrl = (cid, lid) => `/api/courses/${encodeURIComponent(cid)}/lessons/${encodeURIComponent(lid)}/interactive`;
  const modeUrl = (cid, lid, m) => (m === 'interactive' ? interactiveUrl(cid, lid) : lessonUrl(cid, lid, m));
  const minutesText = m => (m.minutes ? `约 ${m.minutes} 分钟` : '');

  const isDone = (prog, lid) => !!(prog && prog[lid] && Object.values(prog[lid]).some(x => x.done));
  const modeDone = (prog, lid, m) => !!(prog && prog[lid] && prog[lid][m] && prog[lid][m].done);
  function currentLesson(c) {
    // 推荐的下一课：第一节还没完成的课；全部完成则为最后一课
    if (!c.progress) return null;
    return c.lessons.find(l => !isDone(c.progress, l.id)) || null;
  }
  const login = () => window.clAuth && window.clAuth.openAuth('login');
  function onAuth(fn) {
    // auth.js 加载完登录态后回调；之后每次登录 / 退出都会再次回调
    let last;
    if (!window.clAuth) { fn(false); return; }
    window.clAuth.onChange(st => { if (!st.loaded) return; if (last === st.logged_in) return; last = st.logged_in; fn(st.logged_in); });
  }

  /* ---------- 工具卡弹窗 ---------- */
  function openTool(t, c) {
    const from = c.lessons.find(l => l.id === t.lesson);
    const dlg = h('dialog', { class: 'tdlg', 'aria-labelledby': 'tdlg-t' });
    const got = c.progress && isDone(c.progress, t.lesson);
    dlg.append(
      h('h3', { id: 'tdlg-t' }, t.name),
      h('p', { class: 't-from' }, from ? `来自 第${from.no}课 · ${from.title}` + (c.progress ? (got ? ' · 已收集' : ' · 学完这节课即可收集') : '') : ''),
      t.points ? h('ul', null, t.points.map(p => h('li', null, p)))
        : h('p', null, '登录后可以查看工具卡的完整内容。'),
      h('div', { class: 'row' },
        !t.points ? h('button', { class: 'iconbtn primary', type: 'button', onclick: () => { dlg.close(); login(); } }, '登录 / 注册') : null,
        from ? h('a', { class: 'iconbtn', href: lessonUrl(c.id, from.id) }, '去这节课') : null,
        h('button', { class: 'iconbtn', type: 'button', onclick: () => dlg.close() }, '关闭')));
    dlg.addEventListener('close', () => dlg.remove());
    document.body.append(dlg);
    dlg.showModal();
  }

  function modeList(c, l, logged, { compact } = {}) {
    // 三种学法：可用的按钮；未登录点击即弹登录；制作中的灰显
    const rec = l.recommended;
    return h('div', { class: 'modes' }, MODE_ORDER.map(m => {
      const info = l.modes[m], meta = MODE_META[m];
      const done = logged && modeDone(c.progress, l.id, m);
      const sub = !info.available ? '制作中' : [minutesText(info), m === rec ? '推荐' : '', done ? '已' + meta.verb : '', !logged ? '登录后学习' : ''].filter(Boolean).join(' · ');
      const kids = [h('span', { class: 'm-ic', html: ic(done ? 'check' : meta.icon) }), h('span', { class: 'm-tx' }, h('b', null, meta.name), h('small', null, sub))];
      const cls = 'mode' + (info.available && m === rec ? ' rec' : '') + (!info.available ? ' na' : '') + (done ? ' done' : '');
      if (!info.available) return h('div', { class: cls, 'aria-disabled': 'true' }, kids);
      if (!logged) return h('button', { class: cls, type: 'button', onclick: login }, kids);
      return h('a', { class: cls, href: modeUrl(c.id, l.id, m) }, kids);
    }));
  }

  function changeChip(l) {
    if (!l.change || l.change.length < 2) return null;
    return h('span', { class: 'lchg' }, l.change[0], h('span', { html: ic('arrow-right') }), l.change[1]);
  }

  /* =====================================================================
   * 1. 课程列表页：内置课程
   * ===================================================================== */
  async function mountList(box) {
    let list = [];
    try { list = (await api('/api/courses')).courses || []; }
    catch { box.replaceChildren(h('p', { class: 'center-msg' }, '内置课程加载失败，请稍后刷新重试。')); return; }
    if (!list.length) { box.remove(); return; }
    box.replaceChildren(...list.map(c => {
      const pub = c.status === 'published';
      const meta = pub ? [`${c.lesson_count} 节课`, c.stage_count ? `${c.stage_count} 个阶段` : '', c.audience].filter(Boolean)
        : [c.audience].filter(Boolean);
      return h('a', { class: 'bcard' + (pub ? '' : ' soon'), href: 'course.html?id=' + encodeURIComponent(c.id) },
        h('div', { class: 'b-top' }, h('h3', null, c.title), pub ? h('span', { class: 'pill ok' }, '已上线') : h('span', { class: 'pill warn' }, '筹备中')),
        h('span', { class: 'b-go', html: ic('arrow-up-right') }),
        h('p', { class: 'b-sub' }, c.subtitle),
        c.summary ? h('p', { class: 'b-sub' }, c.summary) : null,
        !pub && c.topics.length ? h('ul', { class: 'b-topics', 'aria-label': '规划内容' }, c.topics.slice(0, 6).map(t => h('li', null, t))) : null,
        h('div', { class: 'b-meta' }, meta.map(m => h('span', null, m))));
    }));
  }

  /* =====================================================================
   * 2. 课程画布
   * ===================================================================== */
  async function mountCanvas(root) {
    const cid = qs.get('id') || 'ai-intro';
    let c, sheetLid = null;

    async function load() {
      try { c = await api('/api/courses/' + encodeURIComponent(cid)); }
      catch (e) {
        root.replaceChildren(h('div', { class: 'center-msg' }, e.status === 404 ? '课程不存在。' : '课程加载失败，请稍后刷新重试。', h('br'), h('a', { href: 'courses.html' }, '返回课程列表')));
        return;
      }
      document.title = c.title + '｜AI 课程｜Chris Li · AI Agent';
      render();
      if (sheetLid) openSheet(sheetLid);
    }

    function render() {
      const crumb = h('div', { class: 'crumb' }, h('a', { href: 'courses.html' }, 'AI 课程'), ' / ', c.title);
      if (c.status !== 'published') { root.replaceChildren(crumb, renderComing()); return; }

      const logged = !!c.logged_in, prog = c.progress;
      const doneN = logged ? c.lessons.filter(l => isDone(prog, l.id)).length : 0;
      const toolN = logged ? c.tools.filter(t => isDone(prog, t.lesson)).length : 0;
      const cur = currentLesson(c);
      const allDone = logged && !cur;

      let cta;
      if (!logged) cta = h('a', { class: 'iconbtn primary cv-cta', href: lessonUrl(c.id, c.lessons[0].id) }, '从第1课开始', h('span', { html: ic('arrow-right') }));
      else if (allDone) cta = h('span', { class: 'pill ok' }, '🎉 6 节课已全部完成');
      else cta = h('a', { class: 'iconbtn primary cv-cta', href: lessonUrl(c.id, cur.id) }, doneN ? `继续学习：第${cur.no}课` : '开始学习：第1课', h('span', { html: ic('arrow-right') }));

      const head = h('div', { class: 'cv-head' },
        h('div', null, h('h2', null, c.title), h('p', null, c.subtitle)),
        h('div', { class: 'cv-side' },
          cta,
          h('div', { class: 'cv-prog' }, logged
            ? [h('b', null, `${doneN}/${c.lessons.length}`), ' 节课 · 工具卡 ', h('b', null, `${toolN}/${c.tools.length}`)]
            : `${c.lessons.length} 节课 · ${c.stages.length} 个阶段 · ${c.tools.length} 张工具卡`)));

      const loginbar = logged ? null : h('div', { class: 'loginbar', role: 'note' },
        h('span', { html: ic('shield') }),
        h('span', null, '现在可以浏览课程地图和每节课的介绍。登录后即可看视频、读正文、上互动课，并记录你的学习进度。'),
        h('span', { class: 'sp' }),
        h('button', { class: 'iconbtn primary', type: 'button', onclick: login }, '登录 / 注册'));

      // 路线：阶段色带 + 节点
      const firstVisitKey = 'cl_course_intro_' + c.id;   // 仅记录"是否看过引导动画"，与学习进度无关
      const intro = !localStorage.getItem(firstVisitKey);
      let order = 0;
      const stages = c.stages.map((s, si) => {
        const ls = c.lessons.filter(l => l.stage === s.id);
        const lit = logged && ls.length && ls.every(l => isDone(prog, l.id));
        return h('section', { class: 'stage' + (lit ? ' lit lit-end' : ''), 'data-tone': String(si % 4), 'aria-label': `第${si + 1}阶段：${s.name}` },
          h('div', { class: 'st-h' }, h('span', { class: 'st-n' }, String(si + 1).padStart(2, '0')), h('span', { class: 'st-name' }, s.name)),
          h('p', { class: 'st-q' }, s.question),
          h('div', { class: 'st-nodes' }, ls.map(l => {
            const done = logged && isDone(prog, l.id), here = cur && cur.id === l.id;
            const n = h('button', {
              class: 'node' + (done ? ' done lit' : '') + (here ? ' here' : '') + (sheetLid === l.id ? ' sel' : ''),
              type: 'button', 'data-lid': l.id, 'aria-haspopup': 'dialog',
              'aria-label': `第${l.no}课：${l.title}${done ? '（已完成）' : here ? '（下一课）' : ''}`,
              onclick: () => openSheet(l.id),
            },
            here ? h('span', { class: 'here-tag' }, '你在这里') : null,
            h('div', { class: 'n-top' }, h('span', { class: 'n-no' }, `第${l.no}课`),
              done ? h('span', { class: 'pill ok' }, '✓ 已完成') : null),
            h('div', { class: 'n-title' }, l.title),
            h('div', { class: 'n-modes', 'aria-hidden': 'true' }, MODE_ORDER.filter(m => l.modes[m].available).map(m =>
              h('span', { class: modeDone(prog, l.id, m) ? 'on' : '', title: MODE_META[m].name, html: ic(MODE_META[m].icon) }))),
            l.change.length >= 2 ? h('div', { class: 'n-chg' }, l.change[0], h('span', { html: ic('arrow-right') }), l.change[1]) : null);
            if (intro) n.style.animationDelay = (0.15 + order++ * 0.22) + 's';
            return n;
          })));
      });

      const ends = h('div', { class: 'endrow' },
        c.finale ? h('div', { class: 'endcard' }, h('span', { class: 'ec-ic', html: ic('target') }),
          h('div', null, h('h4', null, c.finale.title, c.finale.status === 'coming' ? h('span', { class: 'pill mute' }, '即将开放') : null), h('p', null, c.finale.desc))) : null,
        c.next ? h('div', { class: 'endcard' }, h('span', { class: 'ec-ic', html: ic('layers') }),
          h('div', null, h('h4', null, c.next.title, c.next.status === 'coming' ? h('span', { class: 'pill mute' }, '筹备中') : null),
            h('div', { class: 'ec-items' }, (c.next.items || []).map(x => h('span', null, x))))) : null);

      const canvas = h('div', { class: 'canvas' + (intro ? ' intro' : '') }, h('div', { class: 'track' }, stages), ends);
      if (intro) {
        const tip = h('div', { class: 'intro-tip', role: 'status' },
          h('span', null, `${c.lessons.length} 节课，${c.subtitle}。点任意一节课看看它讲什么。`),
          h('button', { type: 'button', onclick: () => tip.remove() }, '知道了'));
        canvas.append(tip);
        localStorage.setItem(firstVisitKey, '1');
        setTimeout(() => tip.isConnected && tip.remove(), 9000);
      }

      const toolbox = h('section', { class: 'toolbox', 'aria-label': '我的工具箱' },
        h('h3', null, '我的工具箱', logged ? h('span', { class: 'pill acc' }, `${toolN}/${c.tools.length}`) : null),
        h('p', null, logged ? '每学完一节课，收集对应的工具卡，随时点开查看。' : '每节课都有可以带走的方法卡。登录后学完一节课即可收集。'),
        h('div', { class: 'tcards' }, c.tools.map(t => {
          const got = logged && isDone(prog, t.lesson);
          return h('button', { class: 'tcard' + (got ? ' got' : logged ? ' locked' : ''), type: 'button', onclick: () => openTool(t, c) },
            h('span', { html: ic(got ? 'check' : 'file') }), t.name);
        })));

      root.replaceChildren(crumb, head, ...(loginbar ? [loginbar] : []), canvas, toolbox);
    }

    function renderComing() {
      return h('div', { class: 'ls-hero' },
        h('span', { class: 'pill warn' }, '筹备中'),
        h('h2', { class: 'ltitle' }, c.title),
        h('p', { class: 'ltag' }, c.subtitle),
        c.summary ? h('p', null, c.summary) : null,
        c.topics.length ? h('div', { class: 'lsec' }, h('h4', null, '规划内容'), h('div', { class: 'chipsx' }, c.topics.map(t => h('span', null, t)))) : null,
        h('div', { class: 'lsec' }, h('a', { class: 'iconbtn', href: 'course.html?id=ai-intro' }, '先去学「AI入门」')));
    }

    /* ---------- 课程卡抽屉 ---------- */
    const scrim = h('div', { class: 'sheet-scrim', onclick: () => closeSheet() });
    const sheet = h('aside', { class: 'sheet', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'sheet-title', tabindex: '-1' });
    document.body.append(scrim, sheet);
    let lastFocus = null;
    addEventListener('keydown', e => { if (e.key === 'Escape' && sheet.classList.contains('show')) closeSheet(); });

    function openSheet(lid) {
      const l = c.lessons.find(x => x.id === lid);
      if (!l) return;
      if (!sheet.classList.contains('show')) lastFocus = document.activeElement;
      sheetLid = lid;
      root.querySelectorAll('.node').forEach(n => n.classList.toggle('sel', n.dataset.lid === lid));
      const logged = !!c.logged_in;
      const stage = c.stages.find(s => s.id === l.stage);
      const tools = c.tools.filter(t => l.takeaways.includes(t.id));
      const idx = c.lessons.indexOf(l);
      sheet.replaceChildren(
        h('div', { class: 'sh-head' },
          h('span', { class: 'lmeta' }, `第${l.no}课 · ${stage ? stage.name : ''}`),
          h('span', null,
            h('button', { class: 'iconbtn sq', type: 'button', 'aria-label': '上一课', disabled: idx === 0 ? true : null, onclick: () => openSheet(c.lessons[idx - 1].id), html: ic('arrow-left') }),
            ' ',
            h('button', { class: 'iconbtn sq', type: 'button', 'aria-label': '下一课', disabled: idx === c.lessons.length - 1 ? true : null, onclick: () => openSheet(c.lessons[idx + 1].id), html: ic('arrow-right') }),
            ' ',
            h('button', { class: 'iconbtn sq', type: 'button', 'aria-label': '关闭', onclick: () => closeSheet(), html: ic('x') }))),
        h('div', { class: 'sh-body' },
          h('h3', { class: 'ltitle', id: 'sheet-title' }, l.title),
          h('p', { class: 'ltag' }, l.tagline),
          changeChip(l),
          h('div', { class: 'lsec' }, h('h4', null, '这节课解决'), h('p', null, l.solves)),
          l.practice.length ? h('div', { class: 'lsec' }, h('h4', null, '你会练习'), h('ul', null, l.practice.map(p => h('li', null, p)))) : null,
          tools.length ? h('div', { class: 'lsec' }, h('h4', null, '你会带走'), h('div', { class: 'chipsx' }, tools.map(t =>
            h('button', { class: 'tcard', type: 'button', onclick: () => openTool(t, c) }, h('span', { html: ic('file') }), t.name)))) : null,
          l.cases.length ? h('div', { class: 'lsec' }, h('h4', null, '案例人物'), h('p', null, l.cases.join(' / '))) : null,
          h('div', { class: 'lsec' }, h('h4', null, '三种学法'), modeList(c, l, logged)),
          l.knowledge.length ? h('div', { class: 'lsec' }, h('h4', null, '相关知识（AI知识普及 · 筹备中）'), h('div', { class: 'chipsx' }, l.knowledge.map(k => h('span', null, k)))) : null),
        h('div', { class: 'sh-foot' }, h('a', { class: 'iconbtn primary', href: lessonUrl(c.id, l.id) }, '进入这节课', h('span', { html: ic('arrow-right') }))));
      scrim.classList.add('show'); sheet.classList.add('show');
      sheet.focus();
    }
    function closeSheet() {
      sheetLid = null;
      scrim.classList.remove('show'); sheet.classList.remove('show');
      root.querySelectorAll('.node.sel').forEach(n => n.classList.remove('sel'));
      if (lastFocus && lastFocus.focus) lastFocus.focus();
    }

    root.replaceChildren(h('p', { class: 'center-msg' }, '加载中…'));
    onAuth(() => load());
  }

  /* =====================================================================
   * 3. 课程首屏 + 学习页
   * ===================================================================== */
  async function mountLesson(root) {
    const cid = qs.get('course') || 'ai-intro', lid = qs.get('lesson') || '';
    let c, askedLogin = false;

    async function load(logged) {
      try { c = await api('/api/courses/' + encodeURIComponent(cid)); }
      catch { root.replaceChildren(h('div', { class: 'center-msg' }, '课程加载失败。', h('br'), h('a', { href: 'courses.html' }, '返回课程列表'))); return; }
      const l = c.lessons.find(x => x.id === lid);
      if (!l) { root.replaceChildren(h('div', { class: 'center-msg' }, '这节课不存在。', h('br'), h('a', { href: 'course.html?id=' + encodeURIComponent(cid) }, '返回课程地图'))); return; }
      document.title = `第${l.no}课 · ${l.title}｜${c.title}`;
      render(l, logged);
      // 互动课等受限页面在未登录时会跳回这里并带 login=1：自动弹出登录框
      if (!logged && qs.get('login') === '1' && !askedLogin) { askedLogin = true; login(); }
    }

    function render(l, logged) {
      const stage = c.stages.find(s => s.id === l.stage);
      const idx = c.lessons.indexOf(l);
      const prev = c.lessons[idx - 1], next = c.lessons[idx + 1];
      const tools = c.tools.filter(t => l.takeaways.includes(t.id));
      let mode = logged ? qs.get('mode') : null;
      if (mode && !(mode in l.modes && l.modes[mode].available && mode !== 'interactive')) mode = null;

      const hero = h('div', { class: 'ls-hero' },
        h('span', { class: 'lmeta' }, `${c.title} · 第${l.no}课 · ${stage ? stage.name : ''}`),
        h('h2', { class: 'ltitle' }, l.title),
        h('p', { class: 'ltag' }, l.tagline),
        changeChip(l),
        h('div', { class: 'ls-grid' },
          h('div', { class: 'lsec' }, h('h4', null, '这节课解决'), h('p', null, l.solves)),
          l.practice.length ? h('div', { class: 'lsec' }, h('h4', null, '你会练习'), h('ul', null, l.practice.map(p => h('li', null, p)))) : null,
          tools.length ? h('div', { class: 'lsec' }, h('h4', null, '你会带走'), h('div', { class: 'chipsx' }, tools.map(t =>
            h('button', { class: 'tcard' + (logged && isDone(c.progress, l.id) ? ' got' : ''), type: 'button', onclick: () => openTool(t, c) }, h('span', { html: ic(logged && isDone(c.progress, l.id) ? 'check' : 'file') }), t.name)))) : null,
          l.cases.length ? h('div', { class: 'lsec' }, h('h4', null, '案例人物'), h('p', null, l.cases.join(' / '))) : null),
        h('div', { class: 'lsec ls-modes' }, h('h4', null, '选择一种方式开始'), modeList(c, l, logged)),
        logged ? null : h('div', { class: 'ls-lock', role: 'note' },
          h('span', { html: ic('shield') }),
          h('span', null, '登录后才能看视频、读正文和上互动课，学习进度会记录在你的账号里。'),
          h('span', { class: 'sp' }),
          h('button', { class: 'iconbtn primary', type: 'button', onclick: login }, '登录 / 注册')));

      const panel = mode ? h('section', { class: 'ls-panel', 'aria-label': MODE_META[mode].name }) : null;
      const pager = h('nav', { class: 'ls-pager', 'aria-label': '课程翻页' },
        prev ? h('a', { href: lessonUrl(c.id, prev.id) }, h('small', null, '← 上一课'), `第${prev.no}课 · ${prev.title}`) : null,
        h('a', { href: 'course.html?id=' + encodeURIComponent(c.id) }, h('small', null, '课程地图'), `查看全部 ${c.lessons.length} 节课`),
        next ? h('a', { class: 'nx', href: lessonUrl(c.id, next.id) }, h('small', null, '下一课 →'), `第${next.no}课 · ${next.title}`) : null);

      root.replaceChildren(
        h('div', { class: 'crumb' }, h('a', { href: 'courses.html' }, 'AI 课程'), ' / ', h('a', { href: 'course.html?id=' + encodeURIComponent(c.id) }, c.title), ' / ', `第${l.no}课`),
        hero, ...(panel ? [panel] : []), pager);
      if (panel) { fillPanel(panel, l, mode); panel.scrollIntoView && setTimeout(() => panel.scrollIntoView({ behavior: 'smooth', block: 'start' }), 60); }
    }

    function doneBar(l, mode) {
      const done = modeDone(c.progress, l.id, mode);
      const meta = MODE_META[mode];
      const btn = h('button', { class: 'iconbtn primary', type: 'button', disabled: done ? true : null }, done ? '✓ 已' + meta.verb : '我' + meta.verb + '了');
      const bar = h('div', { class: 'ls-donebar' }, h('p', null, done ? '这节课已记录为完成，对应的工具卡已收进你的工具箱。' : `${meta.verb}后点一下，记录到你的学习进度并收集工具卡。`), btn);
      btn.addEventListener('click', () => markDone(l, mode, btn, bar));
      return bar;
    }
    async function markDone(l, mode, btn, bar) {
      btn.disabled = true;
      try {
        await putProgress(c.id, l.id, { mode, done: true });
        c.progress = c.progress || {};
        (c.progress[l.id] = c.progress[l.id] || {})[mode] = { done: true };
        btn.textContent = '✓ 已' + MODE_META[mode].verb;
        bar.querySelector('p').textContent = '已记录。对应的工具卡已收进你的工具箱。';
        const next = c.lessons[c.lessons.indexOf(l) + 1];
        if (next && !bar.querySelector('.go-next')) bar.append(h('a', { class: 'iconbtn go-next', href: lessonUrl(c.id, next.id) }, `下一课：第${next.no}课`, h('span', { html: ic('arrow-right') })));
      } catch (e) {
        btn.disabled = false;
        if (e.status === 401) login();
        else bar.querySelector('p').textContent = '保存失败：' + e.message;
      }
    }

    async function fillPanel(panel, l, mode) {
      if (mode === 'video') {
        const v = h('video', { controls: true, preload: 'metadata', src: `/api/courses/${encodeURIComponent(c.id)}/lessons/${encodeURIComponent(l.id)}/video` });
        v.append('你的浏览器不支持视频播放。');
        const bar = doneBar(l, 'video');
        v.addEventListener('ended', () => { const b = bar.querySelector('button'); if (!b.disabled) b.click(); });
        panel.append(v, bar);
        return;
      }
      panel.append(h('p', { class: 'center-msg' }, '正文加载中…'));
      try {
        const d = await api(`/api/courses/${encodeURIComponent(c.id)}/lessons/${encodeURIComponent(l.id)}/text`);
        const md = h('article', { class: 'md' });
        // 安全前提：必须有 DOMPurify 才渲染 HTML；否则退回纯文本
        if (window.DOMPurify && window.marked) md.innerHTML = window.DOMPurify.sanitize(window.marked.parse(d.markdown || ''));
        else md.append(h('pre', { class: 'md-plain' }, d.markdown || ''));
        panel.replaceChildren(md, doneBar(l, 'text'));
      } catch (e) {
        panel.replaceChildren(h('p', null, e.status === 401 ? '登录已过期，请重新登录。' : '正文加载失败：' + e.message));
        if (e.status === 401) login();
      }
    }

    root.replaceChildren(h('p', { class: 'center-msg' }, '加载中…'));
    onAuth(logged => load(logged));
  }

  /* ---------- 挂载 ---------- */
  const listBox = document.querySelector('[data-builtin-courses]');
  if (listBox) mountList(listBox);
  const canvas = document.querySelector('[data-course-canvas]');
  if (canvas) mountCanvas(canvas);
  const lesson = document.querySelector('[data-lesson]');
  if (lesson) mountLesson(lesson);
})();

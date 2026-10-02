/* 内置课程前端：
   - courses.html：内置课程入口卡片，挂载点 [data-builtin-courses]；已上线课程点击后在新标签页打开课程目录
   - course.html?id=<课程>：课程目录（深色，与故事页同一视觉）：封面 → 四个阶段 → 课程卡 → 工具箱
     有故事页的课可进入（story.html），其余显示「制作中」；登录后显示进度并可续读。
   访问规则：未登录可看目录；进入故事页、工具卡要点、进度需登录。
   依赖：auth.js（window.clAuth）、icons.js（仅 courses.html，可选）。
   所有课程文本都通过 textContent 写入。 */
(() => {
  'use strict';

  const ic = n => (window.icon ? window.icon(n) : '');
  const qs = new URLSearchParams(location.search);
  const CN = '一二三四五六七八九';

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

  const catalogUrl = cid => 'course.html?id=' + encodeURIComponent(cid);
  const storyUrl = (cid, lid) => `story.html?course=${encodeURIComponent(cid)}&lesson=${encodeURIComponent(lid)}`;
  const login = () => window.clAuth && window.clAuth.openAuth('login');

  // 一节课的状态：done 已完成 / mid 学到一半 / open 可学 / soon 制作中
  function lessonState(prog, l) {
    if (!l.modes.story.available) return 'soon';
    const p = prog && prog[l.id];
    if (p && Object.values(p).some(x => x.done)) return 'done';
    if (p && p.story) return 'mid';
    return 'open';
  }

  /* =====================================================================
   * 1. 课程列表页：内置课程（已上线的课程在新标签页打开目录）
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
      const kids = [
        h('div', { class: 'b-top' }, h('h3', null, c.title), pub ? h('span', { class: 'pill ok' }, '已上线') : h('span', { class: 'pill warn' }, '筹备中')),
        pub ? h('span', { class: 'b-go', html: ic('arrow-up-right') }) : null,
        h('p', { class: 'b-sub' }, c.subtitle),
        c.summary ? h('p', { class: 'b-sub' }, c.summary) : null,
        !pub && c.topics.length ? h('ul', { class: 'b-topics', 'aria-label': '规划内容' }, c.topics.slice(0, 6).map(t => h('li', null, t))) : null,
        h('div', { class: 'b-meta' }, meta.map(m => h('span', null, m)), pub ? h('span', null, '在新标签页打开') : null)];
      if (!pub) return h('div', { class: 'bcard soon', 'aria-disabled': 'true' }, kids);
      return h('a', { class: 'bcard', href: catalogUrl(c.id), target: '_blank', rel: 'noopener',
        'aria-label': `${c.title}（在新标签页打开）` }, kids);
    }));
  }

  /* =====================================================================
   * 2. 课程目录
   * ===================================================================== */
  function mountCatalog(root) {
    const cid = qs.get('id') || 'ai-intro';
    const bar = document.querySelector('[data-cat-bar]');

    function renderBar(c, st) {
      const right = !st.loaded ? null
        : st.logged_in ? [h('span', { class: 'who' }, st.user ? st.user.username : ''), h('button', { class: 'btn', type: 'button', onclick: () => window.clAuth.logout() }, '退出')]
        : [h('button', { class: 'btn pri', type: 'button', onclick: login }, '登录 / 注册')];
      bar.replaceChildren(
        h('a', { class: 'logo', href: 'index.html', 'aria-label': '返回网站首页' }, 'CL'),
        h('span', { class: 'crumb' }, h('a', { href: 'courses.html', style: 'color:inherit;text-decoration:none' }, 'AI 课程'), ' / ', h('b', null, c ? c.title : '')),
        ...(right || []));
    }

    async function load(st) {
      let c;
      try { c = await api('/api/courses/' + encodeURIComponent(cid)); }
      catch (e) {
        renderBar(null, st);
        root.replaceChildren(h('div', { class: 'cat-msg' }, h('p', null, e.status === 404 ? '课程不存在。' : '课程加载失败，请稍后刷新重试。', h('br'), h('a', { href: 'courses.html' }, '返回课程列表'))));
        return;
      }
      document.title = c.title + '｜课程目录｜Chris Li · AI Agent';
      renderBar(c, st);
      if (c.status !== 'published') { root.replaceChildren(renderComing(c)); return; }
      root.replaceChildren(...render(c));
    }

    function render(c) {
      const logged = !!c.logged_in, prog = c.progress;
      const states = Object.fromEntries(c.lessons.map(l => [l.id, lessonState(logged ? prog : null, l)]));
      const ready = c.lessons.filter(l => states[l.id] !== 'soon');
      const doneN = c.lessons.filter(l => states[l.id] === 'done').length;
      const toolN = logged ? c.tools.filter(t => states[t.lesson] === 'done').length : 0;
      // 推荐入口：学到一半的课 > 第一节没学完的可学课
      const here = logged ? (ready.find(l => states[l.id] === 'mid') || ready.find(l => states[l.id] === 'open')) : ready[0];

      /* ---- 封面 ---- */
      let cta;
      if (!ready.length) cta = h('span', { class: 'note' }, '课程制作中，敬请期待。');
      else if (!logged) cta = [h('a', { class: 'btn pri', href: storyUrl(c.id, here.id) }, `从第${here.no}课开始 →`), h('span', { class: 'note' }, '登录后进入课程，进度保存在你的账号里')];
      else if (!here) cta = h('span', { class: 'note' }, `已上线的 ${ready.length} 节课都学完了，新课程制作中。`);
      else cta = h('a', { class: 'btn pri', href: storyUrl(c.id, here.id) }, `${states[here.id] === 'mid' ? '继续学习' : doneN ? '下一课' : '开始学习'}：第${here.no}课 →`);

      const title = c.title.replace(/^AI/, '');
      const hero = h('section', { class: 'cat-hero' },
        h('div', { class: 'small' }, `${c.lessons.length} 节课 · ${c.stages.length} 个阶段 · ${c.audience || ''}`),
        h('h1', null, c.title.startsWith('AI') ? ['AI', h('em', null, title)] : c.title),
        h('p', { class: 'sub' }, c.subtitle),
        c.summary ? h('p', { class: 'sum' }, c.summary) : null,
        h('div', { class: 'row' }, cta),
        h('nav', { class: 'cat-path', 'aria-label': '四个阶段' }, c.stages.map((s, i) => {
          const ls = c.lessons.filter(l => l.stage === s.id);
          const lit = logged && ls.length && ls.every(l => states[l.id] === 'done');
          return h('a', { href: '#stage-' + s.id, class: lit ? 'lit' : null }, h('i', null, String(i + 1)), `${s.name} · ${s.question}`);
        })));

      /* ---- 总进度（登录后） ---- */
      const ov = logged ? h('section', { class: 'cat-ov', 'aria-label': '学习进度' },
        h('div', null, h('b', null, `${doneN} / ${c.lessons.length}`), h('span', null, '节课已完成'), h('div', { class: 'bar' }, h('i', { style: `width:${doneN / c.lessons.length * 100}%` }))),
        h('div', null, h('b', null, `${toolN} / ${c.tools.length}`), h('span', null, '张工具卡已收集'), h('div', { class: 'bar' }, h('i', { style: `width:${c.tools.length ? toolN / c.tools.length * 100 : 0}%` }))),
        h('div', null, h('b', null, `${ready.length}`), h('span', null, `节课已上线，其余 ${c.lessons.length - ready.length} 节制作中`))) : null;

      /* ---- 阶段 + 课程卡 ---- */
      const intro = !sessionStorage.getItem('cl_cat_in_' + c.id);   // 仅用于首屏动效，与学习进度无关
      sessionStorage.setItem('cl_cat_in_' + c.id, '1');
      let order = 0;
      const stages = c.stages.map((s, si) => {
        const ls = c.lessons.filter(l => l.stage === s.id);
        const lit = logged && ls.length && ls.every(l => states[l.id] === 'done');
        const head = h('div', { class: 'cat-st-h' },
          h('span', { class: 'n', 'aria-hidden': 'true' }, String(si + 1).padStart(2, '0')),
          h('span', { class: 'lbl' }, `阶段${CN[si] || si + 1} · ${s.name}`),
          h('h2', null, s.question));
        return h('section', { class: 'cat-stage' + (lit ? ' lit' : ''), id: 'stage-' + s.id, 'data-tone': String(si % 4), 'aria-label': `阶段${si + 1}：${s.name}` },
          head, h('div', { class: 'cat-lessons' }, ls.map(l => {
            const card = lessonCard(c, l, states[l.id], here && here.id === l.id);
            if (intro) card.style.animationDelay = (0.08 + order++ * 0.08) + 's';
            return card;
          })));
      });

      /* ---- 工具箱 ---- */
      const toolbox = h('section', { 'aria-label': '工具箱' },
        h('h2', { class: 'cat-sec-h' }, '工具箱'),
        h('p', { class: 'cat-sec-p' }, logged ? '每学完一节课，收集它的工具卡。点开查看要点。' : '每节课都有可以带走的方法卡，登录后学完即可收集。'),
        h('div', { class: 'cat-tools' }, c.tools.map(t => {
          const from = c.lessons.find(l => l.id === t.lesson);
          const got = logged && states[t.lesson] === 'done';
          return h('details', { class: 'tbx' + (got ? ' got' : '') },
            h('summary', null, h('b', null, t.name), h('small', null, (from ? `第${from.no}课` : '') + (got ? ' · 已收集' : ''))),
            t.points ? h('ul', null, t.points.map(p => h('li', null, p)))
              : h('p', { class: 'lock' }, '登录后可以查看工具卡的完整内容。'));
        })));

      const ends = (c.finale || c.next) ? h('section', { class: 'cat-ends' },
        c.finale ? h('div', null, h('h4', null, c.finale.title, c.finale.status === 'coming' ? h('span', { class: 'st-pill lk' }, '即将开放') : null), h('p', null, c.finale.desc)) : null,
        c.next ? h('div', null, h('h4', null, c.next.title, c.next.status === 'coming' ? h('span', { class: 'st-pill lk' }, '筹备中') : null),
          h('div', { class: 'items' }, (c.next.items || []).map(x => h('span', null, x)))) : null) : null;

      const wrap = h('div', { class: intro ? 'cat-in' : null }, stages);
      return [hero, ov, wrap, toolbox, ends].filter(Boolean);
    }

    function lessonCard(c, l, state, here) {
      const tools = c.tools.filter(t => l.takeaways.includes(t.id));
      const mins = l.modes.story.minutes;
      const pill = {
        done: h('span', { class: 'st-pill ok' }, '✓ 已完成'),
        mid: h('span', { class: 'st-pill mid' }, '学到一半'),
        open: h('span', { class: 'st-pill go' }, '可学习'),
        soon: h('span', { class: 'st-pill lk' }, '制作中'),
      }[state];
      const go = state === 'soon' ? '制作中，敬请期待' : state === 'done' ? '再看一遍 →' : state === 'mid' ? '继续 →' : '进入课程 →';
      const kids = [
        h('div', { class: 'top' }, h('span', { class: 'no' }, `第 ${l.no} 课`), mins && state !== 'soon' ? h('span', null, `约 ${mins} 分钟`) : null, h('span', { class: 'sp' }), here ? h('span', { class: 'st-pill go' }, '从这里开始') : null, pill),
        h('h3', null, l.title),
        l.tagline ? h('p', { class: 'tag' }, l.tagline) : null,
        l.change.length >= 2 ? h('span', { class: 'chg' }, l.change[0], ' → ', h('b', null, l.change[1])) : null,
        l.solves ? h('p', { class: 'solve' }, l.solves) : null,
        h('div', { class: 'ft' }, tools.map(t => h('span', { class: 'tool' }, t.name)), h('span', { class: 'go' }, go))];
      const label = `第${l.no}课：${l.title}`;
      if (state === 'soon') return h('div', { class: 'lcard soon', 'aria-disabled': 'true', 'aria-label': label + '（制作中）' }, kids);
      return h('a', { class: 'lcard' + (here ? ' here' : ''), href: storyUrl(c.id, l.id), 'aria-label': label }, kids);
    }

    function renderComing(c) {
      return h('section', { class: 'cat-hero' },
        h('div', { class: 'small' }, '筹备中'),
        h('h1', null, c.title),
        h('p', { class: 'sub' }, c.subtitle),
        c.summary ? h('p', { class: 'sum' }, c.summary) : null,
        c.topics.length ? h('div', { class: 'cat-path' }, c.topics.map(t => h('a', { href: '#', onclick: e => e.preventDefault() }, t))) : null,
        h('div', { class: 'row' }, h('a', { class: 'btn pri', href: catalogUrl('ai-intro') }, '先去学「AI入门」 →')));
    }

    root.replaceChildren(h('div', { class: 'cat-msg' }, '加载中…'));
    if (!window.clAuth) { load({ loaded: true, logged_in: false }); return; }
    let last;
    window.clAuth.onChange(st => {
      if (!st.loaded) return;
      if (last === st.logged_in) return;
      last = st.logged_in;
      load(st);
    });
  }

  /* ---------- 挂载 ---------- */
  const listBox = document.querySelector('[data-builtin-courses]');
  if (listBox) mountList(listBox);
  const cat = document.querySelector('[data-course-catalog]');
  if (cat) mountCatalog(cat);
})();

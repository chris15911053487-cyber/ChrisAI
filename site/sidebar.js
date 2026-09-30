/* 共享一级菜单：注入到所有页面，按当前页高亮，移动端可抽屉展开。
   要改菜单项只需改这里的 ITEMS 数组，所有页面自动同步；bottom: true 的项固定在最下方。 */
(() => {
  'use strict';

  const ITEMS = [
    { key: 'agent',   icon: 'message', text: '对话',   href: 'agent.html'   },
    { key: 'skills',  icon: 'layers',  text: '技能',   href: 'skills.html'  },
    { key: 'knowledge', icon: 'book',  text: '知识库', href: 'knowledge.html' },
    { key: 'courses', icon: 'cap',     text: '课程',   href: 'courses.html' },
    { key: 'works',   icon: 'grid',    text: '作品',   href: 'works.html'   },
    { key: 'tools',   icon: 'wrench',  text: '工具',   href: 'tools.html'   },
    { key: 'settings', icon: 'settings', text: '设置', href: 'settings.html', bottom: true },  // 固定在左下角
  ];

  // 当前页：优先读 <body data-nav="...">，否则按文件名推断
  const page = (document.body.getAttribute('data-nav') ||
    (location.pathname.split('/').pop() || 'agent.html').replace('.html', '') || 'agent');

  // 构建竖栏
  const rail = document.createElement('nav');
  rail.className = 'navrail';
  rail.id = 'navrail';
  rail.setAttribute('aria-label', '主菜单');
  for (const it of ITEMS) {
    const a = document.createElement('a');
    a.className = 'nav-item' + (it.key === page ? ' active' : '') + (it.bottom ? ' bottom' : '');
    a.href = it.href;
    if (it.key === page) a.setAttribute('aria-current', 'page');
    a.innerHTML = `<span class="ic" aria-hidden="true">${window.icon ? window.icon(it.icon) : ''}</span><span class="tx">${it.text}</span>`;
    rail.appendChild(a);
  }

  // 移动端遮罩
  const scrim = document.createElement('div');
  scrim.className = 'nav-scrim';

  document.body.appendChild(rail);
  document.body.appendChild(scrim);

  // 移动端开关：复用/注入顶栏左侧的按钮
  function bindToggle(btn) {
    if (!btn) return;
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const open = rail.classList.toggle('open');
      scrim.classList.toggle('show', open);
      btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
  }
  const close = () => { rail.classList.remove('open'); scrim.classList.remove('show'); };
  scrim.addEventListener('click', close);
  rail.addEventListener('click', (e) => { if (e.target.closest('.nav-item')) close(); });

  // 若页面已有 ☰ 菜单按钮（agent.html 用于会话列表），单独注入一个一级菜单按钮，避免冲突
  const navBtn = document.createElement('button');
  navBtn.className = 'iconbtn sq only-mobile';
  navBtn.id = 'navToggle';
  navBtn.setAttribute('aria-label', '主菜单');
  navBtn.setAttribute('aria-controls', 'navrail');
  navBtn.setAttribute('aria-expanded', 'false');
  navBtn.innerHTML = window.icon ? window.icon('menu') : '≡';
  const left = document.querySelector('header.bar .left');
  if (left) left.insertBefore(navBtn, left.firstChild);
  bindToggle(navBtn);
})();

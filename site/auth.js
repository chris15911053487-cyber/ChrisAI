/* 全站共享登录：登录/注册对话框 + 登录态 + 登出。
   暴露 window.clAuth：
     - state: {loaded, logged_in, user:{id,username}|null, quota}
     - openAuth(mode)   打开登录框（mode: 'login' | 'register'）
     - logout()         退出登录
     - onChange(fn)     订阅登录态变化（立即以当前 state 回调一次）
     - refresh()        重新拉取 /api/auth/me
   依赖：icons.js（window.icon，可选）。需在 sidebar.js 之前引入。 */
(() => {
  'use strict';
  if (window.clAuth) return;

  const listeners = new Set();
  const state = { loaded: false, logged_in: false, user: null, quota: null };

  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const ic = name => (window.icon ? window.icon(name) : '');

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

  function emit() { for (const fn of listeners) { try { fn(state); } catch {} } }
  function setFromResp(d) {
    state.loaded = true;
    state.logged_in = !!(d && d.logged_in);
    state.user = (d && d.user) || null;
    state.quota = (d && d.quota) || null;
    emit();
  }

  /* ---------- 轻量 toast（页面若已有 #toast 则复用） ---------- */
  let tt;
  function toast(msg, err) {
    let t = document.getElementById('toast');
    let temp = false;
    if (!t) { t = el('div', 'cl-auth-toast'); t.id = 'cl-auth-toast'; document.body.appendChild(t); temp = true; }
    t.textContent = msg; t.classList.toggle('err', !!err); t.classList.add('show');
    clearTimeout(tt); tt = setTimeout(() => { t.classList.remove('show'); if (temp) setTimeout(() => t.remove(), 300); }, 3200);
  }

  /* ---------- 样式（作用域前缀 cl-auth-，避免与各页冲突） ---------- */
  function injectStyles() {
    if (document.getElementById('cl-auth-style')) return;
    const s = el('style'); s.id = 'cl-auth-style';
    s.textContent = `
.cl-auth-dlg{background:var(--surface);color:var(--text);border:1px solid var(--line);border-radius:16px;padding:24px;width:min(400px,92vw);box-shadow:var(--sh-3)}
.cl-auth-dlg::backdrop{background:rgba(10,10,10,.28);backdrop-filter:blur(2px)}
.cl-auth-dlg h3{margin:0 0 4px;font-size:18px;font-weight:600;letter-spacing:-.01em}
.cl-auth-dlg p.sub{margin:0 0 16px;color:var(--text-2);font-size:13px;line-height:1.6}
.cl-auth-dlg label{display:block;font-size:13px;font-weight:500;color:var(--text-2);margin:14px 0 6px}
.cl-auth-dlg input{width:100%;background:var(--surface);border:1px solid var(--line-2);border-radius:9px;padding:9px 12px;color:var(--text);font:14px/1.5 var(--sans);transition:border-color .18s,box-shadow .18s}
.cl-auth-dlg input:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}
.cl-auth-dlg .err{color:var(--err);font-size:13px;margin-top:10px;min-height:1em}
.cl-auth-dlg .row{display:flex;align-items:center;gap:8px;margin-top:20px}
.cl-auth-dlg .row .spacer{flex:1}
.cl-auth-btn{height:34px;padding:0 14px;border-radius:9px;border:1px solid var(--line-2);background:var(--surface);color:var(--text);font:13px/1 var(--sans);cursor:pointer;transition:border-color .18s,background .18s,color .18s;display:inline-flex;align-items:center;gap:6px}
.cl-auth-btn:hover{border-color:var(--text-3)}
.cl-auth-btn.primary{background:var(--text);color:#fff;border-color:var(--text)}
.cl-auth-btn.primary:hover{opacity:.9}
.cl-auth-btn.link{border:none;background:none;color:var(--accent);padding:0 2px}
.cl-auth-btn:disabled{opacity:.5;cursor:default}
.cl-auth-toast{position:fixed;left:50%;bottom:40px;transform:translate(-50%,12px);opacity:0;pointer-events:none;z-index:1000;background:var(--text);color:#fff;padding:10px 16px;border-radius:999px;font-size:13px;transition:all .25s;max-width:90vw;box-shadow:var(--sh-3)}
.cl-auth-toast.show{opacity:1;transform:translate(-50%,0)}
.cl-auth-toast.err{background:var(--err)}
`;
    document.head.appendChild(s);
  }

  /* ---------- 对话框 ---------- */
  let dlg, mode = 'login';
  function buildDialog() {
    if (dlg) return dlg;
    injectStyles();
    dlg = el('dialog', 'cl-auth-dlg'); dlg.id = 'cl-auth-dlg';
    dlg.innerHTML = `
      <form method="dialog" id="cl-auth-form">
        <h3 id="cl-auth-title">登录</h3>
        <p class="sub">登录后对话次数更多，且数据跨设备保存。</p>
        <label for="cl-auth-user">用户名</label>
        <input id="cl-auth-user" name="username" autocomplete="username" placeholder="2-20 位字母、数字、下划线或中文" maxlength="20">
        <label for="cl-auth-pass">密码</label>
        <input id="cl-auth-pass" name="password" type="password" autocomplete="current-password" placeholder="6-128 位" maxlength="128">
        <div class="err" id="cl-auth-err"></div>
        <div class="row">
          <button class="cl-auth-btn link" type="button" id="cl-auth-switch"></button>
          <span class="spacer"></span>
          <button class="cl-auth-btn" value="cancel" formnovalidate type="submit">取消</button>
          <button class="cl-auth-btn primary" value="ok" id="cl-auth-ok" type="submit">登录</button>
        </div>
      </form>`;
    document.body.appendChild(dlg);

    const $ = id => dlg.querySelector('#' + id);
    $('cl-auth-switch').addEventListener('click', () => { mode = mode === 'register' ? 'login' : 'register'; $('cl-auth-err').textContent = ''; syncMode(); $('cl-auth-user').focus(); });
    dlg.querySelector('#cl-auth-form').addEventListener('submit', async e => {
      if (e.submitter && e.submitter.value === 'cancel') return; // 让 dialog 正常关闭
      e.preventDefault();
      const username = $('cl-auth-user').value.trim();
      const password = $('cl-auth-pass').value;
      if (!username || !password) { $('cl-auth-err').textContent = '请填写用户名和密码'; return; }
      $('cl-auth-ok').disabled = true;
      try {
        const path = mode === 'register' ? '/api/auth/register' : '/api/auth/login';
        const d = await jsend(path, { username, password });
        setFromResp(d);
        dlg.close();
        const mg = d.migrated || {};
        const moved = (mg.sessions || 0) + (mg.kbs || 0) + (mg.skills_moved || 0);
        let msg = mode === 'register' ? '注册成功，已登录' : '已登录';
        if (moved > 0) {
          const parts = [];
          if (mg.sessions) parts.push(mg.sessions + ' 个会话');
          if (mg.kbs) parts.push(mg.kbs + ' 个知识库');
          if (mg.skills_moved) parts.push(mg.skills_moved + ' 个技能');
          msg += '，已迁移' + parts.join('、');
        }
        toast(msg);
      } catch (err) {
        $('cl-auth-err').textContent = err.message;
      } finally {
        $('cl-auth-ok').disabled = false;
      }
    });
    return dlg;
  }

  function syncMode() {
    const reg = mode === 'register';
    const $ = id => dlg.querySelector('#' + id);
    $('cl-auth-title').textContent = reg ? '注册账号' : '登录';
    $('cl-auth-ok').textContent = reg ? '注册并登录' : '登录';
    $('cl-auth-switch').textContent = reg ? '已有账号？去登录' : '没有账号？去注册';
    $('cl-auth-pass').setAttribute('autocomplete', reg ? 'new-password' : 'current-password');
  }

  /* ---------- 公共 API ---------- */
  function openAuth(m) {
    buildDialog();
    mode = m === 'register' ? 'register' : 'login';
    dlg.querySelector('#cl-auth-err').textContent = '';
    dlg.querySelector('#cl-auth-user').value = '';
    dlg.querySelector('#cl-auth-pass').value = '';
    syncMode();
    dlg.showModal();
    dlg.querySelector('#cl-auth-user').focus();
  }

  async function logout() {
    try { await api('/api/auth/logout', { method: 'POST' }); } catch {}
    setFromResp({ logged_in: false, user: null });
    toast('已退出登录');
  }

  function onChange(fn) {
    if (typeof fn !== 'function') return () => {};
    listeners.add(fn);
    fn(state); // 立即以当前状态回调一次
    return () => listeners.delete(fn);
  }

  async function refresh() {
    try { setFromResp(await api('/api/auth/me')); }
    catch { state.loaded = true; emit(); }
  }

  window.clAuth = { state, openAuth, logout, onChange, refresh };

  // 启动即拉取登录态
  refresh();
})();

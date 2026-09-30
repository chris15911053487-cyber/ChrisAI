/* 设置页（管理员）：对话模型列表（任意 OpenAI 兼容接口，设默认、测试）+ 向量 / 重排模型配置 */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const TOKEN_KEY = 'cl_admin_token';   // 与 admin.html 共用，登录一次即可
  let token = sessionStorage.getItem(TOKEN_KEY) || '';

  const GROUPS = [
    ['embed', '向量模型', '知识库语义检索。API Key 留空则只用关键词检索；启用后知识库文档文字会发送到该服务计算向量。', 'database'],
    ['rerank', '重排模型', '对检索结果重新排序，通常能明显提升回答准确率。模型留空则不重排；地址和 Key 留空时复用向量模型的配置。', 'target'],
  ];
  const SOURCE = {page: ['页面设置', 'public'], env: ['.env', 'builtin'], default: ['默认', '']};
  const ENUM_LABEL = {disabled: '关闭', enabled: '开启', low: 'low · 快', high: 'high · 均衡', max: 'max · 最强'};

  let fields = [], status = {};
  let edits = {}, clears = new Set(), resets = new Set();

  let tt;
  function toast(msg, err) {
    const t = $('toast'); t.textContent = msg; t.classList.toggle('err', !!err); t.classList.add('show');
    clearTimeout(tt); tt = setTimeout(() => t.classList.remove('show'), 3200);
  }
  async function api(path, opts = {}) {
    const headers = Object.assign({'X-Admin-Token': token}, opts.headers || {});
    const r = await fetch(path, {credentials: 'same-origin', ...opts, headers});
    let d = null; try { d = await r.json(); } catch {}
    if (r.status === 401) { const e = new Error('令牌无效'); e.auth = true; throw e; }
    if (!r.ok) {
      let msg = d && d.detail;
      if (Array.isArray(msg)) msg = msg.map(x => x.msg).join('；');
      throw new Error(msg || ('请求失败（' + r.status + '）'));
    }
    return d;
  }
  const jsend = (path, body, method) => api(path, {method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});

  /* ---------- 登录 ---------- */
  function showLogin(msg) {
    $('panelWrap').hidden = true; $('saveBar').hidden = true; $('loginWrap').hidden = false; $('logoutBtn').hidden = true;
    $('loginErr').textContent = msg || '';
    $('tokenInput').focus();
  }
  async function enter(t) {
    token = t;
    const [d, m] = await Promise.all([api('/api/admin/settings'), api('/api/admin/models')]);
    sessionStorage.setItem(TOKEN_KEY, token);
    $('loginWrap').hidden = true; $('panelWrap').hidden = false; $('logoutBtn').hidden = false;
    setData(d);
    setModels(m.models);
  }
  $('loginBtn').addEventListener('click', async () => {
    const t = $('tokenInput').value.trim();
    if (!t) { $('loginErr').textContent = '请输入令牌'; return; }
    $('loginBtn').disabled = true;
    try { await enter(t); } catch (e) { token = ''; $('loginErr').textContent = e.message; }
    finally { $('loginBtn').disabled = false; }
  });
  $('tokenInput').addEventListener('keydown', e => { if (e.key === 'Enter') $('loginBtn').click(); });
  $('logoutBtn').addEventListener('click', () => { sessionStorage.removeItem(TOKEN_KEY); token = ''; showLogin(); });

  /* ---------- 渲染 ---------- */
  function setData(d) {
    fields = d.fields; status = d.status || {};
    edits = {}; clears = new Set(); resets = new Set();
    render();
  }
  const pendingCount = () => new Set([...Object.keys(edits), ...clears, ...resets]).size;

  function render() {
    const p = $('panel'); p.replaceChildren(statusLine());
    for (const [g, title, desc, ic] of GROUPS) {
      const sec = el('section', 'sec');
      const h = el('h3'); h.innerHTML = icon(ic); h.append(title);
      sec.append(h, el('p', 'desc', desc));
      const box = el('div', 'box');
      for (const f of fields.filter(x => x.group === g)) box.append(fieldRow(f));
      const foot = el('div', 'foot'); foot.dataset.group = g;
      const tb = el('button', 'iconbtn'); tb.type = 'button'; tb.innerHTML = icon('activity'); tb.append('测试连接');
      tb.addEventListener('click', () => test(g));
      const res = el('span', 'test-res'); res.setAttribute('aria-live', 'polite');
      foot.append(tb, res); box.append(foot);
      sec.append(box); p.append(sec);
    }
    updateSaveBar();
  }

  function statusLine() {
    const s = el('div', 'status-line'), v = status;
    if (v.enabled) {
      s.append('知识库检索：关键词 + 向量（' + v.model + '）' + (v.rerank ? ' + 重排（' + v.rerank + '）' : ''));
      if (v.pending) s.append(el('span', 'warn', ' · 待向量化 ' + v.pending + ' 段'));
      if (v.error) s.append(el('span', 'warn', ' · 最近错误：' + v.error));
    } else {
      s.append('知识库检索：仅关键词（未配置向量模型 API Key）');
    }
    return s;
  }

  function fieldRow(f) {
    const row = el('div', 'frow'), id = 'f_' + f.name;
    const head = el('div', 'fhead');
    const lab = el('label', '', f.label); lab.htmlFor = id;
    const [srcText, srcCls] = SOURCE[f.source] || SOURCE.default;
    const badge = el('span', 'badge ' + srcCls, srcText); badge.title = '当前值来源';
    head.append(lab, badge, el('code', 'fname', f.name));

    let inp;
    const secret = f.type === 'secret';
    if (f.type === 'enum') {
      inp = el('select', 'field');
      for (const c of f.choices) { const o = el('option', '', ENUM_LABEL[c] || c); o.value = c; inp.append(o); }
      inp.value = f.value;
    } else {
      inp = el('input', 'field');
      inp.type = secret ? 'password' : f.type === 'int' ? 'number' : 'text';
      inp.spellcheck = false;
      if (secret) {
        inp.autocomplete = 'new-password';
        inp.placeholder = f.value ? f.value + '（留空不修改）' : '未设置';
      } else {
        inp.value = f.value == null ? '' : String(f.value);
        inp.autocomplete = 'off';
        if (f.placeholder) inp.placeholder = f.placeholder;
      }
      if (f.type === 'int') inp.min = '0';
      if (f.suggest) {
        const dl = el('datalist'); dl.id = id + '_dl';
        f.suggest.forEach(v => { const o = el('option'); o.value = v; dl.append(o); });
        inp.setAttribute('list', dl.id); row.append(dl);
      }
    }
    inp.id = id;
    const orig = secret ? '' : String(f.value == null ? '' : f.value);
    const mark = () => { row.classList.toggle('changed', f.name in edits || clears.has(f.name) || resets.has(f.name)); updateSaveBar(); };
    inp.addEventListener('input', () => {
      const v = inp.value.trim();
      if (v === orig) delete edits[f.name];
      else edits[f.name] = f.type === 'int' ? Number(v) : v;
      if (secret && v) clears.delete(f.name);
      mark();
    });

    const ctl = el('div', 'fctl'); ctl.append(inp);
    if (secret && f.value) {
      const c = el('button', 'iconbtn', '清除'); c.type = 'button';
      c.title = '保存后该 Key 设为空';
      c.addEventListener('click', () => {
        clears.add(f.name); delete edits[f.name]; inp.value = ''; inp.placeholder = '将被清除（保存后生效）'; mark();
      });
      ctl.append(c);
    }
    if (f.source === 'page') {
      const r = el('button', 'iconbtn ghost', '恢复默认'); r.type = 'button';
      r.title = '恢复为 .env / 默认值：' + f.default_display;
      r.addEventListener('click', () => {
        resets.add(f.name); delete edits[f.name]; clears.delete(f.name);
        inp.disabled = true; if (!secret) inp.value = f.default_display === '（空）' ? '' : f.default_display;
        mark();
      });
      ctl.append(r);
    }
    row.append(head, ctl);
    if (f.hint) row.append(el('div', 'fhint', f.hint));
    return row;
  }

  function updateSaveBar() {
    const n = pendingCount();
    $('saveBar').hidden = !n;
    $('saveCount').textContent = n + ' 项修改未保存';
  }

  /* ---------- 保存 / 测试 ---------- */
  async function save() {
    $('saveBtn').disabled = true;
    try {
      const d = await jsend('/api/admin/settings', {values: edits, clear: [...clears], reset: [...resets]}, 'PUT');
      setData(d);
      toast(d.changed.length ? '已保存，立即生效' : '已保存（没有实际变化）');
      return true;
    } catch (e) {
      if (e.auth) showLogin('令牌已失效，请重新输入'); else toast(e.message, true);
      return false;
    } finally { $('saveBtn').disabled = false; }
  }
  $('saveBtn').addEventListener('click', save);
  $('discardBtn').addEventListener('click', () => { edits = {}; clears = new Set(); resets = new Set(); render(); });
  addEventListener('beforeunload', e => { if (pendingCount()) { e.preventDefault(); e.returnValue = ''; } });

  async function test(g) {
    if (pendingCount()) {
      if (!confirm('测试使用已保存的配置。先保存当前修改再测试？')) return;
      if (!(await save())) return;
    }
    const foot = document.querySelector('.foot[data-group="' + g + '"]');
    const btn = foot.querySelector('button'), res = foot.querySelector('.test-res');
    btn.disabled = true; res.className = 'test-res'; res.textContent = '测试中…';
    try {
      const r = await jsend('/api/admin/settings/test', {target: g}, 'POST');
      res.classList.add(r.ok ? 'ok' : 'fail');
      res.textContent = r.ok ? '✓ 连接正常 · ' + r.detail + ' · ' + r.ms + ' ms' : '✗ ' + r.error;
    } catch (e) {
      if (e.auth) return showLogin('令牌已失效，请重新输入');
      res.classList.add('fail'); res.textContent = '✗ ' + e.message;
    } finally { btn.disabled = false; }
  }

  /* ---------- 对话模型 ---------- */
  // 服务商预设：只预填地址和常用参数，模型 ID 以服务商控制台为准
  const PRESETS = [
    {key: 'deepseek', label: 'DeepSeek', base: 'https://api.deepseek.com', env: 'DEEPSEEK_API_KEY',
      models: ['deepseek-flash', 'deepseek-v4-pro'], temp: 0.5, extra: {thinking: {type: 'disabled'}}},
    {key: 'deepseek-think', label: 'DeepSeek · 思考模式', base: 'https://api.deepseek.com', env: 'DEEPSEEK_API_KEY',
      models: ['deepseek-flash', 'deepseek-v4-pro'], extra: {thinking: {type: 'enabled'}, reasoning_effort: 'high'}, replay: true,
      hint: '思考模式不支持 temperature；带工具调用时需回传思考内容（已勾选）'},
    {key: 'dashscope', label: '阿里云百炼（通义千问）', base: 'https://dashscope.aliyuncs.com/compatible-mode/v1', models: ['qwen-plus', 'qwen-max', 'qwen-turbo']},
    {key: 'siliconflow', label: '硅基流动', base: 'https://api.siliconflow.cn/v1', models: ['deepseek-ai/DeepSeek-V4-Flash', 'Qwen/Qwen3-235B-A22B']},
    {key: 'moonshot', label: '月之暗面（Kimi）', base: 'https://api.moonshot.cn/v1'},
    {key: 'zhipu', label: '智谱（GLM）', base: 'https://open.bigmodel.cn/api/paas/v4'},
    {key: 'ark', label: '火山方舟（豆包）', base: 'https://ark.cn-beijing.volces.com/api/v3', hint: '模型 ID 填控制台中的推理接入点 ID 或模型名'},
    {key: 'openai', label: 'OpenAI', base: 'https://api.openai.com/v1'},
    {key: 'openrouter', label: 'OpenRouter', base: 'https://openrouter.ai/api/v1', hint: '模型 ID 形如 厂商/模型'},
    {key: 'custom', label: '自定义（OpenAI 兼容）', base: ''},
  ];
  let modelList = [], editing = null;

  function setModels(list) { modelList = list; renderModels(); }

  function renderModels() {
    const box = $('models'); box.replaceChildren();
    const sec = el('section', 'sec');
    const head = el('div', 'sec-head');
    const left = el('div');
    const h = el('h3'); h.innerHTML = icon('bot'); h.append('对话模型');
    left.append(h, el('p', 'desc', '支持任意 OpenAI 兼容的 Chat Completions 接口。标记为"默认"的模型用于新对话；启用的模型访客都能在对话框中切换。'));
    const add = el('button', 'iconbtn primary'); add.type = 'button'; add.innerHTML = icon('plus'); add.append('添加模型');
    add.addEventListener('click', () => openModel(null));
    head.append(left, add);
    const list = el('div', 'box');
    if (!modelList.length) list.append(el('div', 'frow', '还没有模型，点击"添加模型"。没有可用模型时对话功能不可用。'));
    for (const m of modelList) list.append(modelRow(m));
    sec.append(head, list);
    box.append(sec);
  }

  function modelRow(m) {
    const row = el('div', 'mrow' + (m.enabled ? '' : ' off'));
    const info = el('div');
    const n = el('div', 'mn', m.name);
    if (m.is_default) n.append(el('span', 'badge user', '默认'));
    if (!m.enabled) n.append(el('span', 'badge', '已停用'));
    if (!m.supports_tools) n.append(el('span', 'badge', '无工具'));
    if (m.replay_reasoning) n.append(el('span', 'badge', '思考'));
    if (!m.key_ready) n.append(el('span', 'badge warn', m.key_env ? '环境变量 ' + m.key_env + ' 未设置' : '未设置 Key'));
    info.append(n, el('div', 'mm', m.model + ' @ ' + m.host));
    if (m.description) info.append(el('div', 'md', m.description));
    const ops = el('div', 'ops');
    const b = (label, fn, cls) => { const x = el('button', 'iconbtn' + (cls ? ' ' + cls : ''), label); x.type = 'button'; x.addEventListener('click', fn); ops.append(x); return x; };
    const res = el('div', 'test-res'); res.setAttribute('aria-live', 'polite');
    const tb = b('测试', async () => { tb.disabled = true; await runTest({...toBody(m), id: m.id}, res); tb.disabled = false; });
    if (!m.is_default && m.enabled) b('设为默认', () => act(api('/api/admin/models/' + m.id + '/default', {method: 'POST'}), '已设为默认'));
    b('编辑', () => openModel(m));
    if (!m.is_default) b(m.enabled ? '停用' : '启用', () => act(jsend('/api/admin/models/' + m.id, {...toBody(m), enabled: !m.enabled}, 'PUT'), m.enabled ? '已停用' : '已启用'));
    b('删除', () => {
      if (!confirm('删除模型「' + m.name + '」？使用它的会话将改用默认模型。')) return;
      act(api('/api/admin/models/' + m.id, {method: 'DELETE'}), '已删除');
    }, 'danger');
    row.append(info, ops, res);
    return row;
  }

  // 现有模型 -> 请求体（不含 Key，Key 留空表示保留）
  const toBody = m => ({name: m.name, description: m.description, base_url: m.base_url, model: m.model, api_key: '',
    key_env: m.key_env, temperature: m.temperature, max_tokens: m.max_tokens, extra_body: m.extra_body,
    supports_tools: m.supports_tools, replay_reasoning: m.replay_reasoning, enabled: m.enabled, sort: m.sort});

  async function act(promise, msg) {
    try { const d = await promise; setModels(d.models); toast(msg); }
    catch (e) { if (e.auth) showLogin('令牌已失效，请重新输入'); else toast(e.message, true); }
  }

  async function runTest(body, res) {
    res.className = 'test-res'; res.textContent = '测试中…（最长约 1 分钟）';
    try {
      const r = await jsend('/api/admin/models/test', body, 'POST');
      res.classList.add(r.ok ? (r.tools_ok === false && body.supports_tools ? 'fail' : 'ok') : 'fail');
      res.textContent = r.ok ? '✓ ' + r.detail + ' · ' + r.ms + ' ms' : '✗ ' + r.error;
    } catch (e) {
      if (e.auth) return showLogin('令牌已失效，请重新输入');
      res.classList.add('fail'); res.textContent = '✗ ' + e.message;
    }
  }

  /* 添加 / 编辑对话框 */
  const sel = $('m_preset');
  PRESETS.forEach(p => { const o = el('option', '', p.label); o.value = p.key; sel.append(o); });
  function applyPreset(key, fill) {
    const p = PRESETS.find(x => x.key === key) || PRESETS[PRESETS.length - 1];
    $('m_modelList').replaceChildren(...(p.models || []).map(v => { const o = el('option'); o.value = v; return o; }));
    $('m_presetHint').textContent = p.hint || (p.models ? '可选模型：' + p.models.join('、') + '（以服务商控制台为准）' : '模型 ID 请在服务商控制台查看');
    if (!fill) return;
    $('m_base').value = p.base;
    if (p.models && p.models.length) $('m_model').value = p.models[0];
    if (!$('m_name').value || $('m_name').dataset.auto === '1') { $('m_name').value = p.key === 'custom' ? '' : p.label; $('m_name').dataset.auto = '1'; }
    $('m_keyEnv').value = p.env || '';
    $('m_temp').value = p.temp != null ? p.temp : '';
    $('m_extra').value = p.extra ? JSON.stringify(p.extra, null, 2) : '';
    $('m_replay').checked = !!p.replay;
  }
  sel.addEventListener('change', () => applyPreset(sel.value, true));
  $('m_name').addEventListener('input', () => { $('m_name').dataset.auto = ''; });

  function guessPreset(m) {
    const hit = PRESETS.find(p => p.base && m.base_url === p.base && (!!p.replay) === !!m.replay_reasoning);
    return hit ? hit.key : 'custom';
  }

  function openModel(m) {
    editing = m;
    $('mTitle').textContent = m ? '编辑模型' : '添加模型';
    $('mErr').textContent = ''; $('mTestRes').textContent = ''; $('mTestRes').className = 'test-res';
    sel.value = m ? guessPreset(m) : 'deepseek';
    $('m_name').value = m ? m.name : ''; $('m_name').dataset.auto = m ? '' : '1';
    applyPreset(sel.value, !m);
    if (m) {
      $('m_model').value = m.model; $('m_desc').value = m.description || ''; $('m_base').value = m.base_url;
      $('m_keyEnv').value = m.key_env || '';
      $('m_temp').value = m.temperature != null ? m.temperature : ''; $('m_max').value = m.max_tokens || '';
      $('m_extra').value = Object.keys(m.extra_body || {}).length ? JSON.stringify(m.extra_body, null, 2) : '';
      $('m_replay').checked = m.replay_reasoning; $('m_tools').checked = m.supports_tools; $('m_enabled').checked = m.enabled;
      $('m_sort').value = m.sort || 0;
    } else {
      $('m_desc').value = ''; $('m_max').value = ''; $('m_tools').checked = true; $('m_enabled').checked = true;
      $('m_sort').value = modelList.length;
    }
    $('m_key').value = '';
    $('m_key').placeholder = m && m.key_display ? m.key_display + '（留空不修改）' : '粘贴 API Key';
    $('m_enabled').disabled = !!(m && m.is_default);
    $('mdlg').showModal();
    (m ? $('m_name') : sel).focus();
  }

  function formBody() {
    let extra = {};
    const raw = $('m_extra').value.trim();
    if (raw) {
      try { extra = JSON.parse(raw); } catch { throw new Error('附加请求参数不是合法的 JSON'); }
      if (!extra || typeof extra !== 'object' || Array.isArray(extra)) throw new Error('附加请求参数必须是 JSON 对象');
    }
    const num = (v, int) => { v = v.trim(); if (!v) return null; const n = int ? parseInt(v, 10) : parseFloat(v); if (Number.isNaN(n)) throw new Error('请输入数字'); return n; };
    return {
      name: $('m_name').value.trim(), description: $('m_desc').value.trim(), base_url: $('m_base').value.trim(),
      model: $('m_model').value.trim(), api_key: $('m_key').value.trim(), key_env: $('m_keyEnv').value.trim(),
      temperature: num($('m_temp').value), max_tokens: num($('m_max').value, true), extra_body: extra,
      supports_tools: $('m_tools').checked, replay_reasoning: $('m_replay').checked, enabled: $('m_enabled').checked,
      sort: parseInt($('m_sort').value, 10) || 0,
    };
  }

  $('mTest').addEventListener('click', async () => {
    let body;
    try { body = formBody(); } catch (e) { $('mErr').textContent = e.message; return; }
    $('mErr').textContent = ''; $('mTest').disabled = true;
    await runTest(editing ? {...body, id: editing.id} : body, $('mTestRes'));
    $('mTest').disabled = false;
  });

  $('mForm').addEventListener('submit', async e => {
    if (e.submitter && e.submitter.value === 'cancel') return;
    e.preventDefault();
    let body;
    try { body = formBody(); } catch (err) { $('mErr').textContent = err.message; return; }
    $('mOk').disabled = true;
    try {
      const d = editing ? await jsend('/api/admin/models/' + editing.id, body, 'PUT') : await jsend('/api/admin/models', body, 'POST');
      $('mdlg').close(); setModels(d.models); toast(editing ? '已保存' : '已添加「' + d.model.name + '」');
    } catch (err) {
      if (err.auth) { $('mdlg').close(); showLogin('令牌已失效，请重新输入'); }
      else $('mErr').textContent = err.message;
    } finally { $('mOk').disabled = false; }
  });

  /* ---------- 启动 ---------- */
  if (token) enter(token).catch(e => showLogin(e.auth ? '' : e.message));
  else showLogin();
})();

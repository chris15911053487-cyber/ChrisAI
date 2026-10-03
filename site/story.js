/* 故事页渲染器：story.html?course=<课程>&lesson=<课>
   播放 courses/<课程>/stories/<课>.html 里的声明式内容片段（写法见 courses/ai-intro/stories/README.md）。
   引擎源自第4课 demo：全屏金句（.beat）、对话舞台（.scene > .phone）、白板舞台（.scene > .board）、滚动旁白（.step）、
   互动解锁（data-act=choice|pick + data-unlock / data-gate）、窄屏点击推进的故事模式。
   平台部分：登录门槛、阅读进度保存到账号并续读、侧栏切换课、末尾自动追加「完成」页。
   依赖：auth.js（window.clAuth）。内容片段经 storycheck 校验（无脚本），课程元信息一律转义后写入。 */
(function(){
  'use strict';
  var $=function(s,r){return (r||document).querySelector(s);};
  var $$=function(s,r){return [].slice.call((r||document).querySelectorAll(s));};
  var esc=function(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});};
  var enc=encodeURIComponent;
  var qs=new URLSearchParams(location.search);
  var CID=qs.get('course')||'ai-intro',LID=qs.get('lesson')||'';
  var API='/api/courses/'+enc(CID);
  var mainEl=$('main');
  var catalogUrl='course.html?id='+enc(CID);
  var storyUrl=function(lid){return 'story.html?course='+enc(CID)+'&lesson='+enc(lid);};

  function api(path,opts){
    return fetch(path,Object.assign({credentials:'same-origin'},opts||{})).then(function(r){
      return r.json().catch(function(){return null;}).then(function(d){
        if(!r.ok){var e=new Error((d&&d.detail)||('请求失败（'+r.status+'）'));e.status=r.status;throw e;}
        return d;
      });
    });
  }

  /* ---------- 提示条 ---------- */
  var toastEl=$('#stoast'),toastT;
  function toast(small,big,err){
    toastEl.innerHTML=(small?'<small>'+esc(small)+'</small>':'')+'<b>'+esc(big)+'</b>';
    toastEl.classList.toggle('err',!!err);
    toastEl.classList.add('on');clearTimeout(toastT);
    toastT=setTimeout(function(){toastEl.classList.remove('on');},err?3600:1900);
  }

  /* ---------- 加载 / 未登录 / 制作中 ---------- */
  function gate(small,title,text,btns){
    document.body.classList.add('gated');
    mainEl.innerHTML='<section class="gate">'+(small?'<div class="small">'+esc(small)+'</div>':'')+'<h2>'+esc(title)+'</h2>'+
      (text?'<p>'+esc(text)+'</p>':'')+'<div class="row">'+(btns||[]).map(function(b){
        return b.href?'<a class="btn'+(b.pri?' pri':'')+'" href="'+esc(b.href)+'">'+esc(b.t)+'</a>'
          :'<button type="button" class="btn'+(b.pri?' pri':'')+'" data-gb="'+esc(b.id)+'">'+esc(b.t)+'</button>';
      }).join('')+'</div></section>';
    var lb=$('[data-gb="login"]',mainEl);
    if(lb)lb.addEventListener('click',function(){window.clAuth&&window.clAuth.openAuth('login');});
  }
  var backBtn={t:'← 课程目录',href:catalogUrl};

  var course=null,lesson=null;
  function start(){
    gate('','加载中…');
    api(API).then(function(c){
      course=c;
      lesson=(c.lessons||[]).filter(function(l){return l.id===LID;})[0];
      if(!lesson){gate(c.title||'','这节课不存在','',[backBtn]);return;}
      var label=c.title+' · 第'+lesson.no+'课';
      document.title='第'+lesson.no+'课 · '+lesson.title+'｜'+c.title;
      if(!lesson.modes.story.available){gate(label,lesson.title,'这节课正在制作中，敬请期待。',[backBtn]);return;}
      if(!c.logged_in){gate(label,lesson.title,(lesson.solves||'')+' 登录后即可开始学习，进度会保存在你的账号里。',[backBtn,{t:'登录 / 注册',id:'login',pri:true}]);return;}
      return api(API+'/lessons/'+enc(LID)+'/story').then(function(d){play(d.html,d.progress||{});});
    }).catch(function(e){
      if(e.status===401){gate('','请先登录','',[backBtn,{t:'登录 / 注册',id:'login',pri:true}]);return;}
      gate('','加载失败',e.message+'，请稍后刷新重试。',[backBtn]);
    });
  }
  // 登录态就绪后开始；之后登录 / 退出直接刷新页面
  if(window.clAuth){
    var lastLogged;
    window.clAuth.onChange(function(st){
      if(!st.loaded||st.logged_in===lastLogged)return;
      var first=lastLogged===undefined;lastLogged=st.logged_in;
      if(first)start();else location.reload();
    });
  }else start();

  /* =====================================================================
   * 播放
   * ===================================================================== */
  function play(html,progress){
    document.body.classList.remove('gated');
    mainEl.innerHTML=html;
    var saved=(progress&&progress.state&&progress.state.v===1)?progress.state:null;

    /* ---------- 侧栏头：课程切换 + 片段里的 story-meta ---------- */
    var meta=$('.story-meta',mainEl),STAGES={};
    (meta?(meta.getAttribute('data-stages')||''):'').split('|').forEach(function(n,i){if(n)STAGES[i+1]='阶段'+'一二三四五六七八九'.charAt(i)+' · '+n;});
    var prog=course.progress||{};
    var sw=course.lessons.map(function(l){
      var t='第'+l.no+'课 · '+l.title;
      if(l.id===LID)return '<a class="on" href="'+esc(storyUrl(l.id))+'" aria-current="page" title="'+esc(t)+'">'+l.no+'</a>';
      if(!l.modes.story.available)return '<span title="'+esc(t)+'（制作中）">'+l.no+'</span>';
      var ok=prog[l.id]&&prog[l.id].story&&prog[l.id].story.done;
      return '<a class="'+(ok?'ok':'')+'" href="'+esc(storyUrl(l.id))+'" title="'+esc(t)+(ok?'（已完成）':'')+'">'+l.no+'</a>';
    }).join('');
    $('#sideHd').innerHTML='<div class="side-course"><a class="home" href="'+esc(catalogUrl)+'">← '+esc(course.title)+' · 课程目录</a>'+
      '<nav class="lsw" aria-label="切换课程">'+sw+'</nav></div><div class="side-hd">'+(meta?meta.innerHTML:'<div class="t">'+esc(lesson.title)+'</div>')+'</div>';
    if(meta)meta.remove();

    /* ---------- 末尾追加「完成」页 ---------- */
    var idx=course.lessons.indexOf(lesson),next=course.lessons[idx+1];
    var tools=(course.tools||[]).filter(function(t){return (lesson.takeaways||[]).indexOf(t.id)>-1;});
    var fin=document.createElement('div');fin.className='chapter';fin.setAttribute('data-title','完成');fin.setAttribute('data-stage','0');
    fin.innerHTML='<section class="beat st"><div class="small reveal">'+esc(course.title)+' · 第'+lesson.no+'课 · 完成</div>'+
      '<h2 class="reveal">这节课，<em>学完了</em>。</h2>'+
      (tools.length?'<p class="reveal">这些工具卡已收进你的工具箱，随时可以在课程目录里翻看。</p><div class="fin-tools reveal">'+tools.map(function(t){return '<span>'+esc(t.name)+'</span>';}).join('')+'</div>':'')+
      '<div class="row reveal"><a class="btn" href="'+esc(catalogUrl)+'">回到课程目录</a>'+
      (next?(next.modes.story.available?'<a class="btn pri" href="'+esc(storyUrl(next.id))+'">下一课 · 第'+next.no+'课 →</a>'
        :'<span class="btn" aria-disabled="true">第'+next.no+'课 · 制作中</span>'):'')+'</div></section>';
    mainEl.appendChild(fin);

    engine(saved,!!(progress&&progress.done),STAGES);
  }

  /* =====================================================================
   * 引擎
   * ===================================================================== */
  function engine(saved,wasDone,STAGES){
  var reduce=window.matchMedia&&matchMedia('(prefers-reduced-motion: reduce)').matches;
  var hasIO='IntersectionObserver' in window;
  var MOB=false; /* 手机故事模式 */

  /* ---------- 淡入 ---------- */
  var revealIO=hasIO?new IntersectionObserver(function(es){
    es.forEach(function(e){if(e.isIntersecting){e.target.classList.add('on');revealIO.unobserve(e.target);}});
  },{threshold:.2}):null;
  function watchReveal(root){$$('.reveal',root).forEach(function(el){revealIO?revealIO.observe(el):el.classList.add('on');});}
  watchReveal(mainEl);

  /* ---------- 数据模型：章节 → 步骤 ---------- */
  var chapters=$$('.chapter',mainEl).map(function(el){
    return {el:el,no:el.getAttribute('data-no')||'',title:el.getAttribute('data-title'),
      stage:+(el.getAttribute('data-stage')||0),desc:el.getAttribute('data-desc')||'',
      act:el.getAttribute('data-badge')||'',steps:[]};
  });
  function chOf(el){var w=el.closest('.chapter');for(var i=0;i<chapters.length;i++)if(chapters[i].el===w)return chapters[i];return null;}
  var steps=$$('.st',mainEl).map(function(el,i){
    var ch=chOf(el),s={el:el,i:i,ch:ch,k:ch.steps.length,act:el.hasAttribute('data-act')};
    ch.steps.push(s);el._st=s;return s;
  });
  chapters=chapters.filter(function(ch){return ch.steps.length;});
  function chLabel(ch){return ch.no?ch.no+' · '+ch.title:ch.title;}
  function isLocked(s){return !!s.el.closest('.locked');}
  var cur=0,visited={0:1};
  var finStep=steps[steps.length-1];

  /* ---------- 每一步：章节名 + 第几步 + 分段条 + 下一步按钮 ---------- */
  steps.forEach(function(s){
    var host=s.el.classList.contains('step')?$('.card',s.el):s.el,n=s.ch.steps.length,h='';
    if(host!==s.el){s.el._card=host;host._home=s.el;}
    var meta=document.createElement('div');meta.className='meta';
    meta.innerHTML='<span>'+esc(chLabel(s.ch))+'</span><span class="meta-n">第 '+(s.k+1)+' / '+n+' 步</span>';
    var seg=document.createElement('div');seg.className='seg';seg.setAttribute('aria-hidden','true');
    for(var j=0;j<n;j++)h+='<i class="'+(j<s.k?'f':j===s.k?'c':'')+'"></i>';
    seg.innerHTML=h;
    host.insertBefore(seg,host.firstChild);host.insertBefore(meta,seg);
    var b=document.createElement('button');b.type='button';b.className='next';
    b.addEventListener('click',function(){go(s.i+1);});
    host.appendChild(b);s.next=b;
  });
  function refreshNext(){
    steps.forEach(function(s){
      var n=steps[s.i+1],b=s.next;
      if(!n){b.hidden=true;return;}
      var c=chatOf(s);
      if(c&&c.pending&&s.i===cur){b.disabled=false;b.className='next sendnow';b.textContent='发送这句话 ↗';return;}
      if(isLocked(n)){b.disabled=true;b.className='next';b.textContent=s.el.getAttribute('data-wait')||'先完成上面的互动';return;}
      b.disabled=false;
      if(n.ch!==s.ch){b.className='next ch';b.textContent=(n===finStep?'完成本课':'下一章 · '+chLabel(n.ch))+' →';}
      else{b.className='next';b.textContent=s.i===0?'继续 ↓':'下一步 ↓';}
    });
    if(MOB)mobBar();
  }

  /* ---------- 白板舞台：按步骤点亮 ---------- */
  function applyBoard(step){
    var scene=step.closest('.scene'),stage=$('.stage',scene),board=$('.board',scene);
    var show=(step.getAttribute('data-show')||'').split(',').filter(Boolean),last=null;
    $$('[data-i]',board).forEach(function(m){
      var on=show.indexOf(m.getAttribute('data-i'))>-1;
      m.classList.toggle('on',on);if(on)last=m;
    });
    stage.className='stage '+(step.getAttribute('data-fx')||'');
    requestAnimationFrame(function(){
      if(last)board.scrollTop=Math.max(0,last.offsetTop+last.offsetHeight-board.clientHeight+20);
    });
  }

  /* ---------- 对话舞台：AI 流式输出 + 预填输入框，由读者点击发送 ---------- */
  var SEND_SVG='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 19V5M5 12l7-7 7 7"/></svg>';
  function kind(m){return m.classList.contains('sys')?'sys':m.classList.contains('u')?'u':'a';}
  function Chat(scene){
    var self=this,phone=$('.phone',scene),hd=$('.phone-hd',phone),role=$('b',hd).textContent;
    self.stage=$('.stage',scene);self.judge=phone.hasAttribute('data-judge');
    hd.innerHTML='<span class="hd-av" aria-hidden="true">AI</span><span class="hd-tx"><b>AI Chat</b><small>在线</small></span><span class="count"></span>';
    var r=document.createElement('div');r.className='role';r.textContent='你的身份：'+role;
    phone.insertBefore(r,hd.nextSibling);
    self.status=$('small',hd);self.count=$('.count',hd);self.body=$('.body',phone);
    var em=document.createElement('div');em.className='empty';em.innerHTML='<b>今天想聊点什么？</b>问题会出现在下方输入框里';
    self.body.insertBefore(em,self.body.firstChild);
    self.msgs=$$('.m',self.body);
    self.msgs.forEach(function(m){
      var row=document.createElement('div'),k=kind(m);
      row.className='row '+(k==='u'?'me':k==='a'?'ai':'sys');
      self.body.insertBefore(row,m);
      if(k!=='sys'){var av=document.createElement('span');av.className='av';av.setAttribute('aria-hidden','true');av.textContent=k==='u'?'你':'AI';row.appendChild(av);}
      row.appendChild(m);m._row=row;m._html=m.innerHTML;
    });
    var cmp=document.createElement('div');cmp.className='cmp';
    cmp.innerHTML='<div class="cmp-box" data-ph="给 AI 发消息…"></div><button type="button" class="send" aria-label="发送" disabled>'+SEND_SVG+'</button><span class="send-tip" aria-hidden="true">点击发送 ↵</span>';
    phone.appendChild(cmp);
    self.phone=phone;cmp._home=phone;
    self.cmp=cmp;self.box=$('.cmp-box',cmp);self.sendBtn=$('.send',cmp);
    self.sendBtn.addEventListener('click',function(){self.send();});
    self.shown={};self.queue=[];self.cur=null;self.pending=null;self.timers=[];self.typingRow=null;
    self.updateCount();
  }
  var CP=Chat.prototype;
  CP.t=function(fn,ms,iv){var id=(iv?setInterval:setTimeout)(fn,ms);this.timers.push(id);return id;};
  CP.clear=function(){this.timers.forEach(function(id){clearTimeout(id);clearInterval(id);});this.timers=[];};
  CP.scroll=function(){this.body.scrollTop=this.body.scrollHeight;};
  CP.busy=function(b){this.status.textContent=b?'正在输入…':'在线';this.status.classList.toggle('busy',b);this.onChange();};
  CP.setBox=function(text,state){
    this.box.textContent=text;
    this.box.classList.toggle('typing',state==='typing');
    this.cmp.classList.toggle('ready',state==='ready');
    this.sendBtn.disabled=state!=='ready';
    this.onChange();
  };
  CP.show=function(m,full){
    if(MOB)this.body.appendChild(m._row);
    m._row.classList.add('on');
    if(full&&kind(m)==='a'){m.innerHTML=m._html;m.classList.remove('streaming');}
    this.shown[m.getAttribute('data-i')]=1;this.body.classList.add('has');
  };
  CP.hide=function(m){m._row.classList.remove('on');delete this.shown[m.getAttribute('data-i')];};
  CP.forget=function(ids){var self=this;this.msgs.forEach(function(m){if(ids.indexOf(m.getAttribute('data-i'))>-1)self.hide(m);});};
  CP.updateCount=function(){
    var users=$$('.row.me.on',this.body).length;
    if(this.judge){
      var j=$$('.row.me.on .m.j',this.body).length;
      this.count.textContent='说了 '+users+' 句 · 带判断 '+j+' 句';this.count.classList.toggle('one',users>1&&j===0);
    }else{this.count.textContent='你说了 '+users+' 句';this.count.classList.toggle('one',users===1);}
  };
  /* 立即完成所有排队中的动画（滚动过快 / 往回翻时） */
  CP.flush=function(){
    var self=this;this.clear();
    if(this.typingRow){this.typingRow.remove();this.typingRow=null;}
    var rest=this.queue;this.queue=[];
    if(this.cur){rest.unshift(this.cur);this.cur=null;}
    rest.forEach(function(it){if(it.fn)it.fn();else self.show(it.m,true);});
    this.pending=null;this.setBox('','idle');this.busy(false);this.updateCount();this.scroll();
  };
  CP.apply=function(step,animate,after){
    var self=this,ids=(step.getAttribute('data-show')||'').split(',').filter(Boolean),add=[];
    var keep=step.getAttribute('data-keep');
    this.flush();this.fast=false;
    if(MOB)this.place(step);
    this.msgs.forEach(function(m){
      var id=m.getAttribute('data-i'),want=ids.indexOf(id)>-1;
      if(!want&&self.shown[id]&&!MOB)self.hide(m);
      if(want&&!self.shown[id])add.push(m);
      m._row.classList.toggle('keep',want&&!!keep&&id===keep);
    });
    this.stage.className='stage '+(step.getAttribute('data-fx')||'');
    if(!animate){
      add.forEach(function(m){self.show(m,true);});
      this.updateCount();this.scroll();if(after)after();this.onChange();return;
    }
    this.queue=add.map(function(m){return {m:m};});
    if(after)this.queue.push({fn:after});
    this.run();
  };
  CP.run=function(){
    var self=this,it=this.queue.shift();
    this.cur=it||null;
    if(!it){this.busy(false);this.onChange();return;}
    if(it.fn){it.fn();this.cur=null;this.run();return;}
    var m=it.m,k=kind(m);
    if(k==='u'){
      var text=m.textContent.trim();
      this.typeInto(text,function(){self.pending=it;self.setBox(text,'ready');self.onChange();});
    }else if(k==='sys'){
      this.t(function(){self.show(m,true);self.scroll();self.cur=null;self.run();},300);
    }else{
      this.busy(true);
      var tr=document.createElement('div');tr.className='row ai on';
      tr.innerHTML='<span class="av" aria-hidden="true">AI</span><div class="m"><span class="dots3"><i></i><i></i><i></i></span></div>';
      if(MOB)this.body.appendChild(tr);else this.body.insertBefore(tr,m._row);this.typingRow=tr;this.body.classList.add('has');this.scroll();
      this.t(function(){
        tr.remove();self.typingRow=null;self.show(m,false);
        self.stream(m,function(){self.cur=null;self.run();});
      },600);
    }
  };
  /* 把预留的话逐字“打”进输入框 */
  CP.typeInto=function(text,done){
    var self=this,i=0,per=Math.max(1,Math.ceil(text.length/40)),id;
    this.setBox('','typing');
    this.t(function(){
      id=self.t(function(){
        i=Math.min(text.length,i+per);
        self.box.textContent=text.slice(0,i);self.box.scrollTop=self.box.scrollHeight;
        if(i>=text.length){clearInterval(id);self.box.classList.remove('typing');done();}
      },28,true);
    },250);
  };
  /* AI 回复逐字流式输出（保留原有标记） */
  CP.stream=function(m,done){
    var self=this,nodes=[],full,n,ni=0,ci=0,id;
    m.innerHTML=m._html;
    var w=document.createTreeWalker(m,NodeFilter.SHOW_TEXT,null);
    while((n=w.nextNode()))if(n.nodeValue.trim())nodes.push(n);
    full=nodes.map(function(x){return x.nodeValue;});
    nodes.forEach(function(x){x.nodeValue='';});
    var total=full.join('').length,per=Math.max(1,Math.ceil(total/75));
    m.classList.add('streaming');
    id=this.t(function(){
      var left=self.fast?Infinity:per;
      while(left>0&&ni<nodes.length){
        var take=Math.min(left,full[ni].length-ci);
        ci+=take;left-=take;nodes[ni].nodeValue=full[ni].slice(0,ci);
        if(ci>=full[ni].length){ni++;ci=0;}
      }
      self.scroll();
      if(ni>=nodes.length){clearInterval(id);m.classList.remove('streaming');done();}
    },24,true);
  };
  CP.send=function(){
    var it=this.pending,self=this;if(!it)return;
    this.pending=null;this.setBox('','idle');
    this.show(it.m,true);this.updateCount();this.scroll();this.cur=null;this.onChange();
    this.t(function(){self.run();},350);
  };
  CP.onChange=function(){refreshNext();};
  CP.isBusy=function(){return !!this.cur&&!this.pending&&!this.box.classList.contains('typing');};
  /* 手机：把旁白卡片放进对话流 */
  CP.place=function(step){
    var c=step._card;
    if(c&&c.parentNode!==this.body){this.body.appendChild(c);this.body.classList.add('has');}
  };
  /* 手机：跳转 / 回退时，按顺序重放本场景到目标步骤 */
  CP.rebuild=function(step){
    var self=this,scene=this.stage.closest('.scene'),to=step._st.i;
    this.flush();
    $$('.body > .card',this.phone).forEach(function(c){c._home.appendChild(c);});
    this.msgs.forEach(function(m){self.hide(m);});
    this.body.classList.remove('has');
    steps.forEach(function(x){
      if(x.i<=to&&x.el.closest('.scene')===scene&&!isLocked(x))self.apply(x.el,false);
    });
  };
  /* 切换桌面 / 手机时，恢复原始 DOM 顺序 */
  CP.reset=function(){
    var self=this;this.flush();
    $$('.body > .card',this.phone).forEach(function(c){c._home.appendChild(c);});
    this.cmp.hidden=false;
    if(this.cmp.parentNode!==this.phone)this.phone.appendChild(this.cmp);
    this.msgs.forEach(function(m){self.hide(m);self.body.appendChild(m._row);});
    this.body.classList.remove('has');this.updateCount();
  };
  $$('.scene',mainEl).forEach(function(sc){if($('.phone',sc))sc._chat=new Chat(sc);});
  function chatOf(s){var sc=s&&s.el.closest('.scene');return sc&&sc._chat;}

  /* ---------- 左侧总览 ---------- */
  var list=$('#sideList'),html='',lastStage=-1;
  chapters.forEach(function(ch,ci){
    if(ch.stage!==lastStage){if(ch.stage&&STAGES[ch.stage])html+='<div class="sg sg'+(((ch.stage-1)%3)+1)+'">'+esc(STAGES[ch.stage])+'</div>';lastStage=ch.stage;}
    html+='<div class="ch-item" data-ci="'+ci+'"><button type="button" class="ch-btn"><span class="ch-no">'+esc(ch.no||(ci===chapters.length-1?'终':'序'))+'</span><span class="ch-tt">'+esc(ch.title)+'</span>'+
      (ch.act?'<span class="ch-act">互动 · '+esc(ch.act)+'</span>':'')+'<span class="ch-ic" aria-hidden="true"></span></button>'+
      (ch.desc?'<div class="ch-desc">'+esc(ch.desc)+'</div>':'')+'<div class="ch-dots">';
    ch.steps.forEach(function(s){
      html+='<button type="button" class="dot'+(s.act?' x':'')+'" data-si="'+s.i+'" aria-label="'+esc(chLabel(ch))+' 第 '+(s.k+1)+' 步'+(s.act?'（互动）':'')+'"></button>';
    });
    html+='</div></div>';
  });
  list.innerHTML=html;
  var items=$$('.ch-item',list),dots=$$('.dot',list);
  list.addEventListener('click',function(e){
    var d=e.target.closest('.dot');
    if(d){go(+d.getAttribute('data-si'));return;}
    var b=e.target.closest('.ch-btn');
    if(b)go(chapters[+b.parentNode.getAttribute('data-ci')].steps[0].i);
  });
  function renderSide(){
    var s=steps[cur],vn=0;
    for(var k in visited)vn++;
    dots.forEach(function(d){
      var i=+d.getAttribute('data-si');
      d.classList.toggle('c',i===cur);
      d.classList.toggle('v',!!visited[i]&&i!==cur);
      d.classList.toggle('l',isLocked(steps[i]));
      if(i===cur)d.setAttribute('aria-current','step');else d.removeAttribute('aria-current');
    });
    items.forEach(function(it,ci){
      var ch=chapters[ci];
      it.classList.toggle('cur',ch===s.ch);
      it.classList.toggle('done',ch!==s.ch&&ch.steps.every(function(x){return visited[x.i];}));
      it.classList.toggle('lk',ch.steps.every(isLocked));
    });
    $('#ovN').textContent='已看 '+vn+' / '+steps.length+' 步';
    $('#ovBar').style.width=(vn/steps.length*100)+'%';
    var it=items[chapters.indexOf(s.ch)],lr=list.getBoundingClientRect(),r=it.getBoundingClientRect();
    if(r.top<lr.top||r.bottom>lr.bottom)list.scrollTop+=r.top-lr.top-60;
  }

  /* ---------- 阅读进度：保存到账号，读到「完成」页记为完成 ---------- */
  var gatesOpen=[],choiceSel={},pickSel={},saveT=null,doneSent=wasDone,saveWarned=false,resetting=false;
  var progUrl=API+'/lessons/'+enc(LID)+'/progress';
  function snapshot(){
    return {v:1,cur:cur,visited:Object.keys(visited).map(Number),gates:gatesOpen.slice(),choice:choiceSel,pick:pickSel};
  }
  function save(){if(resetting)return;clearTimeout(saveT);saveT=setTimeout(function(){flushSave(false);},800);}
  function flushSave(keep){
    saveT=null;
    if(resetting)return;
    var body={mode:'story',state:snapshot()};
    if(visited[finStep.i]&&!doneSent){body.done=true;doneSent=true;}
    fetch(progUrl,{method:'PUT',credentials:'same-origin',keepalive:keep,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
      .then(function(r){
        if(r.ok){saveWarned=false;return;}
        if(body.done)doneSent=false;
        if(!saveWarned){saveWarned=true;toast('进度未保存',r.status===401?'登录已过期，请刷新后重新登录':'保存失败，请检查网络',true);}
      }).catch(function(){if(body.done)doneSent=false;});
  }
  addEventListener('pagehide',function(){if(saveT){clearTimeout(saveT);flushSave(true);}});

  /* ---------- 当前步骤切换 ---------- */
  var progEl=$('.prog'),lastCh=steps[0].ch,shownCh=steps[0].ch,chT;
  function render(){
    var s=steps[cur];
    steps.forEach(function(x){x.el.classList.toggle('cur',x.i===cur);if(x.el._card)x.el._card.classList.toggle('now',x.i===cur);});
    if(MOB)mobSegs();
    if(s.el.classList.contains('step')){
      var dev=$('.phone,.board',s.el.closest('.scene'));
      dev.classList.remove('tick');void dev.offsetWidth;dev.classList.add('tick');
    }
    renderSide();
    $('#tbL').textContent=chLabel(s.ch);
    $('#tbN').textContent=(s.k+1)+' / '+s.ch.steps.length;
    progEl.style.width=((cur+1)/steps.length*100)+'%';
    if(s.ch!==lastCh){
      lastCh=s.ch;clearTimeout(chT);
      chT=setTimeout(function(){
        var c=steps[cur].ch;
        if(c===s.ch&&c!==shownCh){shownCh=c;if(c.no)toast(STAGES[c.stage]||'','第 '+c.no+' 章 · '+c.title);}
      },350);
    }
  }
  function onStep(s){
    if(s.i===cur&&s.el.classList.contains('cur'))return;
    var prev=cur;cur=s.i;visited[s.i]=1;
    if(s.el.classList.contains('step')){
      var c=chatOf(s);
      if(c)c.apply(s.el,!reduce&&s.i===prev+1);else applyBoard(s.el);
    }
    render();refreshNext();save();
  }
  if(hasIO){
    var stIO=new IntersectionObserver(function(es){
      if(MOB)return;
      es.forEach(function(e){if(e.isIntersecting)onStep(e.target._st);});
    },{rootMargin:'-50% 0px -50% 0px'});
    steps.forEach(function(s){stIO.observe(s.el);});
  }

  /* ---------- 跳转 ---------- */
  function blocker(){
    for(var i=0;i<steps.length-1;i++){
      var s=steps[i];
      if(s.el.hasAttribute('data-act')&&!isLocked(s)&&isLocked(steps[i+1]))return s;
    }
    return null;
  }
  function nudge(s){var c=s.el._card||s.el;c.classList.remove('nudge');void c.offsetWidth;c.classList.add('nudge');}
  function scrollToStep(s,instant){
    setOpen(false);
    if(MOB){mobGo(s.i);return;}
    scrollTo({top:s.el.getBoundingClientRect().top+pageYOffset,behavior:(reduce||instant)?'auto':'smooth'});
  }
  function go(i){
    var c=chatOf(steps[cur]);
    if(i===cur+1&&c&&c.pending){c.send();return;}
    var s=steps[i];if(!s)return;
    if(isLocked(s)){
      var b=blocker();if(!b)return;
      if(b.i===cur)nudge(b);
      else{scrollToStep(b);setTimeout(function(){nudge(b);},600);}
      toast('','完成这里的互动后，才能继续');
      return;
    }
    scrollToStep(s);
  }

  /* ---------- 解锁 ---------- */
  function unlock(g){
    if(gatesOpen.indexOf(g)<0)gatesOpen.push(g);
    $$('[data-gate]',mainEl).forEach(function(el){
      if(el.getAttribute('data-gate')!==g||!el.classList.contains('locked'))return;
      el.classList.remove('locked');watchReveal(el);
    });
    refreshNext();renderSide();
  }

  /* ---------- 金句页快问快答：.opts[data-quick] > .opt[data-r]，回应写进同一页的 .reply ---------- */
  $$('.opts[data-quick]',mainEl).forEach(function(box){
    var reply=$('.reply',box.closest('section'));
    $$('.opt',box).forEach(function(b){
      b.addEventListener('click',function(){
        $$('.opt',box).forEach(function(x){x.classList.toggle('sel',x===b);});
        if(reply)reply.textContent=b.getAttribute('data-r')||'';
      });
    });
  });

  /* ---------- 互动：选一句（data-act="choice"） ----------
     选项 .opt[data-c][data-show][data-cls][data-fb] → 文本填进 data-slot 指定的消息，读者点发送；
     下一步的 <template data-res="选项key"> 写入其 .res 作为解读。 */
  $$('.step[data-act="choice"]',mainEl).forEach(function(step){
    var card=step._card,scene=step.closest('.scene'),chat=scene._chat,g=step.getAttribute('data-unlock');
    var slot=$('.m[data-i="'+step.getAttribute('data-slot')+'"]',scene),fb=$('.fb',card);
    var resStep=step.nextElementSibling,base=(step.getAttribute('data-show')||'').split(',');
    var opts=$$('.opt[data-c]',card),extra=[];
    opts.forEach(function(o){(o.getAttribute('data-show')||'').split(',').forEach(function(id){if(id&&base.indexOf(id)<0&&extra.indexOf(id)<0)extra.push(id);});});
    function pickOpt(b,live){
      var show=b.getAttribute('data-show')||'',cls=b.getAttribute('data-cls')||'',key=b.getAttribute('data-c');
      opts.forEach(function(x){x.classList.toggle('sel',x===b);});
      if(live)chat.flush();
      chat.forget(extra);
      slot.className='m u'+(cls?' '+cls:'');slot.textContent=b.getAttribute('data-text')||b.textContent.trim();
      step.setAttribute('data-show',show);step.removeAttribute('data-fx');
      if(resStep){
        resStep.setAttribute('data-show',show);
        var t=$('template[data-res="'+key+'"]',resStep),box=$('.res',resStep);
        if(t&&box)box.innerHTML=t.innerHTML;
      }
      choiceSel[g]=key;unlock(g);
      if(!live){fb.textContent=b.getAttribute('data-fb')||'';return;}
      fb.textContent='已填进输入框——点'+(MOB?'下方':'右侧')+'「发送」，看看会发生什么。';
      chat.apply(step,!reduce,function(){fb.textContent=b.getAttribute('data-fb')||'';});
      save();
    }
    opts.forEach(function(b){b.addEventListener('click',function(){pickOpt(b,true);});});
    step._restore=function(key){var b=opts.filter(function(o){return o.getAttribute('data-c')===key;})[0];if(b)pickOpt(b,false);};
  });

  /* ---------- 互动：找错句（data-act="pick"） ----------
     对话里的 .s[data-ok="1"] 为错句；卡片里 <template data-k="ok|no|skip"> 为反馈，[data-skip] 为“直接告诉我”。 */
  $$('.step[data-act="pick"]',mainEl).forEach(function(step){
    var card=step._card,scene=step.closest('.scene'),stage=$('.stage',scene),body=$('.body',scene),g=step.getAttribute('data-unlock');
    var fb=$('.fb',card),found=false;
    var wrongEl=$('.s[data-ok="1"]',body),msg=wrongEl&&wrongEl.closest('.m');
    var tpl=function(k){var t=$('template[data-k="'+k+'"]',card);return t?t.innerHTML:'';};
    function reveal(k,live){
      var w=$('.s[data-ok="1"]',msg);if(w)w.classList.add('wrong');
      var tmp=document.createElement('div');tmp.innerHTML=msg._html;
      var tw=$('.s[data-ok="1"]',tmp);if(tw){tw.classList.add('wrong');msg._html=tmp.innerHTML;}
      fb.innerHTML=tpl(k);
      if(!found){found=true;pickSel[g]=k;unlock(g);if(live)save();}
    }
    // 事件委托：AI 回复是流式重绘的，句子节点会被替换
    body.addEventListener('click',function(e){
      var s=e.target.closest('.s');if(!s||!msg.contains(s))return;
      if(!stage.classList.contains('pick')&&!found)return;
      if(s.getAttribute('data-ok')==='1')reveal('ok',true);
      else if(!found){
        s.classList.remove('no');void s.offsetWidth;s.classList.add('no');
        fb.innerHTML=tpl('no');
      }
    });
    body.addEventListener('keydown',function(e){
      var s=e.target.closest&&e.target.closest('.s');
      if(s&&(e.key==='Enter'||e.key===' ')){e.preventDefault();e.stopPropagation();s.click();}
    });
    var skip=$('[data-skip]',card);
    if(skip)skip.addEventListener('click',function(){reveal('skip',true);});
    step._restore=function(k){reveal(k==='skip'?'skip':'ok',false);};
  });

  /* ---------- 总览抽屉（窄屏） ---------- */
  var side=$('#side'),scrim=$('#scrim'),ovBtn=$('#ovBtn');
  function setOpen(o){side.classList.toggle('open',o);scrim.classList.toggle('on',o);ovBtn.setAttribute('aria-expanded',String(o));}
  ovBtn.addEventListener('click',function(){setOpen(!side.classList.contains('open'));});
  scrim.addEventListener('click',function(){setOpen(false);});

  /* ---------- 全屏：按 F 或点侧栏按钮切换（浏览器自带的 Esc 退出） ---------- */
  var fsRoot=document.documentElement,fsHint=$('#fsHint');
  var fsReq=fsRoot.requestFullscreen||fsRoot.webkitRequestFullscreen,
      fsExit=document.exitFullscreen||document.webkitExitFullscreen;
  function isFs(){return !!(document.fullscreenElement||document.webkitFullscreenElement);}
  function toggleFs(){
    if(!fsReq){toast('','当前浏览器不支持全屏',true);return;}
    var p=isFs()?fsExit.call(document):fsReq.call(fsRoot);
    if(p&&p.catch)p.catch(function(){toast('','无法进入全屏',true);});
  }
  if(!fsReq)fsHint.hidden=true;   // 如 iPhone Safari 不支持网页全屏

  /* ---------- 重置本课进度：两次点击确认；清除阅读状态与完成记录，然后重新载入 ---------- */
  var resetBtn=$('#resetBtn'),resetT,RESET_TXT='重置本课进度';
  function disarmReset(){clearTimeout(resetT);resetBtn.classList.remove('warn');resetBtn.textContent=RESET_TXT;}
  resetBtn.disabled=false;disarmReset();
  resetBtn.addEventListener('click',function(){
    if(resetting)return;
    if(!resetBtn.classList.contains('warn')){
      resetBtn.classList.add('warn');resetBtn.textContent='再点确认';
      toast('重置本课进度','将清除阅读进度和完成记录（含本课工具卡）');
      resetT=setTimeout(disarmReset,4000);
      return;
    }
    clearTimeout(resetT);resetting=true;resetBtn.disabled=true;
    clearTimeout(saveT);saveT=null;   // 丢弃尚未发出的保存，避免把进度写回去
    fetch(progUrl+'?mode=story&full=true',{method:'DELETE',credentials:'same-origin'}).then(function(r){
      if(!r.ok)throw new Error(r.status===401?'登录已过期，请刷新后重新登录':'请求失败（'+r.status+'）');
      if('scrollRestoration' in history)history.scrollRestoration='manual';
      location.reload();
    }).catch(function(e){
      resetting=false;resetBtn.disabled=false;disarmReset();
      toast('重置失败',e&&e.message&&e.message!=='Failed to fetch'?e.message:'请检查网络后重试',true);
    });
  });

  /* ---------- 键盘：↓ / 空格 下一步，↑ 上一步，F 全屏 ---------- */
  document.addEventListener('keydown',function(e){
    if(e.altKey||e.ctrlKey||e.metaKey)return;
    if(document.querySelector('dialog[open]'))return;   // 登录框打开时不翻页
    var t=e.target,tag=t.tagName||'';
    if(/INPUT|TEXTAREA|SELECT/.test(tag))return;
    var onBtn=tag==='BUTTON'||tag==='A'||(t.getAttribute&&t.getAttribute('role')==='button');
    if(e.key==='Escape'){setOpen(false);return;}
    if((e.key==='f'||e.key==='F')&&!e.repeat){e.preventDefault();toggleFs();return;}
    if(e.key==='Enter'&&!onBtn){var pc=chatOf(steps[cur]);if(pc&&pc.pending){e.preventDefault();pc.send();return;}}
    if(e.key==='ArrowDown'||e.key==='PageDown'||e.key==='j'||(e.key===' '&&!e.shiftKey&&!onBtn)){e.preventDefault();go(cur+1);}
    else if(e.key==='ArrowUp'||e.key==='PageUp'||e.key==='k'||(e.key===' '&&e.shiftKey&&!onBtn)){e.preventDefault();go(cur-1);}
  });

  /* ---------- 手机故事模式：点击推进 ---------- */
  var mact=$('#mact'),mGo=$('#mGo'),mBack=$('#mBack'),segsEl=$('#segs'),tbar=$('.tbar');
  segsEl.innerHTML=chapters.map(function(){return '<i><b></b></i>';}).join('');
  var segBars=$$('b',segsEl);
  function layoutVars(){
    if(!MOB)return;
    var r=document.documentElement.style;
    r.setProperty('--vh',innerHeight+'px');
    r.setProperty('--mt',tbar.offsetHeight+'px');
    r.setProperty('--mb',mact.offsetHeight+'px');
  }
  function mobSegs(){
    var ci=chapters.indexOf(steps[cur].ch);
    segBars.forEach(function(b,i){
      var ch=chapters[i];
      b.style.width=(i<ci?100:i>ci?0:(steps[cur].k+1)/ch.steps.length*100)+'%';
    });
  }
  function mobBar(){
    var s=steps[cur],c=chatOf(s),n=steps[cur+1];
    mBack.disabled=cur===0;
    var showCmp=!!c&&(c.cmp.classList.contains('ready')||c.box.classList.contains('typing'));
    if(c)c.cmp.hidden=!showCmp;
    mGo.hidden=showCmp;mGo.disabled=false;
    if(!showCmp){
      if(c&&c.isBusy()){mGo.className='go skip';mGo.textContent='AI 正在回复… 点此跳过';}
      else if(!n){mGo.className='go wait';mGo.textContent='— 本课完 —';mGo.disabled=true;}
      else if(isLocked(n)){mGo.className='go wait';mGo.textContent=s.el.getAttribute('data-wait')||'先完成上面的互动';}
      else if(n.ch!==s.ch){mGo.className='go ch';mGo.textContent=(n===finStep?'完成本课':'下一章 · '+chLabel(n.ch))+' ›';}
      else{mGo.className='go';mGo.textContent='下一步 ›';}
    }
    layoutVars();
  }
  function mobShow(s,animate){
    var sec=s.el.classList.contains('beat')?s.el:s.el.closest('.scene'),c=chatOf(s);
    $$('.mcur',mainEl).forEach(function(x){if(x!==sec)x.classList.remove('mcur');});
    sec.classList.add('mcur');
    // 当前场景的输入框放进底部操作栏，其余放回各自对话窗
    $$('.cmp',mact).forEach(function(x){if(!c||x!==c.cmp){x.hidden=false;x._home.appendChild(x);}});
    if(c&&c.cmp.parentNode!==mact)mact.insertBefore(c.cmp,mGo);
    if(s.el.classList.contains('beat')){
      s.el.scrollTop=0;
      $$('.reveal',s.el).forEach(function(el){el.classList.add('on');});
    }else if(c){
      if(animate)c.apply(s.el,true);else c.rebuild(s.el);
    }else applyBoard(s.el);
  }
  function mobGo(i){
    var s=steps[i];if(!s||isLocked(s))return;
    var prev=cur;cur=i;visited[i]=1;
    mobShow(s,!reduce&&i===prev+1);
    render();refreshNext();save();
  }
  mGo.addEventListener('click',function(){
    var c=chatOf(steps[cur]);
    if(mGo.classList.contains('skip')&&c){c.fast=true;return;}
    go(cur+1);
  });
  mBack.addEventListener('click',function(){go(cur-1);});
  segsEl.addEventListener('click',function(){setOpen(true);});
  segsEl.addEventListener('keydown',function(e){if(e.key==='Enter'||e.key===' '){e.preventDefault();e.stopPropagation();setOpen(true);}});
  // 金句页：点空白处继续
  mainEl.addEventListener('click',function(e){
    if(!MOB)return;
    var b=e.target.closest('.beat');
    if(b&&b===steps[cur].el&&!e.target.closest('button,a,[role="button"],.recap,.trio'))go(cur+1);
  });
  function setMode(m){
    if(m===MOB)return;
    $$('.scene',mainEl).forEach(function(sc){if(sc._chat)sc._chat.reset();});
    MOB=m;document.body.classList.toggle('mob',m);
    if(!m)$$('.cmp').forEach(function(c){c.hidden=false;});
    $$('.mcur',mainEl).forEach(function(x){x.classList.remove('mcur');});
    var s=steps[cur];
    if(m){scrollTo(0,0);layoutVars();mobShow(s,false);}
    else{
      if(s.el.classList.contains('step')){var c=chatOf(s);if(c)c.apply(s.el,false);else applyBoard(s.el);}
      scrollTo({top:s.el.getBoundingClientRect().top+pageYOffset,behavior:'auto'});
    }
    render();refreshNext();
  }
  var mq=window.matchMedia?matchMedia('(max-width:860px)'):null;
  function checkMode(){setMode(!!(mq&&mq.matches));}
  if(mq){if(mq.addEventListener)mq.addEventListener('change',checkMode);else if(mq.addListener)mq.addListener(checkMode);}
  addEventListener('resize',layoutVars);
  if(window.ResizeObserver)new ResizeObserver(layoutVars).observe(mact);

  /* ---------- 续读：恢复互动结果、已看步骤，回到上次位置 ---------- */
  var resumeTo=0;
  if(saved){
    var byGate={};
    $$('.step[data-unlock]',mainEl).forEach(function(s){byGate[s.getAttribute('data-unlock')]=s;});
    Object.keys(saved.choice||{}).forEach(function(g){var s=byGate[g];if(s&&s._restore)s._restore(saved.choice[g]);});
    Object.keys(saved.pick||{}).forEach(function(g){var s=byGate[g];if(s&&s._restore)s._restore(saved.pick[g]);});
    (saved.visited||[]).forEach(function(i){if(steps[i])visited[i]=1;});
    var to=+saved.cur||0;
    if(steps[to]&&!isLocked(steps[to])&&to<finStep.i)resumeTo=to;
  }

  /* ---------- 初始化 ---------- */
  refreshNext();render();checkMode();
  if(resumeTo>0){
    setTimeout(function(){
      scrollToStep(steps[resumeTo],true);
      toast('继续学习','已回到上次读到的位置');
    },60);
  }
  }
})();

(function(){
  const $ = (s, r=document) => r.querySelector(s);

  // 取“内容宽”（不含滚动条）：offsetWidth - (offsetWidth - clientWidth)
  function innerWidth(el){
    if(!el) return 0;
    return el.clientWidth; // 已排除滚动条，更适合我们
  }

function measure(){
  const root = document.documentElement;
  const $ = (s)=>document.querySelector(s);

  // 顶部两条
  const h1 = document.getElementById('header')?.offsetHeight || 56;
  const h2 = document.querySelector('.breadcrumbs')?.offsetHeight || 40;
  root.style.setProperty('--top-h1', h1 + 'px');
  root.style.setProperty('--top-h2', h2 + 'px');

  // 左右真实“内容宽”（clientWidth：排除滚动条）
  const left  = document.getElementById('nav-sidebar');
  const right = document.getElementById('content-related') || document.getElementById('changelist-filter');
  root.style.setProperty('--left-w',  (left  ? left.clientWidth  : 260) + 'px');
  root.style.setProperty('--right-w', (right ? right.clientWidth : 300) + 'px');

  // 三条工具带高度 = 元素高度 + 上下 margin（避免高度低估导致表头和工具条之间有缝/重叠）
  const blockH = (el)=>{
    if(!el) return 0;
    const cs = getComputedStyle(el);
    return el.getBoundingClientRect().height
         + parseFloat(cs.marginTop || 0)
         + parseFloat(cs.marginBottom || 0);
  };
  const tb = document.querySelector('#changelist #toolbar');
  const tl = document.querySelector('#changelist nav.toplinks');
  const ac = document.querySelector('#changelist .actions');
  root.style.setProperty('--bar-1', blockH(tb) + 'px');
  root.style.setProperty('--bar-2', blockH(tl) + 'px');
  root.style.setProperty('--bar-3', blockH(ac) + 'px');
}


  // 在固定工具带上滚轮 → 代理滚动到内容区（只滚数据行）
  function proxyWheel(el){
    const list = $('#content-main'); if(!el || !list) return;
    const wheel = e => { list.scrollTop += e.deltaY; e.preventDefault(); };
    el.addEventListener('wheel', wheel, {passive:false});
    // 触屏简单代理
    let lastY=null;
    el.addEventListener('touchstart', e=>{ lastY = e.touches[0].clientY; }, {passive:true});
    el.addEventListener('touchmove', e=>{
      if(lastY==null) return;
      const dy = lastY - e.touches[0].clientY;
      list.scrollTop += dy;
      lastY = e.touches[0].clientY;
      e.preventDefault();
    }, {passive:false});
  }

  document.addEventListener('DOMContentLoaded', function(){
    measure();
    proxyWheel($('#changelist #toolbar'));
    proxyWheel($('#changelist nav.toplinks'));
    proxyWheel($('#changelist .actions'));
  });
  window.addEventListener('resize', measure);

  // 结构变化（收起左栏/展开过滤器/切换日期等）时，自动重测
  const host = $('#changelist') || document.body;
  new MutationObserver(measure).observe(host, {subtree:true, childList:true, attributes:true});
})();
// 侧栏展开/收起后，重新测量左宽，避免工具条/克隆表头压住菜单
document.addEventListener('DOMContentLoaded', function () {
  const btn = document.getElementById('toggle-nav-sidebar');
  if (btn) {
    btn.addEventListener('click', () => {
      // 等动画/样式切换结束再测
      setTimeout(() => {
        // 你已有的测量总入口（前面提供的函数名）
        if (typeof measureAll === 'function') measureAll();
        else if (typeof measure === 'function') measure();
      }, 250);
    });
  }
});

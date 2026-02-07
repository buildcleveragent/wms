// admin/custom/collapse-apps.js
(function () {
  var SIDEBAR_SEL = "#nav-sidebar";
  var MOD_SEL     = ".module";
  var CAPTION_SEL = "caption";
  var KEY_PREFIX  = "admin.sidebar.collapse.";

  function keyFor(mod){
    // .app-xxx 在 Django admin 侧栏每个分组上都会有
    var cls = Array.from(mod.classList).find(c => c.startsWith("app-")) || "app-unknown";
    return KEY_PREFIX + cls;
  }

  function setCollapsed(mod, flag){
    mod.classList.toggle("collapsed", !!flag);
    try { localStorage.setItem(keyFor(mod), flag ? "1" : "0"); } catch(e){}
  }

  function getSaved(mod){
    try { return localStorage.getItem(keyFor(mod)) === "1"; } catch(e){ return false; }
  }

  function applyDefault(mod){
    // 默认全部折叠；仅当记忆为 "0" 时展开
    var saved = null;
    try { saved = localStorage.getItem(keyFor(mod)); } catch(e){}
    setCollapsed(mod, saved === "0" ? false : true);
  }

  function init(){
    var sidebar = document.querySelector(SIDEBAR_SEL);
    if (!sidebar) return;

    var modules = sidebar.querySelectorAll(MOD_SEL);
    // 先应用默认（全折叠）
    modules.forEach(applyDefault);
    // 再次强制一遍，避免其他脚本晚于我们修改
    setTimeout(function(){ modules.forEach(applyDefault); }, 0);

    // 事件委托：点击 caption（或其中的任何子元素）都切换
    sidebar.addEventListener("click", function(e){
      var cap = e.target.closest(CAPTION_SEL);
      if (!cap || !sidebar.contains(cap)) return;

      var mod = cap.closest(MOD_SEL);
      if (!mod) return;

      e.preventDefault();
      var willCollapse = !mod.classList.contains("collapsed");
      setCollapsed(mod, willCollapse);
    });

    // 侧栏搜索：有匹配就自动展开
    var search = sidebar.querySelector("input[type='search']");
    if (search) {
      search.addEventListener("input", function () {
        var q = (search.value || "").toLowerCase();
        if (!q) return;
        modules.forEach(function (mod) {
          if (mod.innerText.toLowerCase().indexOf(q) >= 0) setCollapsed(mod, false);
        });
      });
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();

// static/tasking/count_blind.js
(function () {
  console.log("[blind] v7 loaded");

  function pickOrderEl() {
    // 兼容 0/1/2 等索引：取第一个 countorder
    return document.querySelector('[id^="id_countlineextra-"][id$="-countorder"]');
  }

  function rowOf(inputEl) {
    if (!inputEl) return null;
    return inputEl.closest(".form-row") || inputEl.parentElement;
  }

  function hideNow() {
    const orderEl = pickOrderEl();
    if (!orderEl) return;

    // 当前是否是盲盘（SECOND/THIRD）
    const v = (orderEl.value || "").toUpperCase();
    const blind = v === "SECOND" || v === "THIRD";

    // 通过同一行的“前缀”精确定位 qty_book / qty_diff
    const prefix = orderEl.id.replace(/countorder$/, ""); // 例如 id_countlineextra-0-

    const bookInput = document.getElementById(prefix + "qty_book");
    const diffInput = document.getElementById(prefix + "qty_diff");
    const planInput = document.getElementById("id_qty_plan");

    const bookRow = rowOf(bookInput) || document.querySelector('.form-row.field-qty_book');
    const diffRow = rowOf(diffInput) || document.querySelector('.form-row.field-qty_diff');
    const planRow = rowOf(planInput) || document.querySelector('.form-row.field-qty_plan');

    if (bookRow) bookRow.style.display = blind ? "none" : "";
    if (diffRow) diffRow.style.display = blind ? "none" : "";
    if (planRow) planRow.style.display = blind ? "none" : "";
  }

  function init() {
    hideNow();

    // 切换 SECOND/THIRD/FIRST 时实时生效
    const orderEl = pickOrderEl();
    if (orderEl) orderEl.addEventListener("change", hideNow);

    // Admin 有时会重绘 DOM：监听并再次隐藏
    const target = document.getElementById("content") || document.body;
    const obs = new MutationObserver(hideNow);
    obs.observe(target, { childList: true, subtree: true });

    // 再做一小段轮询，压制时序问题
    let n = 0;
    const timer = setInterval(() => {
      hideNow();
      if (++n > 20) clearInterval(timer); // ~3 秒
    }, 150);
  }

  document.addEventListener("DOMContentLoaded", init);
})();

// static/tasking/count_blind.js
(function () {
  console.log("[blind] script loaded");
  function apply(box) {
    const orderEl = box.querySelector('[name$="-countorder"]');
    const bookRow = box.querySelector('.form-row.field-qty_book'); // StackedInline
    const bookInp = box.querySelector('[name$="-qty_book"]');
    if (!orderEl || !bookRow) return;

    const sync = () => {
      const v = (orderEl.value || "").toUpperCase();
      const blind = v === "SECOND" || v === "THIRD";
      bookRow.style.display = blind ? "none" : "";
      if (bookInp) bookInp.readOnly = blind; // 防手动改DOM
    };

    sync();
    orderEl.addEventListener("change", sync);
  }

  function init() {
    // 你页面里容器 id 确实是这个（你已在控制台验证）
    const group =
      document.getElementById("countlineextra-group") ||
      document.getElementById("countlineextra_set-group");
    if (!group) return console.warn("[blind] group not found");
    group.querySelectorAll(".inline-related").forEach(apply);
  }

  document.addEventListener("DOMContentLoaded", init);

  // 兼容“添加另一条”新增的 inline（Django admin 的 jQuery 事件）
  document.addEventListener("formset:added", function (e) {
    if (e && e.target && e.target.matches(".inline-related")) apply(e.target);
  });
})();

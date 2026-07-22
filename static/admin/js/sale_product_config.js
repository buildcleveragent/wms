(function () {
  function setupQuantityRuleToggle() {
    const toggle = document.getElementById("id_enable_qty_rules");
    if (!toggle) return;

    const rows = ["min_order_qty", "multiple_qty"]
      .map((name) => document.querySelector(`.form-row.field-${name}`))
      .filter(Boolean);

    const syncVisibility = () => {
      rows.forEach((row) => {
        row.hidden = !toggle.checked;
      });
    };

    toggle.addEventListener("change", syncVisibility);
    syncVisibility();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", setupQuantityRuleToggle);
  } else {
    setupQuantityRuleToggle();
  }
})();

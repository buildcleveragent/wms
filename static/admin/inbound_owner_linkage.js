(function () {
  function ready(fn) {
    if (document.readyState !== 'loading') fn();
    else document.addEventListener('DOMContentLoaded', fn);
  }

  // ——— helpers ———
  function clearSelect(el) {
    el.innerHTML = '';
    var empty = document.createElement('option');
    empty.value = '';
    empty.textContent = '---------';
    el.appendChild(empty);
  }

  function fillSelect(el, list, keepValue) {
    list.forEach(function (o) {
      var opt = document.createElement('option');
      opt.value = o.id;
      opt.textContent = o.text;
      el.appendChild(opt);
    });
    if (keepValue != null && keepValue !== '') {
      el.value = String(keepValue);
      if (el.value !== String(keepValue)) el.value = '';
    }
  }

  function fetchOptions(url, cb) {
    fetch(url, { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (data) { cb((data && data.results) || []); })
      .catch(function (e) { console.error(e); cb([]); });
  }

  // ——— supplier 下拉（单一） ———
  function refreshSupplier(ownerId) {
    var supplier = document.getElementById('id_supplier');
    if (!supplier) return;
    var src = supplier.getAttribute('data-source-url');
    if (!src) return;

    var keep = supplier.value;
    clearSelect(supplier);
    if (!ownerId) return;

    fetchOptions(src + '?owner=' + encodeURIComponent(ownerId), function (list) {
      fillSelect(supplier, list, keep);
    });
  }

  // ——— product 下拉（多行 Inline） ———
  function refreshAllProducts(ownerId) {
    var selects = document.querySelectorAll('select.vProductByOwner');
    selects.forEach(function (sel) { refreshOneProductSelect(sel, ownerId); });
  }

  function refreshOneProductSelect(sel, ownerId) {
    var src = sel.getAttribute('data-source-url');
    if (!src) return;

    var keep = sel.value;
    clearSelect(sel);
    if (!ownerId) return;

    fetchOptions(src + '?owner=' + encodeURIComponent(ownerId), function (list) {
      fillSelect(sel, list, keep);
    });
  }

  function watchNewInlines() {
    // Django admin 触发的事件
    document.addEventListener('formset:added', function (e) {
      var owner = document.getElementById('id_owner');
      if (!owner) return;
      var container = e.target || (e.detail && e.detail.formset);
      if (!container) return;

      var sel = container.querySelector && container.querySelector('select.vProductByOwner');
      if (sel) refreshOneProductSelect(sel, owner.value);
    });

    // 兜底：MutationObserver（个别主题可能不触发上面的事件）
    var mo = new MutationObserver(function (mutations) {
      var owner = document.getElementById('id_owner');
      if (!owner) return;
      mutations.forEach(function (m) {
        m.addedNodes.forEach(function (node) {
          if (!(node instanceof Element)) return;
          var sel = node.matches && node.matches('select.vProductByOwner')
            ? node
            : (node.querySelector && node.querySelector('select.vProductByOwner'));
          if (sel) refreshOneProductSelect(sel, owner.value);
        });
      });
    });
    mo.observe(document.body, { childList: true, subtree: true });
  }

  // ——— entry ———
  ready(function () {
    var owner = document.getElementById('id_owner');
    if (!owner) return;

    // 初次进入，根据 owner 初始化
    refreshSupplier(owner.value);
    refreshAllProducts(owner.value);

    // owner 改变 → 两类下拉都联动刷新
    owner.addEventListener('change', function () {
      refreshSupplier(owner.value);
      refreshAllProducts(owner.value);
    });

    // 监听新增行
    watchNewInlines();
  });
})();

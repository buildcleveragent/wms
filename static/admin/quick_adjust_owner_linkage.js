(function () {
  function ready(fn) { if (document.readyState !== 'loading') fn(); else document.addEventListener('DOMContentLoaded', fn); }
  function clearSelect(el) {
    el.innerHTML = '';
    var empty = document.createElement('option');
    empty.value = '';
    empty.textContent = '---------';
    el.appendChild(empty);
  }
  function fetchOptions(url, cb) {
    fetch(url, { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (data) { cb((data && data.results) || []); })
      .catch(function (e) { console.error(e); cb([]); });
  }
  function refreshProducts(ownerId) {
    var sel = document.getElementById('id_product');
    if (!sel) return;
    var base = sel.getAttribute('data-source-url');
    if (!base) return;
    var keep = sel.value;
    clearSelect(sel);
    if (!ownerId) return;
    fetchOptions(base + '?owner=' + encodeURIComponent(ownerId), function (list) {
      list.forEach(function (o) {
        var opt = document.createElement('option');
        opt.value = o.id;
        opt.textContent = o.text;
        sel.appendChild(opt);
      });
      if (keep) {
        sel.value = String(keep);
        if (sel.value !== String(keep)) sel.value = '';
      }
    });
  }

  ready(function () {
    var owner = document.getElementById('id_owner');
    if (!owner) return;
    refreshProducts(owner.value);
    owner.addEventListener('change', function () { refreshProducts(owner.value); });
  });
})();

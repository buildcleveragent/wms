(function () {
  function ready(callback) {
    if (document.readyState !== 'loading') callback();
    else document.addEventListener('DOMContentLoaded', callback);
  }

  ready(function () {
    var owner = document.getElementById('id_owner');
    var salesperson = document.getElementById('id_salesperson');
    if (!owner || !salesperson) return;
    var source = salesperson.getAttribute('data-source-url');
    if (!source) return;

    var revision = 0;
    var $ = window.django && window.django.jQuery;

    function notifyChange() {
      if ($) $(salesperson).trigger('change');
      else salesperson.dispatchEvent(new Event('change', { bubbles: true }));
    }

    function clearOptions(label) {
      salesperson.replaceChildren(new Option(label, ''));
      notifyChange();
    }

    async function refresh(keepValue) {
      var currentRevision = ++revision;
      var ownerId = owner.value;
      clearOptions(ownerId ? '正在加载业务员…' : '请先选择货主');
      salesperson.disabled = true;
      if (!ownerId) return;

      try {
        var url = new URL(source, window.location.href);
        url.searchParams.set('owner', ownerId);
        var response = await fetch(url, { credentials: 'same-origin' });
        if (!response.ok) throw new Error('Unable to load salespeople');
        var data = await response.json();
        if (currentRevision !== revision) return;

        clearOptions(data.results.length ? '---------' : '该货主暂无可选业务员');
        data.results.forEach(function (item) {
          salesperson.add(new Option(item.text, item.id));
        });
        salesperson.value = keepValue || '';
        if (!salesperson.value) salesperson.value = '';
        notifyChange();
      } catch (error) {
        if (currentRevision !== revision) return;
        clearOptions('加载失败，请重新选择货主');
      } finally {
        if (currentRevision === revision) salesperson.disabled = false;
      }
    }

    if ($) $(owner).on('change', function () { refresh(''); });
    else owner.addEventListener('change', function () { refresh(''); });
    refresh(salesperson.value);
  });
})();

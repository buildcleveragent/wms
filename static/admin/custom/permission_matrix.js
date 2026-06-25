(function () {
  function visibleRows(matrix) {
    return Array.from(matrix.querySelectorAll("[data-permission-row]")).filter(function (row) {
      return !row.classList.contains("wms-permission-hidden");
    });
  }

  function setInputs(rows, checked) {
    rows.forEach(function (row) {
      row.querySelectorAll('input[name][type="checkbox"]').forEach(function (input) {
        input.checked = checked;
      });
    });
  }

  function updateToggle(toggle, inputs) {
    if (!toggle) {
      return;
    }
    var checkedCount = inputs.filter(function (input) { return input.checked; }).length;
    toggle.checked = inputs.length > 0 && checkedCount === inputs.length;
    toggle.indeterminate = checkedCount > 0 && checkedCount < inputs.length;
  }

  function updateCounts(matrix) {
    var allInputs = Array.from(matrix.querySelectorAll('input[name][type="checkbox"]'));
    var total = allInputs.length;
    var selected = allInputs.filter(function (input) { return input.checked; }).length;
    var selectedNode = matrix.querySelector("[data-permission-selected-count]");
    var totalNode = matrix.querySelector("[data-permission-total-count]");

    if (selectedNode) {
      selectedNode.textContent = selected;
    }
    if (totalNode) {
      totalNode.textContent = total;
    }

    matrix.querySelectorAll("[data-permission-row]").forEach(function (row) {
      updateToggle(
        row.querySelector("[data-permission-row-toggle]"),
        Array.from(row.querySelectorAll('input[name][type="checkbox"]'))
      );
    });

    matrix.querySelectorAll("[data-permission-app]").forEach(function (app) {
      var appInputs = Array.from(app.querySelectorAll('input[name][type="checkbox"]'));
      var appSelected = appInputs.filter(function (input) { return input.checked; }).length;
      var countNode = app.querySelector("[data-permission-app-count]");

      updateToggle(app.querySelector("[data-permission-app-toggle]"), appInputs);
      if (countNode) {
        countNode.textContent = appSelected + "/" + appInputs.length;
      }
    });
  }

  function applyFilter(matrix) {
    var input = matrix.querySelector("[data-permission-filter]");
    var query = input ? input.value.trim().toLowerCase() : "";

    matrix.querySelectorAll("[data-permission-app]").forEach(function (app) {
      var visibleCount = 0;
      app.querySelectorAll("[data-permission-row]").forEach(function (row) {
        var text = row.getAttribute("data-search-text") || "";
        var visible = !query || text.indexOf(query) !== -1;
        row.classList.toggle("wms-permission-hidden", !visible);
        if (visible) {
          visibleCount += 1;
        }
      });
      app.classList.toggle("wms-permission-hidden", visibleCount === 0);
    });
  }

  function bindMatrix(matrix) {
    var filter = matrix.querySelector("[data-permission-filter]");
    if (filter) {
      filter.addEventListener("input", function () {
        applyFilter(matrix);
      });
    }

    matrix.addEventListener("change", function (event) {
      var target = event.target;
      if (target.matches("[data-permission-row-toggle]")) {
        setInputs([target.closest("[data-permission-row]")], target.checked);
      } else if (target.matches("[data-permission-app-toggle]")) {
        setInputs(Array.from(target.closest("[data-permission-app]").querySelectorAll("[data-permission-row]")), target.checked);
      }
      updateCounts(matrix);
    });

    var checkVisible = matrix.querySelector("[data-permission-check-visible]");
    if (checkVisible) {
      checkVisible.addEventListener("click", function () {
        setInputs(visibleRows(matrix), true);
        updateCounts(matrix);
      });
    }

    var clearVisible = matrix.querySelector("[data-permission-clear-visible]");
    if (clearVisible) {
      clearVisible.addEventListener("click", function () {
        setInputs(visibleRows(matrix), false);
        updateCounts(matrix);
      });
    }

    applyFilter(matrix);
    updateCounts(matrix);
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-permission-matrix]").forEach(bindMatrix);
  });
})();

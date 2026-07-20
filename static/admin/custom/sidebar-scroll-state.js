// Keep the Django admin navigation at the same vertical position after a
// model link performs a full-page navigation.
(function () {
  "use strict";

  var SIDEBAR_SELECTOR = "#nav-sidebar";
  var STORAGE_KEY = "admin.sidebar.scrollTop";

  function readSavedPosition() {
    try {
      var rawValue = sessionStorage.getItem(STORAGE_KEY);
      if (rawValue === null) return null;
      var value = Number(rawValue);
      return Number.isFinite(value) && value >= 0 ? value : null;
    } catch (error) {
      return null;
    }
  }

  function savePosition(sidebar) {
    try {
      sessionStorage.setItem(STORAGE_KEY, String(sidebar.scrollTop));
    } catch (error) {
      // Storage may be unavailable in hardened/private browser modes.
    }
  }

  function restorePosition(sidebar, position) {
    var maxScrollTop = Math.max(0, sidebar.scrollHeight - sidebar.clientHeight);
    sidebar.scrollTop = Math.min(position, maxScrollTop);
  }

  function init() {
    var sidebar = document.querySelector(SIDEBAR_SELECTOR);
    if (!sidebar) return;

    var savedPosition = readSavedPosition();
    var restoring = savedPosition !== null;
    var saveFrame = null;

    if (restoring) {
      // collapse-apps.js restores group expansion on DOMContentLoaded and once
      // more in a zero-delay timer. Restore after both passes so the changed
      // sidebar height cannot force the browser back to the top.
      restorePosition(sidebar, savedPosition);
      window.requestAnimationFrame(function () {
        restorePosition(sidebar, savedPosition);
        window.setTimeout(function () {
          restorePosition(sidebar, savedPosition);
          restoring = false;
        }, 0);
      });
    }

    sidebar.addEventListener(
      "scroll",
      function () {
        if (restoring || saveFrame !== null) return;
        saveFrame = window.requestAnimationFrame(function () {
          saveFrame = null;
          savePosition(sidebar);
        });
      },
      { passive: true }
    );

    // Save synchronously before a sidebar link starts the next page load.
    sidebar.addEventListener(
      "click",
      function (event) {
        if (event.target.closest("a[href]")) savePosition(sidebar);
      },
      true
    );

    window.addEventListener("pagehide", function () {
      savePosition(sidebar);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();

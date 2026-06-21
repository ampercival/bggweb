// Shared site-wide helpers loaded on every page via base.html.
(function () {
  "use strict";

  // Convert any server-rendered UTC timestamps marked with `.localtime`
  // (carrying an ISO value in `data-ts`) into the visitor's local time.
  function localizeTimestamps() {
    document.querySelectorAll(".localtime").forEach(function (el) {
      var ts = el.getAttribute("data-ts");
      if (!ts) return;
      try {
        el.textContent = new Date(ts).toLocaleString();
      } catch (e) {
        /* leave the original server-rendered value in place */
      }
    });
  }

  // Populate the footer copyright year.
  function setFooterYear() {
    var el = document.getElementById("year");
    if (el) el.textContent = new Date().getFullYear();
  }

  function init() {
    localizeTimestamps();
    setFooterYear();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();

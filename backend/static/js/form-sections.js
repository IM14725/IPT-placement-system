/* Collapsible form sections (<details class="ipt-section">).
   A section that holds an error, or a field the browser refuses to submit, opens by itself so a problem
   is never hidden inside a collapsed section. */
(function () {
  "use strict";

  function sections() { return document.querySelectorAll("details.ipt-section"); }

  Array.prototype.forEach.call(sections(), function (d) {
    if (d.querySelector(".is-error, [aria-invalid='true']")) { d.open = true; }
  });

  // "invalid" does not bubble, so listen in the capture phase.
  document.addEventListener("invalid", function (e) {
    var d = e.target && e.target.closest ? e.target.closest("details.ipt-section") : null;
    if (d && !d.open) { d.open = true; }
  }, true);

  // A link to a field inside a closed section (e.g. the camera button in the account header) opens it.
  function openFor(hash) {
    if (!hash || hash.length < 2) { return; }
    var el = null;
    try { el = document.getElementById(decodeURIComponent(hash.slice(1))); } catch (e) { el = null; }
    var d = el && el.closest ? el.closest("details.ipt-section") : null;
    if (d && !d.open) {
      d.open = true;
      setTimeout(function () { el.scrollIntoView({ block: "center" }); }, 0);  // it was hidden, so tabs.js could not scroll to it
    }
  }
  window.addEventListener("hashchange", function () { openFor(location.hash); });
  openFor(location.hash);
})();

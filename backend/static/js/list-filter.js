/* Instant client-side search + status filter for a short list that is already on the page (organization
   "My Slots"). Markup: a [data-list-filter] container holding [data-list-q] (text) and [data-list-status]
   (select); each row is [data-filter-item] with data-filter-text (lower-case words to search) and
   data-filter-status; [data-list-empty] is the "nothing matches" message. */
(function () {
  "use strict";
  var root = document.querySelector("[data-list-filter]");
  if (!root) return;
  var items = Array.prototype.slice.call(document.querySelectorAll("[data-filter-item]"));
  var q = root.querySelector("[data-list-q]");
  var status = root.querySelector("[data-list-status]");
  var empty = document.querySelector("[data-list-empty]");

  function apply() {
    var term = q ? q.value.trim().toLowerCase() : "";
    var wanted = status ? status.value : "";
    var shown = 0;
    items.forEach(function (item) {
      var ok = (!term || (item.getAttribute("data-filter-text") || "").indexOf(term) !== -1) &&
        (!wanted || item.getAttribute("data-filter-status") === wanted);
      item.hidden = !ok;
      if (ok) shown += 1;
    });
    if (empty) empty.hidden = shown !== 0 || items.length === 0;
  }

  root.addEventListener("input", apply);
  root.addEventListener("change", apply);
  root.addEventListener("submit", function (e) { e.preventDefault(); apply(); });
  apply();
})();

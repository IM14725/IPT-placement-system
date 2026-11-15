/* Tabs on one page. Markup:
     <div data-tabs data-default="profile">
       <div role="tablist">
         <button type="button" data-tab="profile">...</button> <button type="button" data-tab="security">...</button>
       </div>
       <div data-panel="profile">...</div> <div data-panel="security" hidden>...</div>
     </div>
   The address hash picks the tab (#security), so a redirect can land straight on it, and a click
   keeps the hash in sync. A hash that names an element inside a panel (#security-phone) opens that
   panel and scrolls to the element. Left/Right/Home/End move between tabs. Without JavaScript every
   panel simply stays visible. */
(function () {
  "use strict";

  var ACTIVE = ["border-primary", "text-primary"];
  var INACTIVE = ["border-transparent", "text-on-surface-variant"];

  function each(list, fn) { Array.prototype.forEach.call(list, fn); }

  function setup(root) {
    var tabs = root.querySelectorAll("[data-tab]");
    var panels = root.querySelectorAll("[data-panel]");
    var names = Array.prototype.map.call(panels, function (p) { return p.getAttribute("data-panel"); });
    if (!tabs.length || !names.length) return;

    each(panels, function (panel) { panel.setAttribute("role", "tabpanel"); });

    function show(name) {
      if (names.indexOf(name) === -1) name = root.getAttribute("data-default") || names[0];
      each(panels, function (panel) {
        var hide = panel.getAttribute("data-panel") !== name;
        panel.hidden = hide;
        // The inline style is belt and braces for a page whose CSS lets a display class beat [hidden].
        panel.style.display = hide ? "none" : "";
      });
      each(tabs, function (tab) {
        var on = tab.getAttribute("data-tab") === name;
        tab.setAttribute("aria-selected", on ? "true" : "false");
        tab.setAttribute("tabindex", on ? "0" : "-1");
        ACTIVE.forEach(function (c) { tab.classList.toggle(c, on); });
        INACTIVE.forEach(function (c) { tab.classList.toggle(c, !on); });
      });
      return name;
    }

    // The panel that a hash points at: the panel itself (#security) or an element inside one (#security-phone).
    function panelFor(hash) {
      if (!hash) return "";
      if (names.indexOf(hash) !== -1) return hash;
      var target = null;
      try { target = document.getElementById(decodeURIComponent(hash)); } catch (e) { target = null; }
      var holder = target && target.closest ? target.closest("[data-panel]") : null;
      return holder && root.contains(holder) ? holder.getAttribute("data-panel") : "";
    }

    function fromHash(scroll) {
      var hash = location.hash.slice(1);
      var name = panelFor(hash);
      show(name);
      if (scroll && name && name !== hash) {
        var target = document.getElementById(decodeURIComponent(hash));
        if (target && target.scrollIntoView) target.scrollIntoView();
      }
    }

    each(tabs, function (tab, index) {
      tab.addEventListener("click", function () {
        var name = tab.getAttribute("data-tab");
        show(name);
        try { history.replaceState(null, "", "#" + name); } catch (e) {}
      });
      tab.addEventListener("keydown", function (e) {
        var move = { ArrowRight: 1, ArrowLeft: -1, Home: "first", End: "last" }[e.key];
        if (move === undefined) return;
        e.preventDefault();
        var next = move === "first" ? 0 : move === "last" ? tabs.length - 1 : (index + move + tabs.length) % tabs.length;
        tabs[next].focus();
        tabs[next].click();
      });
    });
    window.addEventListener("hashchange", function () { fromHash(true); });
    fromHash(true);
  }

  function start() {
    each(document.querySelectorAll("[data-tabs]"), setup);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();

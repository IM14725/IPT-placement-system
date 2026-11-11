/* Responsive behaviour: navigation rail/drawer, Light/Dark/System theme switch, card-table labels. */
(function () {
  "use strict";

  /* ---------- Navigation ---------- */
  var body = document.body;
  var btn = document.getElementById("sidebar-toggle");
  var side = document.getElementById("app-sidebar");
  var back = document.getElementById("sidebar-backdrop");
  var icon = document.getElementById("sidebar-toggle-icon");
  var wide = window.matchMedia("(min-width: 768px)");

  function isOpen() { return body.classList.contains("sb-open"); }

  function setOpen(open) {
    body.classList.toggle("sb-open", open);
    if (back) {
      back.classList.toggle("opacity-0", !open);
      back.classList.toggle("pointer-events-none", !open);
    }
    // Phones: a closed drawer is off-screen and must not be tabbable. The tablet/desktop rail is always usable.
    if (side) {
      if (!wide.matches && !open) { side.setAttribute("inert", ""); } else { side.removeAttribute("inert"); }
      side.querySelectorAll(".side-link").forEach(function (a) {
        var label = a.querySelector(".side-label");
        if (wide.matches && !open && label) { a.setAttribute("title", label.textContent.trim()); } else { a.removeAttribute("title"); }
      });
    }
    if (btn) {
      btn.setAttribute("aria-expanded", open ? "true" : "false");
      btn.setAttribute("aria-label", open ? btn.dataset.labelClose : btn.dataset.labelOpen);
    }
    if (icon) { icon.textContent = open ? (wide.matches ? "menu_open" : "close") : "menu"; }
  }

  if (btn && side) {
    setOpen(false);
    btn.addEventListener("click", function () { setOpen(!isOpen()); });
    if (back) { back.addEventListener("click", function () { setOpen(false); }); }
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && isOpen()) { setOpen(false); btn.focus(); }
    });
    var onBreakpoint = function () { setOpen(false); };
    if (wide.addEventListener) { wide.addEventListener("change", onBreakpoint); } else { wide.addListener(onBreakpoint); }
  }

  /* ---------- Theme: system (default) / light / dark ---------- */
  var MODES = ["system", "light", "dark"];
  var ICONS = { system: "contrast", light: "light_mode", dark: "dark_mode" };
  var NAMES = { system: "System", light: "Light", dark: "Dark" };
  var root = document.documentElement;
  var dq = window.matchMedia("(prefers-color-scheme: dark)");
  var themeBtn = document.getElementById("theme-toggle");
  var themeIcon = document.getElementById("theme-toggle-icon");

  function savedMode() {
    var m = "system";
    try { m = localStorage.getItem("ipt-theme") || "system"; } catch (e) { /* storage blocked */ }
    return MODES.indexOf(m) === -1 ? "system" : m;
  }

  function applyTheme(mode) {
    var dark = mode === "dark" || (mode === "system" && dq.matches);
    root.classList.toggle("dark", dark);
    root.setAttribute("data-theme", mode);
    if (themeIcon) { themeIcon.textContent = ICONS[mode]; }
    if (themeBtn) {
      var text = "Theme: " + NAMES[mode];
      themeBtn.setAttribute("title", text);
      themeBtn.setAttribute("aria-label", text + " (click to change)");
    }
  }

  applyTheme(savedMode());
  if (themeBtn) {
    themeBtn.addEventListener("click", function () {
      var next = MODES[(MODES.indexOf(savedMode()) + 1) % MODES.length];
      try { localStorage.setItem("ipt-theme", next); } catch (e) { /* storage blocked */ }
      applyTheme(next);
    });
  }
  var onSystem = function () { if (savedMode() === "system") { applyTheme("system"); } };
  if (dq.addEventListener) { dq.addEventListener("change", onSystem); } else { dq.addListener(onSystem); }
  window.addEventListener("storage", function (e) { if (e.key === "ipt-theme") { applyTheme(savedMode()); } });

  // Always print on white paper, whatever the screen theme is.
  var wasDark = false;
  window.addEventListener("beforeprint", function () { wasDark = root.classList.contains("dark"); root.classList.remove("dark"); });
  window.addEventListener("afterprint", function () { if (wasDark) { root.classList.add("dark"); } });

  /* ---------- Tables: copy header text onto each cell so phones can render rows as cards ---------- */
  function labelTable(table) {
    if (table.hasAttribute("data-no-cards")) { return; }
    var headRow = table.querySelector("thead tr:last-child");
    if (!headRow) { return; }
    var heads = [];
    Array.prototype.forEach.call(headRow.children, function (th) {
      var span = parseInt(th.getAttribute("colspan") || "1", 10) || 1;
      var text = (th.textContent || "").replace(/\s+/g, " ").trim();
      for (var i = 0; i < span; i++) { heads.push(text); }
    });
    table.classList.add("rt");
    table.querySelectorAll("tbody tr").forEach(function (tr) {
      var col = 0;
      Array.prototype.forEach.call(tr.children, function (cell) {
        if (cell.tagName !== "TD" && cell.tagName !== "TH") { return; }
        if (!cell.hasAttribute("data-label")) { cell.setAttribute("data-label", heads[col] || ""); }
        col += parseInt(cell.getAttribute("colspan") || "1", 10) || 1;
      });
    });
  }

  function labelAll(scope) {
    (scope || document).querySelectorAll("table").forEach(labelTable);
  }

  labelAll();
  if (window.MutationObserver) {
    var pending = false;
    new MutationObserver(function (muts) {
      var relevant = muts.some(function (m) {
        return Array.prototype.some.call(m.addedNodes, function (n) {
          return n.nodeType === 1 && (n.tagName === "TABLE" || n.tagName === "TR" || (n.querySelector && n.querySelector("table, tr")));
        });
      });
      if (!relevant || pending) { return; }
      pending = true;
      setTimeout(function () { pending = false; labelAll(); }, 50);
    }).observe(document.body, { childList: true, subtree: true });
  }
})();

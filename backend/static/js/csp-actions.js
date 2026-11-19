/*
 * Page actions that used to be inline on*= attributes.
 *
 * The Content-Security-Policy no longer allows inline event handlers (or javascript: links), so
 * templates declare intent with data attributes and this one script, delegated from `document`,
 * carries it out. Delegation also covers markup inserted later (rows, swapped forms).
 *
 *   data-ipt-confirm="Message"   on a <form> or a submit <button>: ask before submitting
 *   data-no-submit               on a <form>: never submit natively (a script handles it)
 *   data-autosubmit              on a <select>/<input>: submit its form when the value changes
 *   data-toggle-password="id"    on a button: show/hide the password input with that id
 *   data-print                   on a button: open the print dialog
 *   data-history-back            on a link: go back one page
 *   data-stop-click              on an element: its clicks do not reach ancestor click handlers
 *   data-href="/url"             on a <tr>: clicking the row (not a link/button in it) opens the URL
 */
(function () {
  "use strict";
  var d = document;

  function asked(el) {
    var message = el.getAttribute("data-ipt-confirm");
    return !message || window.confirm(message);
  }

  function cancel(e) {
    e.preventDefault();
    // Stops later handlers too (e.g. the loading overlay in form-loading.js), so a cancelled
    // confirm leaves the page exactly as it was.
    e.stopImmediatePropagation();
  }

  // Capture phase: runs before any other submit/click handler on the page.
  d.addEventListener(
    "submit",
    function (e) {
      var form = e.target;
      if (!form || !form.hasAttribute) return;
      if (form.hasAttribute("data-no-submit")) return cancel(e);
      if (form.hasAttribute("data-ipt-confirm") && !asked(form)) cancel(e);
    },
    true
  );

  d.addEventListener(
    "click",
    function (e) {
      var t = e.target;
      if (!t || !t.closest) return;
      var guarded = t.closest("button[data-ipt-confirm],input[data-ipt-confirm],a[data-ipt-confirm]");
      if (guarded && !asked(guarded)) return cancel(e);
      if (t.closest("[data-stop-click]")) e.stopPropagation();
    },
    true
  );

  d.addEventListener("change", function (e) {
    var t = e.target;
    if (t && t.matches && t.matches("[data-autosubmit]") && t.form) t.form.submit();
  });

  d.addEventListener("click", function (e) {
    var t = e.target;
    if (!t || !t.closest) return;

    var toggle = t.closest("[data-toggle-password]");
    if (toggle) {
      var input = d.getElementById(toggle.getAttribute("data-toggle-password"));
      if (!input) return;
      var icon = toggle.querySelector("[data-icon]");
      var show = input.type === "password";
      input.type = show ? "text" : "password";
      if (icon) icon.textContent = show ? "visibility_off" : "visibility";
      return;
    }
    if (t.closest("[data-print]")) {
      window.print();
      return;
    }
    if (t.closest("[data-history-back]")) {
      e.preventDefault();
      window.history.back();
      return;
    }
    var row = t.closest("tr[data-href]");
    if (row && !t.closest("a,button,input,select,textarea,label")) {
      window.location.href = row.getAttribute("data-href");
    }
  });
})();

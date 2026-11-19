/* On-screen keyboard behaviour for every form control on the platform.
 *
 * Gives each field the right mobile keyboard (numeric pad, email keyboard, search key), switches off
 * auto-capitalise / auto-correct where it corrupts data (emails, passwords, codes, search), labels the
 * Enter key (Next / Go / Search) and makes "Next" really move to the next field on touch devices.
 * It only fills in what a template or form widget has not set itself, and it also covers fields that
 * are added to the page later. No inline handlers, so it works under the strict CSP.
 */
(function () {
  "use strict";

  var SKIP_TYPES = /^(hidden|checkbox|radio|file|button|submit|reset|image|range|color)$/;
  var TEXTUAL = /^(text|search|email|tel|url|number|password|)$/;
  var WORD_NAMES = /(^|_|-)(name|first_name|last_name|title|department|course|organization|organisation|industry|university|position|street|company|city|region|district|ward)($|_|-)/;
  var coarse = window.matchMedia && window.matchMedia("(pointer: coarse)").matches;

  function has(el, attr) { return el.hasAttribute(attr); }
  function put(el, attr, value) { if (!has(el, attr)) el.setAttribute(attr, value); }
  function key(el) { return ((el.getAttribute("name") || "") + " " + (el.id || "")).toLowerCase(); }

  function enhanceField(el) {
    var tag = el.tagName;
    var type = (el.getAttribute("type") || "text").toLowerCase();
    if (tag === "INPUT" && (SKIP_TYPES.test(type) || el.readOnly || el.disabled)) return;
    if (tag === "INPUT" && !TEXTUAL.test(type)) return;   // date, time, … use the browser's own picker
    var id = key(el);
    var max = parseInt(el.getAttribute("maxlength") || "0", 10);

    if (type === "email") {
      put(el, "inputmode", "email");
      put(el, "autocapitalize", "none"); put(el, "autocorrect", "off"); put(el, "spellcheck", "false");
    } else if (type === "password") {
      put(el, "autocapitalize", "none"); put(el, "autocorrect", "off"); put(el, "spellcheck", "false");
    } else if (type === "tel" || /(^|[\s_-])(phone|tel|mobile|msisdn)/.test(id)) {
      put(el, "inputmode", "tel");
      put(el, "autocapitalize", "none"); put(el, "autocorrect", "off"); put(el, "spellcheck", "false");
    } else if (type === "number") {
      var step = el.getAttribute("step");
      var min = el.getAttribute("min");
      var decimal = step && step !== "any" ? /\./.test(step) : step === "any";
      // iOS's numeric pad has no minus sign, so leave fields that may be negative alone.
      if (!(min !== null && parseFloat(min) < 0) && min !== null) put(el, "inputmode", decimal ? "decimal" : "numeric");
      else if (min === null && !decimal) put(el, "inputmode", "numeric");
    } else if (type === "search" || /(^|[\s_-])q($|[\s_-])|search/.test(id)) {
      put(el, "inputmode", "search"); put(el, "enterkeyhint", "search");
      put(el, "autocapitalize", "none"); put(el, "autocorrect", "off"); put(el, "spellcheck", "false");
    } else if (/(^|[\s_-])(otp|code|token|pin)($|[\s_-])/.test(id)) {
      // A six-character code is the one-time passcode; longer / dashed codes (recovery codes) stay alphanumeric.
      if (max === 6) {
        put(el, "inputmode", "numeric"); put(el, "autocomplete", "one-time-code");
      }
      put(el, "autocapitalize", "none"); put(el, "autocorrect", "off"); put(el, "spellcheck", "false");
    } else if (tag === "INPUT" && WORD_NAMES.test((el.getAttribute("name") || el.id || "").toLowerCase())) {
      put(el, "autocapitalize", "words");
    }
  }

  function controls(form) {
    var out = [];
    var list = form.querySelectorAll("input, textarea, select");
    for (var i = 0; i < list.length; i++) {
      var el = list[i];
      var type = (el.getAttribute("type") || "text").toLowerCase();
      if (el.disabled || (el.tagName === "INPUT" && SKIP_TYPES.test(type))) continue;
      if (el.closest("[hidden], [aria-hidden='true']") || el.getAttribute("tabindex") === "-1") continue;   // also skips spam honeypots
      if (el.offsetParent === null && el.getClientRects().length === 0) continue;
      out.push(el);
    }
    return out;
  }

  function labelEnterKey(form) {
    var method = (form.getAttribute("method") || "get").toLowerCase();
    var list = controls(form);
    for (var i = 0; i < list.length; i++) {
      var el = list[i];
      if (el.tagName !== "INPUT" || (has(el, "enterkeyhint") && !has(el, "data-kb-eh"))) continue;
      var last = i === list.length - 1;
      // Filter / search forms (GET) submit straight from Enter; only multi-field POST forms advance.
      var hint = method === "get" ? (el.type === "search" || el.getAttribute("inputmode") === "search" ? "search" : "go") : last ? "go" : "next";
      el.setAttribute("enterkeyhint", hint);
      el.setAttribute("data-kb-eh", "");   // ours, so it can be recomputed when fields show or hide
    }
  }

  function enhance(root) {
    var scope = root && root.querySelectorAll ? root : document;
    var fields = scope.querySelectorAll("input, textarea");
    for (var i = 0; i < fields.length; i++) enhanceField(fields[i]);
    if (scope.matches && scope.matches("input, textarea")) enhanceField(scope);
    var forms = scope.querySelectorAll ? scope.querySelectorAll("form") : [];
    for (var j = 0; j < forms.length; j++) labelEnterKey(forms[j]);
    var owner = scope.closest ? scope.closest("form") : null;
    if (owner) labelEnterKey(owner);
  }

  // "Next" on a touch keyboard: Enter moves to the next field instead of submitting early.
  document.addEventListener("keydown", function (e) {
    if (!coarse || e.key !== "Enter" || e.defaultPrevented || e.isComposing) return;
    var el = e.target;
    if (!el || el.tagName !== "INPUT" || el.getAttribute("enterkeyhint") !== "next") return;
    var form = el.form;
    if (!form) return;
    var list = controls(form);
    var at = list.indexOf(el);
    if (at < 0 || at === list.length - 1) return;
    e.preventDefault();
    list[at + 1].focus();
  });

  // Fields appear and disappear (role pickers, wizards), so refresh the Next/Go labels when one is focused.
  document.addEventListener("focusin", function (e) {
    var form = e.target && e.target.form;
    if (form) labelEnterKey(form);
  });

  // Keep the focused field visible above the on-screen keyboard.
  function reveal() {
    var el = document.activeElement;
    if (!el || !/^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName)) return;
    var type = (el.getAttribute("type") || "").toLowerCase();
    if (SKIP_TYPES.test(type)) return;
    var vv = window.visualViewport;
    var rect = el.getBoundingClientRect();
    var bottom = vv ? vv.height : window.innerHeight;
    if (rect.top < 8 || rect.bottom > bottom - 12) {
      el.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  }
  if (coarse) {
    document.addEventListener("focusin", function () { setTimeout(reveal, 320); });
    if (window.visualViewport) window.visualViewport.addEventListener("resize", function () { setTimeout(reveal, 60); });
  }

  function start() {
    enhance(document);
    if (!window.MutationObserver) return;
    new MutationObserver(function (records) {
      for (var i = 0; i < records.length; i++) {
        var added = records[i].addedNodes;
        for (var j = 0; j < added.length; j++) if (added[j].nodeType === 1) enhance(added[j]);
      }
    }).observe(document.body, { childList: true, subtree: true });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();

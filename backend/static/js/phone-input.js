/* Tanzanian phone input: a fixed +255 prefix, accepts 07XX / 06XX / 7XX / 2557XX / +2557XX while typing,
   and submits the canonical 255XXXXXXXXX the server already validates and stores.

   The visible input keeps its id (labels, errors and focus keep working) but loses its name; a hidden
   input with the original name carries the canonical value. Applies to every input whose name ends in
   "phone" (register, organization/student profile, account phone change). */
(function () {
  "use strict";

  window.IPT = window.IPT || {};
  var SELECTOR = 'input[name$="phone"]:not([data-phone-ready]):not([data-no-phone-fix])';

  function national(raw) {
    var d = String(raw || "").replace(/\D/g, "");
    if (d.indexOf("255") === 0) { d = d.slice(3); } else if (d.charAt(0) === "0") { d = d.slice(1); }
    return d.slice(0, 9);
  }

  function pretty(n) {
    return n.replace(/(\d{3})(?=\d)/g, "$1 ").trim();
  }

  // Mobile numbers only for personal phones; organization lines may also be landlines.
  function validate(input) {
    var n = national(input.value);
    if (!n) { return ""; }
    var mobileOnly = input.dataset.phoneName !== "organization_phone";
    var t = (window.IPT.i18n || {});
    if (n.length !== 9) { return t.phoneLength || "Enter 9 digits after +255, e.g. 0712 345 678."; }
    if (mobileOnly && ["6", "7"].indexOf(n.charAt(0)) === -1) { return t.phoneMobile || "Mobile numbers start with 06 or 07."; }
    return "";
  }

  function enhance(input) {
    var name = input.getAttribute("name");
    input.setAttribute("data-phone-ready", "1");
    input.dataset.phoneName = name;

    var hidden = document.createElement("input");
    hidden.type = "hidden";
    hidden.name = name;
    input.removeAttribute("name");
    input.insertAdjacentElement("afterend", hidden);

    var chip = document.createElement("span");
    chip.className = "ipt-phone-prefix";
    chip.textContent = "+255";
    chip.setAttribute("aria-hidden", "true");
    if (input.parentElement && input.parentElement.classList.contains("ipt-field-box")) {
      input.insertAdjacentElement("beforebegin", chip);
    } else {
      var wrap = document.createElement("div");
      wrap.className = "ipt-phone-wrap";
      input.parentNode.insertBefore(wrap, input);
      wrap.appendChild(chip);
      wrap.appendChild(input);
    }

    input.setAttribute("type", "tel");
    input.setAttribute("inputmode", "numeric");
    input.setAttribute("autocomplete", "tel-national");
    input.setAttribute("maxlength", "13");
    input.setAttribute("placeholder", "712 345 678");

    function sync() {
      var n = national(input.value);
      input.value = pretty(n);
      hidden.value = n ? "255" + n : "";
    }
    input.addEventListener("input", sync);
    input.addEventListener("blur", sync);
    sync();
  }

  function init(scope) {
    (scope || document).querySelectorAll(SELECTOR).forEach(enhance);
  }

  window.IPT.phone = { init: init, national: national, validate: validate };
  init();
  document.addEventListener("ipt:form-swapped", function (e) { init(e.detail && e.detail.form); });
})();

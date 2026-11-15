/* Compact search + filters.

   A page marks one container with [data-filterbar]. Inside it:
     [data-filter-toggle]  button that opens/closes the filter panel on phones (hidden on wide screens, where the
                           panel is always visible). Its [data-filter-count] child shows how many filters are set.
     [data-filter-panel]   the less-used fields (selects, numbers, checkboxes...). Each field is wrapped in
                           [data-filter-field] with a <label>, which names its chip.
     [data-filter-chips]   one removable chip per active filter, so a closed panel still shows what is applied.
   Removing a chip clears that field and fires the same input/change events the page already listens to; for
   forms that reload the page (data-filter-submit on the bar) it submits the form instead.
   Without JavaScript nothing is hidden: the panel is only collapsed once this script has run (.is-ready). */
(function () {
  "use strict";

  var bars = Array.prototype.slice.call(document.querySelectorAll("[data-filterbar]"));
  if (!bars.length) return;

  function controls(panel) {
    return Array.prototype.slice.call(
      panel.querySelectorAll("input:not([type=hidden]):not([type=submit]):not([type=button]), select, textarea")
    );
  }

  function labelOf(el) {
    var field = el.closest("[data-filter-field]");
    var label = field && field.querySelector("label");
    var text = label ? label.textContent : (el.getAttribute("aria-label") || el.name || "");
    return text.replace(/\s*\*\s*$/, "").replace(/\s+/g, " ").trim();
  }

  function isActive(el) {
    if (el.disabled) return false;
    if (el.type === "checkbox" || el.type === "radio") return el.checked;
    return String(el.value || "").trim() !== "" && String(el.value) !== (el.getAttribute("data-filter-default") || "");
  }

  function valueText(el) {
    if (el.type === "checkbox" || el.type === "radio") return "";
    if (el.tagName === "SELECT") {
      var opt = el.options[el.selectedIndex];
      return opt ? opt.text.trim() : "";
    }
    return String(el.value).trim();
  }

  function fire(el) {
    ["input", "change"].forEach(function (type) {
      el.dispatchEvent(new Event(type, { bubbles: true }));
    });
  }

  function clearControl(bar, el) {
    if (el.type === "checkbox" || el.type === "radio") { el.checked = false; }
    else { el.value = el.getAttribute("data-filter-default") || ""; }
    if (bar.hasAttribute("data-filter-submit") && bar.form) {
      if (bar.form.requestSubmit) { bar.form.requestSubmit(); } else { bar.form.submit(); }
    } else {
      fire(el);
    }
  }

  function setup(bar) {
    var panel = bar.querySelector("[data-filter-panel]");
    if (!panel) return;
    var toggle = bar.querySelector("[data-filter-toggle]");
    var badge = bar.querySelector("[data-filter-count]");
    var chips = bar.querySelector("[data-filter-chips]");
    // For a <form data-filterbar>, bar.form is the form itself; otherwise the closest form (if any).
    bar.form = bar.tagName === "FORM" ? bar : bar.closest("form");

    function update() {
      var active = controls(panel).filter(isActive);
      if (badge) {
        badge.textContent = String(active.length);
        badge.hidden = active.length === 0;
      }
      if (!chips) return;
      chips.innerHTML = "";
      active.forEach(function (el) {
        var name = labelOf(el);
        var value = valueText(el);
        var text = value ? name + ": " + value : name;
        var chip = document.createElement("button");
        chip.type = "button";
        chip.className = "ipt-chip";
        chip.setAttribute("aria-label", (bar.getAttribute("data-filter-remove") || "Remove filter") + ": " + text);
        var span = document.createElement("span");
        span.textContent = text;
        var icon = document.createElement("span");
        icon.className = "material-symbols-outlined";
        icon.setAttribute("aria-hidden", "true");
        icon.textContent = "close";
        chip.appendChild(span);
        chip.appendChild(icon);
        chip.addEventListener("click", function () { clearControl(bar, el); update(); });
        chips.appendChild(chip);
      });
      chips.hidden = active.length === 0;
    }

    if (toggle) {
      toggle.addEventListener("click", function () {
        var open = bar.classList.toggle("is-open");
        toggle.setAttribute("aria-expanded", open ? "true" : "false");
      });
    }
    bar.addEventListener("input", update);
    bar.addEventListener("change", update);
    bar.classList.add("is-ready");
    update();
    // Pages that fill the fields after load (e.g. district lists) get a second look.
    setTimeout(update, 400);
    bar.iptFilterUpdate = update;
  }

  bars.forEach(setup);
  window.IPT = window.IPT || {};
  window.IPT.refreshFilterbars = function () {
    bars.forEach(function (bar) { if (bar.iptFilterUpdate) bar.iptFilterUpdate(); });
  };
})();

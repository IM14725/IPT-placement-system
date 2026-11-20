(function () {
  "use strict";

  // Turns <select multiple data-multi-picker data-empty="Any ..."> into a searchable chip picker that looks
  // like the other fields. The real <select> stays in the page (hidden) and is the source of truth, so the
  // form posts exactly as before; without JavaScript the native list is still usable.

  function el(tag, className) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    return node;
  }

  function init(select) {
    var box = select.closest(".ipt-field-box");
    if (!box || select.getAttribute("data-multi-ready")) return;
    select.setAttribute("data-multi-ready", "1");

    var emptyText = select.getAttribute("data-empty") || "Any";
    var options = Array.prototype.slice.call(select.options);

    var wrap = el("div", "ipt-multi");
    var input = el("input");
    input.type = "text";
    input.setAttribute("autocomplete", "off");
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-expanded", "false");
    input.setAttribute("aria-label", select.getAttribute("aria-label") || emptyText);
    var list = el("ul", "ipt-multi-list");
    list.setAttribute("role", "listbox");
    list.setAttribute("aria-multiselectable", "true");
    list.hidden = true;
    wrap.appendChild(input);
    wrap.appendChild(list);

    select.style.display = "none";
    select.setAttribute("aria-hidden", "true");
    select.tabIndex = -1;
    select.insertAdjacentElement("afterend", wrap);
    box.classList.add("ipt-has-multi");

    var shown = [];
    var active = -1;

    function selectedOptions() {
      return options.filter(function (o) { return o.selected; });
    }

    function renderChips() {
      Array.prototype.slice.call(wrap.querySelectorAll(".ipt-multi-chip")).forEach(function (c) { c.remove(); });
      var chosen = selectedOptions();
      chosen.forEach(function (opt) {
        var chip = el("span", "ipt-multi-chip");
        var label = el("span");
        label.textContent = opt.text;
        label.title = opt.text;
        var remove = el("button");
        remove.type = "button";
        remove.setAttribute("aria-label", "Remove " + opt.text);
        remove.textContent = "×";
        remove.addEventListener("mousedown", function (e) { e.preventDefault(); });
        remove.addEventListener("click", function (e) {
          e.stopPropagation();
          toggle(opt);
        });
        chip.appendChild(label);
        chip.appendChild(remove);
        wrap.insertBefore(chip, input);
      });
      input.placeholder = chosen.length ? "" : emptyText;
    }

    function renderList() {
      var q = input.value.toLowerCase().replace(/\s+/g, " ").trim();
      var keepScroll = list.scrollTop;
      shown = options.filter(function (o) { return o.value !== "" && (!q || o.text.toLowerCase().indexOf(q) !== -1); });
      list.innerHTML = "";
      if (!shown.length) {
        var none = el("li", "ipt-multi-none");
        none.textContent = "Nothing matches";
        list.appendChild(none);
      }
      shown.forEach(function (opt, i) {
        var li = el("li", "ipt-multi-option" + (opt.selected ? " is-selected" : "") + (i === active ? " is-active" : ""));
        li.setAttribute("role", "option");
        li.setAttribute("aria-selected", opt.selected ? "true" : "false");
        var mark = el("span", "ipt-multi-mark");
        mark.textContent = opt.selected ? "✓" : "";
        var text = el("span");
        text.textContent = opt.text;
        li.appendChild(mark);
        li.appendChild(text);
        li.addEventListener("mousedown", function (e) {
          e.preventDefault();
          toggle(opt);
        });
        list.appendChild(li);
      });
      list.scrollTop = keepScroll;
    }

    function open() {
      list.hidden = false;
      input.setAttribute("aria-expanded", "true");
      renderList();
    }

    function close() {
      list.hidden = true;
      input.setAttribute("aria-expanded", "false");
      active = -1;
    }

    function toggle(opt) {
      opt.selected = !opt.selected;
      select.dispatchEvent(new Event("change", { bubbles: true }));
      renderChips();
      if (!list.hidden) renderList();
    }

    function setActive(i) {
      active = i;
      renderList();
      var node = list.querySelector(".is-active");
      if (node) {
        // Scroll only the list, never the page.
        var top = node.offsetTop, bottom = top + node.offsetHeight;
        if (top < list.scrollTop) list.scrollTop = top;
        else if (bottom > list.scrollTop + list.clientHeight) list.scrollTop = bottom - list.clientHeight;
      }
    }

    wrap.addEventListener("click", function () { input.focus({ preventScroll: true }); });
    input.addEventListener("focus", open);
    input.addEventListener("input", function () { active = -1; if (list.hidden) open(); else renderList(); });
    input.addEventListener("blur", function () { setTimeout(close, 120); });
    input.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        if (list.hidden) open();
        setActive(Math.min(active + 1, shown.length - 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setActive(Math.max(active - 1, 0));
      } else if (e.key === "Enter") {
        // Enter picks the highlighted option; it must never submit the whole form from this box.
        e.preventDefault();
        if (active >= 0 && shown[active]) toggle(shown[active]);
      } else if (e.key === "Escape") {
        close();
      } else if (e.key === "Backspace" && !input.value) {
        var chosen = selectedOptions();
        if (chosen.length) toggle(chosen[chosen.length - 1]);
      }
    });

    renderChips();
  }

  Array.prototype.slice.call(document.querySelectorAll("select[data-multi-picker]")).forEach(init);
})();

(function () {
  "use strict";

  var institutions = (window.IPT && window.IPT.institutions) || [];

  function initPicker(input) {
    if (!input || !institutions.length) return;

    var wrapper = document.createElement("div");
    wrapper.className = "ipt-combobox";
    wrapper.style.position = "relative";
    wrapper.style.width = "100%";

    input.parentNode.insertBefore(wrapper, input);
    wrapper.appendChild(input);

    var fieldBox = input.closest(".ipt-field-box");
    if (fieldBox) {
      fieldBox.classList.add("ipt-has-combobox");
    }

    var dropdown = document.createElement("ul");
    dropdown.className = "ipt-combobox-list";
    dropdown.setAttribute("role", "listbox");
    dropdown.style.position = "absolute";
    dropdown.style.zIndex = "50";
    dropdown.style.top = "100%";
    dropdown.style.left = "0";
    dropdown.style.right = "0";
    dropdown.style.margin = "4px 0 0";
    dropdown.style.padding = "4px";
    dropdown.style.maxHeight = "240px";
    dropdown.style.overflowY = "auto";
    dropdown.style.background = "#ffffff";
    dropdown.style.border = "1px solid #cbd5e1";
    dropdown.style.borderRadius = "10px";
    dropdown.style.boxShadow = "0 10px 25px rgba(0,0,0,0.12)";
    dropdown.style.listStyle = "none";
    dropdown.style.display = "none";
    wrapper.appendChild(dropdown);

    var noMatch = document.createElement("li");
    noMatch.textContent = "No matching institution found";
    noMatch.style.padding = "10px 12px";
    noMatch.style.color = "#64748b";
    noMatch.style.fontSize = "14px";
    noMatch.style.display = "none";

    var activeIndex = -1;
    var items = [];
    var currentInstitutions = [];

    function normalize(s) {
      return (s || "").toLowerCase().replace(/\s+/g, " ").trim();
    }

    function matches(inst, q) {
      var qn = normalize(q);
      if (!qn) return true;
      return [inst.name, inst.abbreviation].concat(inst.aliases || []).some(function (h) {
        return normalize(h).indexOf(qn) !== -1;
      });
    }

    function renderList() {
      var q = input.value;
      currentInstitutions = q
        ? institutions.filter(function (i) {
            return matches(i, q);
          })
        : institutions.slice();
      dropdown.innerHTML = "";
      items = [];
      activeIndex = -1;

      if (!currentInstitutions.length) {
        dropdown.appendChild(noMatch);
        noMatch.style.display = "block";
        dropdown.style.display = "block";
        return;
      }
      noMatch.style.display = "none";
      currentInstitutions.forEach(function (inst) {
        var li = document.createElement("li");
        li.textContent = inst.name + (inst.abbreviation ? " (" + inst.abbreviation + ")" : "");
        li.style.padding = "9px 12px";
        li.style.borderRadius = "8px";
        li.style.cursor = "pointer";
        li.style.fontSize = "14px";
        li.style.color = "#1e293b";
        li.setAttribute("role", "option");
        li.addEventListener("mousedown", function (e) {
          e.preventDefault();
          select(inst.name);
        });
        li.addEventListener("mouseenter", function () {
          setActive(items.indexOf(li));
        });
        dropdown.appendChild(li);
        items.push(li);
      });
      dropdown.style.display = "block";
    }

    function setActive(idx) {
      items.forEach(function (li, i) {
        if (i === idx) {
          li.style.background = "#eef2ff";
          li.style.color = "#3730a3";
        } else {
          li.style.background = "transparent";
          li.style.color = "#1e293b";
        }
      });
      activeIndex = idx;
      if (idx >= 0 && items[idx]) {
        items[idx].scrollIntoView({ block: "nearest" });
      }
    }

    function select(name) {
      input.value = name;
      dropdown.style.display = "none";
      input.focus();
    }

    function close() {
      dropdown.style.display = "none";
      activeIndex = -1;
    }

    input.addEventListener("focus", renderList);
    input.addEventListener("input", renderList);
    input.addEventListener("keydown", function (e) {
      if (dropdown.style.display !== "block") return;
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setActive(Math.min(activeIndex + 1, currentInstitutions.length - 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setActive(Math.max(activeIndex - 1, 0));
      } else if (e.key === "Enter") {
        if (activeIndex >= 0 && currentInstitutions[activeIndex]) {
          e.preventDefault();
          select(currentInstitutions[activeIndex].name);
        }
      } else if (e.key === "Escape") {
        close();
      }
    });
    input.addEventListener("blur", function () {
      setTimeout(close, 120);
    });
  }

  initPicker(document.getElementById("id_university"));
  initPicker(document.getElementById("dir-university"));
})();
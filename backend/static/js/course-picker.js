(function () {
  "use strict";

  var institutions = (window.IPT && window.IPT.institutions) || [];

  function initCoursePicker(input, uniInputId) {
    if (!input || !institutions.length) return;
    input.setAttribute("autocomplete", "off");

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
    noMatch.textContent = "No matching course found (select university first)";
    noMatch.style.padding = "10px 12px";
    noMatch.style.color = "#64748b";
    noMatch.style.fontSize = "14px";
    noMatch.style.display = "none";

    var activeIndex = -1;
    var items = [];
    var currentCourses = [];

    function normalize(s) {
      return (s || "").toLowerCase().replace(/\s+/g, " ").trim();
    }

    function renderList() {
      var uniInput = document.getElementById(uniInputId);
      var uniName = uniInput ? uniInput.value : "";
      
      var uni = institutions.find(function(i) {
         return i.name === uniName || (i.abbreviation && i.abbreviation === uniName);
      });
      if (!uni && uniName) {
         var qn = normalize(uniName);
         uni = institutions.find(function(i) {
             return normalize(i.name).indexOf(qn) !== -1 || (i.abbreviation && normalize(i.abbreviation).indexOf(qn) !== -1);
         });
      }

      var availableCourses = uni && uni.courses ? uni.courses : [];
      
      var q = normalize(input.value);
      currentCourses = q
        ? availableCourses.filter(function (c) {
            return normalize(c).indexOf(q) !== -1;
          })
        : availableCourses.slice();

      dropdown.innerHTML = "";
      items = [];
      activeIndex = -1;

      if (!currentCourses.length) {
        if (!uniName) {
            noMatch.textContent = "Please select a university first";
        } else if (!availableCourses.length) {
            noMatch.textContent = "No courses found for selected university";
        } else {
            noMatch.textContent = "No matching course found";
        }
        dropdown.appendChild(noMatch);
        noMatch.style.display = "block";
        dropdown.style.display = "block";
        return;
      }
      
      noMatch.style.display = "none";
      currentCourses.forEach(function (course) {
        var li = document.createElement("li");
        li.textContent = course;
        li.style.padding = "9px 12px";
        li.style.borderRadius = "8px";
        li.style.cursor = "pointer";
        li.style.fontSize = "14px";
        li.style.color = "#1e293b";
        li.setAttribute("role", "option");
        li.addEventListener("mousedown", function (e) {
          e.preventDefault();
          select(course);
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
        setActive(Math.min(activeIndex + 1, currentCourses.length - 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setActive(Math.max(activeIndex - 1, 0));
      } else if (e.key === "Enter") {
        if (activeIndex >= 0 && currentCourses[activeIndex]) {
          e.preventDefault();
          select(currentCourses[activeIndex]);
        }
      } else if (e.key === "Escape") {
        close();
      }
    });
    input.addEventListener("blur", function () {
      setTimeout(close, 120);
    });
  }

  initCoursePicker(document.getElementById("id_course"), "id_university");
  initCoursePicker(document.getElementById("dir-course"), "dir-university");
})();

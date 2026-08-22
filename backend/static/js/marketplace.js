(function () {
  "use strict";

  var T = (window.IPT && window.IPT.i18n) || {};
  function t(key, fallback) {
    return T[key] || fallback;
  }

  var region = document.getElementById("filter-region");
  var district = document.getElementById("filter-district");
  var department = document.getElementById("filter-department");
  var level = document.getElementById("filter-level");
  var educationLevel = document.getElementById("filter-education-level");
  var results = document.getElementById("slot-results");

  var page = 1;
  var hasMore = false;
  var loadBtn = document.createElement("div");
  loadBtn.id = "load-more-wrap";
  loadBtn.className = "col-span-full flex justify-center mt-lg";
  results.parentNode.appendChild(loadBtn);

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function baseParams() {
    var params = new URLSearchParams();
    if (region.value) params.set("region", region.value);
    if (district.value) params.set("district", district.value);
    if (department.value.trim()) params.set("department", department.value.trim());
    if (level.value) params.set("level", level.value);
    if (educationLevel.value) params.set("education_level", educationLevel.value);
    return params;
  }

  function fetchPage(p, append) {
    var params = baseParams();
    params.set("page", String(p));
    if (!append) {
      results.innerHTML = "<div class='col-span-full text-on-surface-variant text-body-md'>Loading...</div>";
    }
    return fetch("/api/slots/search/?" + params.toString(), { credentials: "same-origin" })
      .then(function (r) {
        if (!r.ok) throw new Error("Failed to load slots");
        return r.json();
      })
      .then(function (data) {
        page = data.page;
        hasMore = data.has_more;
        render(data.results, append);
        renderLoadMore();
      })
      .catch(function (e) {
        if (!append) {
          results.innerHTML = "<div class='col-span-full text-error text-body-md'>" + esc(e.message) + "</div>";
        }
      });
  }

  function load() {
    fetchPage(1, false);
  }

  function renderLoadMore() {
    if (hasMore) {
      loadBtn.innerHTML =
        '<button type="button" id="load-more-btn" class="bg-transparent text-primary font-metadata text-metadata uppercase tracking-wider border-2 border-primary hover:bg-primary/5 transition-colors duration-300 px-md py-sm rounded-lg">Load more</button>';
      document.getElementById("load-more-btn").addEventListener("click", function () {
        var btn = document.getElementById("load-more-btn");
        btn.disabled = true;
        btn.textContent = "Loading\u2026";
        fetchPage(page + 1, true).then(function () {
          btn.disabled = false;
        });
      });
    } else {
      loadBtn.innerHTML = "";
    }
  }

  function render(slots, append) {
    if (!slots.length && !append) {
      results.innerHTML = "<div class='col-span-full text-on-surface-variant text-body-md'>" + esc(t("noMatch", "No slots match your filters.")) + "</div>";
      return;
    }
    if (append) {
      results.insertAdjacentHTML("beforeend", slots.map(card).join(""));
    } else {
      results.innerHTML = slots.map(card).join("");
    }
  }

  function card(s) {
    var apply = "";
    if (s.available_count > 0) {
      if (window.IPT.canApply) {
        apply =
          '<a href="/student/apply/' + s.id + '/" class="w-full h-11 bg-primary/10 text-primary group-hover:bg-primary group-hover:text-on-primary rounded-xl font-label-caps text-label-caps uppercase tracking-wider transition-all duration-300 flex items-center justify-center gap-2">' +
          esc(t("applyNow", "Apply Now")) + "<span class='material-symbols-outlined text-[16px]'>arrow_forward</span></a>";
      } else {
        apply =
          '<div class="text-body-md text-on-surface-variant bg-surface-container-low rounded-lg px-sm py-2 flex items-center gap-xs">' +
          "<span class='material-symbols-outlined text-[16px]'>verified_user</span>" + esc(t("verificationRequired", "Verification required to apply.")) + "</div>";
      }
    } else {
      apply = '<span class="inline-block bg-error/10 text-error font-label-caps text-label-caps uppercase px-sm py-2 rounded-lg">' + esc(t("slotFull", "Slot Full")) + "</span>";
    }

    var stipend = s.stipend_available
      ? "<span class='font-metadata text-metadata text-[#1b5e20] font-bold'>TZS " + Number(s.stipend_amount || 0).toLocaleString() + "/month</span>"
      : "<span class='font-metadata text-metadata text-on-surface-variant'>" + esc(t("unpaid", "Unpaid")) + "</span>";

    var skills = (s.skills_required || []).map(function (sk) {
      return "<span class='inline-block bg-surface-container-high text-on-surface font-metadata text-metadata px-2 py-1 rounded-md mr-1'>" + esc(sk) + "</span>";
    }).join("");

    return (
      '<div class="group bg-surface-container-lowest rounded-2xl p-lg shadow-sm hover:shadow-xl hover:-translate-y-1 transition-all duration-300 flex flex-col justify-between relative overflow-hidden">' +
      '<div class="absolute -top-16 -right-16 w-32 h-32 bg-secondary-fixed/30 rounded-full blur-2xl group-hover:scale-150 transition-transform duration-500"></div>' +
      '<div class="flex flex-col gap-md relative z-10">' +
      '<div class="flex items-start justify-between gap-sm">' +
      '<div class="flex flex-col gap-xs">' +
      '<span class="font-metadata text-metadata text-on-surface-variant uppercase tracking-wider">' + esc(s.company_name) + "</span>" +
      '<span class="font-metadata text-metadata text-secondary font-semibold">' + esc(s.industry) + "</span>" +
      "</div>" +
      '<span class="px-sm py-1 rounded-full ' + (s.available_count > 0 ? "bg-[#e8f5e9] text-[#1b5e20]" : "bg-error/10 text-error") + ' font-label-caps text-label-caps flex items-center gap-1 whitespace-nowrap">' +
      '<span class="w-1.5 h-1.5 rounded-full ' + (s.available_count > 0 ? "bg-[#1b5e20]" : "bg-error") + ' animate-pulse"></span>' +
      (s.available_count > 0 ? s.available_count + " " + esc(t("available", "Available")) : esc(t("full", "FULL"))) +
      "</span>" +
      "</div>" +
      '<h3 class="font-headline-md text-headline-md text-primary group-hover:text-primary-fixed-variant transition-colors">' + esc(s.title) + "</h3>" +
      '<div class="grid grid-cols-2 gap-sm">' +
      '<div class="flex items-center gap-xs">' +
      '<span class="material-symbols-outlined text-outline text-[18px]">location_on</span>' +
      '<span class="font-metadata text-metadata text-on-surface-variant truncate">' + esc(s.region.name) + " · " + esc(s.district_name) + "</span>" +
      "</div>" +
      '<div class="flex items-center gap-xs">' +
      '<span class="material-symbols-outlined text-outline text-[18px]">schedule</span>' +
      '<span class="font-metadata text-metadata text-on-surface-variant truncate">' + esc(s.role_type) + (s.education_level_display ? " · " + esc(s.education_level_display) : s.level ? " · " + esc(t("year", "Year")) + " " + esc(s.level) : "") + "</span>" +
      "</div>" +
      (s.department ? '<div class="col-span-2 flex items-center gap-xs"><span class="material-symbols-outlined text-outline text-[18px]">school</span><span class="font-metadata text-metadata text-on-surface-variant truncate">' + esc(t("dept", "Dept:")) + " " + esc(s.department) + "</span></div>" : "") +
      '<div class="col-span-2 flex items-center gap-xs">' +
      '<span class="material-symbols-outlined text-[#1b5e20] text-[18px]">payments</span>' +
      stipend +
      "</div>" +
      "</div>" +
      (skills ? '<div class="flex flex-wrap gap-xs mt-sm">' + skills + "</div>" : "") +
      "</div>" +
      '<div class="w-full mt-lg relative z-10">' + apply + "</div>" +
      "</div>"
    );
  }

  function setDistrictDisabled(disabled, text) {
    district.disabled = disabled;
    district.innerHTML = '<option value="">' + text + "</option>";
  }

  region.addEventListener("change", function () {
    if (!region.value) {
      setDistrictDisabled(false, "All districts");
      load();
      return;
    }
    setDistrictDisabled(true, "Loading districts\u2026");
    fetch("/api/locations/districts/?region=" + region.value, { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        district.disabled = false;
        district.innerHTML = '<option value="">All districts</option>';
        data.districts.forEach(function (d) {
          var opt = document.createElement("option");
          opt.value = d.id;
          opt.text = d.name;
          district.appendChild(opt);
        });
      })
      .catch(function () {
        setDistrictDisabled(true, "Couldn't load districts");
      });
    load();
  });
  district.addEventListener("change", load);
  department.addEventListener("input", function () { clearTimeout(window.__iptDebounce); window.__iptDebounce = setTimeout(load, 350); });
  level.addEventListener("change", load);
  educationLevel.addEventListener("change", load);

  load();
})();
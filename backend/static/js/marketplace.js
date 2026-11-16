(function () {
  "use strict";

  var T = (window.IPT && window.IPT.i18n) || {};
  function t(key, fallback) {
    return T[key] || fallback;
  }

  // "1 seat left" / "4 seats left": the same wording as the cards the server draws.
  function seatsLeft(n) {
    return n === 1 ? t("seatOne", "1 seat left") : t("seatMany", "2 seats left").replace("2", n);
  }

  var region = document.getElementById("filter-region");
  var district = document.getElementById("filter-district");
  // The "any district" option text comes from the (translated) template, so JS-built lists match it.
  var ANY_DISTRICT = district && district.options.length ? district.options[0].textContent : "District";
  var department = document.getElementById("filter-department");
  var organization = document.getElementById("filter-organization");
  var educationLevel = document.getElementById("filter-education-level");
  var academicYear = document.getElementById("filter-academic-year");
  var keyword = document.getElementById("filter-keyword");
  var minStipend = document.getElementById("filter-min-stipend");
  var sortSel = document.getElementById("filter-sort");
  var paidOnly = document.getElementById("filter-paid-only");
  var results = document.getElementById("slot-results");
  var quick = "";          // "", "closing" or "new": the quick-filter chips
  var recommended = [];      // top matches, drawn above the results while no search is active
  var recEl = document.getElementById("recommended-data");
  var recBlock = document.getElementById("recommended-block");
  var recGrid = document.getElementById("recommended-results");
  if (recEl) { try { recommended = JSON.parse(recEl.textContent) || []; } catch (e) { recommended = []; } }

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
    if (organization && organization.value.trim()) params.set("organization", organization.value.trim());
    if (educationLevel.value) params.set("education_level", educationLevel.value);
    if (academicYear.value) params.set("academic_year", academicYear.value);
    if (quick) params.set("quick", quick);
    if (keyword && keyword.value.trim()) params.set("q", keyword.value.trim());
    if (minStipend && minStipend.value && Number(minStipend.value) > 0) params.set("min_stipend", String(Math.floor(Number(minStipend.value))));
    if (paidOnly && paidOnly.checked) params.set("paid_only", "1");
    if (sortSel && sortSel.value) params.set("sort", sortSel.value);
    return params;
  }

  // The "Recommended for you" block stays at the top until the student searches for something:
  // any filter or chip replaces it with the plain search results.
  function hasFilters() {
    return baseParams().toString() !== "";
  }

  function syncRecommended() {
    var show = recommended.length > 0 && !hasFilters();
    if (recBlock) recBlock.hidden = !show;
    var heading = document.getElementById("results-heading");
    if (heading) heading.textContent = show ? t("allSlots", "All slots") : t("searchResults", "Search results");
  }

  function tickTimes() {
    if (window.IPT && window.IPT.tickCountdowns) window.IPT.tickCountdowns();
  }

  function fetchPage(p, append) {
    var params = baseParams();
    params.set("page", String(p));
    if (!append) {
      results.innerHTML = (window.IPT && window.IPT.skeletonCards)
        ? window.IPT.skeletonCards(4)
        : "<div class='col-span-full text-on-surface-variant text-body-md'>" + t("loading", "Loading...") + "</div>";
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
    syncRecommended();
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
    tickTimes();
  }

  function renderRecommended() {
    if (!recGrid) return;
    recGrid.innerHTML = recommended.map(card).join("");
    tickTimes();
  }

  function formatDeadline(iso) {
    var d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    return d.toLocaleString(undefined, { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
  }

  var saved = {};
  ((window.IPT && window.IPT.savedSlots) || []).forEach(function (id) { saved[id] = true; });

  function saveButton(id) {
    var on = !!saved[id];
    var label = on ? t("unsaveSlot", "Remove from saved") : t("saveSlot", "Save slot");
    return (
      '<button type="button" data-save-slot="' + esc(id) + '" aria-pressed="' + (on ? "true" : "false") + '" ' +
      'title="' + esc(label) + '" aria-label="' + esc(label) + '" ' +
      'class="w-9 h-9 rounded-full flex items-center justify-center hover:bg-primary/10 transition-colors ' + (on ? "text-primary" : "text-on-surface-variant") + '">' +
      '<span class="material-symbols-outlined"' + (on ? " style=\"font-variation-settings: 'FILL' 1;\"" : "") + ">bookmark</span></button>"
    );
  }

  function postJson(url, body) {
    return fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "X-CSRFToken": window.IPT.csrf, "Accept": "application/json" },
      body: body
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) {
        if (!r.ok) throw new Error(data.detail || "Request failed");
        return data;
      });
    });
  }

  document.addEventListener("click", function (e) {
    var btn = e.target.closest ? e.target.closest("[data-save-slot]") : null;
    if (!btn) return;
    e.preventDefault();
    var id = btn.getAttribute("data-save-slot");
    btn.disabled = true;
    postJson("/student/slots/" + encodeURIComponent(id) + "/save/", new FormData())
      .then(function (data) {
        if (data.saved) { saved[id] = true; } else { delete saved[id]; }
        document.querySelectorAll('[data-save-slot="' + id + '"]').forEach(function (b) { b.outerHTML = saveButton(id); });
        window.IPT.toast("success", data.saved ? t("slotSaved", "Slot saved") : t("slotUnsaved", "Removed from saved slots"), "");
      })
      .catch(function (err) {
        btn.disabled = false;
        window.IPT.toast("error", err.message, "");
      });
  });

  var saveSearchBtn = document.getElementById("save-search-btn");
  if (saveSearchBtn) {
    saveSearchBtn.addEventListener("click", function () {
      var body = new FormData();
      baseParams().forEach(function (value, key) { body.append(key, value); });
      saveSearchBtn.disabled = true;
      postJson("/student/alerts/", body)
        .then(function (data) {
          window.IPT.toast("success", t("alertSaved", "Alert saved"), (data.label ? data.label + ". " : "") + t("alertSavedBody", "We will notify you when a matching slot is posted."));
        })
        .catch(function (err) {
          window.IPT.toast("error", t("alertFailed", "Could not save the alert"), err.message);
        })
        .then(function () { saveSearchBtn.disabled = false; });
    });
  }

  // Mirrors apps.applications.services.eligibility_blocker; the server enforces it regardless.
  function ineligibleReason(s) {
    var me = window.IPT.student || {};
    if (s.education_level && me.level && me.level < s.education_level) {
      return t("requiresLevel", "Requires Level") + " " + s.education_level;
    }
    if (s.academic_year && me.year && (!me.level || me.level === s.education_level) && me.year !== s.academic_year) {
      return t("requiresYear", "For a different year of study");
    }
    return "";
  }

  function card(s) {
    var apply = "";
    var blocked = ineligibleReason(s);
    if (s.deadline_passed) {
      apply = '<span class="inline-block bg-surface-container-high text-on-surface-variant font-label-caps text-label-caps uppercase px-sm py-2 rounded-lg">' + esc(t("deadlinePassed", "Applications closed")) + "</span>";
    } else if (s.available_count > 0 && blocked) {
      apply = '<span class="inline-flex items-center gap-xs bg-surface-container-high text-on-surface-variant font-label-caps text-label-caps uppercase px-sm py-2 rounded-lg"><span class="material-symbols-outlined text-[16px]">block</span>' + esc(blocked) + "</span>";
    } else if (s.available_count > 0) {
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
      '<span class="font-metadata text-metadata text-on-surface-variant uppercase tracking-wider">' + esc(s.organization_name) + "</span>" +
      '<span class="font-metadata text-metadata text-secondary font-semibold">' + esc(s.industry) + "</span>" +
      (s.is_new ? '<span class="w-fit px-2 py-0.5 rounded-full bg-primary text-on-primary font-label-caps text-label-caps uppercase">' + esc(t("newSlot", "New")) + "</span>" : "") +
      "</div>" +
      '<div class="flex items-center gap-1 shrink-0">' +
      saveButton(s.id) +
      '<span class="px-sm py-1 rounded-full ' + (s.available_count > 0 ? "bg-[#e8f5e9] text-[#1b5e20]" : "bg-error/10 text-error") + ' font-label-caps text-label-caps flex items-center gap-1 whitespace-nowrap">' +
      '<span class="w-1.5 h-1.5 rounded-full ' + (s.available_count > 0 ? "bg-[#1b5e20]" : "bg-error") + ' animate-pulse"></span>' +
      (s.available_count > 0 ? esc(seatsLeft(s.available_count)) : esc(t("full", "FULL"))) +
      "</span>" +
      "</div>" +
      "</div>" +
      '<h3 class="font-headline-md text-headline-md text-primary group-hover:text-primary-fixed-variant transition-colors"><a href="/student/slots/' + esc(s.id) + '/" class="hover:underline" title="' + esc(t("viewDetails", "View details")) + '">' + esc(s.title) + "</a></h3>" +
      matchBlock(s) +
      '<div class="grid grid-cols-2 gap-sm">' +
      '<div class="flex items-center gap-xs">' +
      '<span class="material-symbols-outlined text-outline text-[18px]">location_on</span>' +
      '<span class="font-metadata text-metadata text-on-surface-variant truncate">' + esc(s.region.name) + " · " + esc(s.district_name) + "</span>" +
      "</div>" +
      '<div class="flex items-center gap-xs">' +
      '<span class="material-symbols-outlined text-outline text-[18px]">schedule</span>' +
      '<span class="font-metadata text-metadata text-on-surface-variant truncate">' + esc(s.role_type) + (s.education_level_display ? " · " + esc(s.education_level_display) : "") + (s.academic_year_display ? " - " + esc(s.academic_year_display) : "") + "</span>" +
      "</div>" +
      (s.department ? '<div class="col-span-2 flex items-center gap-xs"><span class="material-symbols-outlined text-outline text-[18px]">school</span><span class="font-metadata text-metadata text-on-surface-variant truncate">' + esc(t("dept", "Dept:")) + " " + esc(s.department) + "</span></div>" : "") +
      (s.placement_label ? '<div class="col-span-2 flex items-center gap-xs"><span class="material-symbols-outlined text-outline text-[18px]">date_range</span><span class="font-metadata text-metadata text-on-surface-variant truncate">' + esc(s.placement_label) + "</span></div>" : "") +
      '<div class="col-span-2 flex items-center gap-xs">' +
      '<span class="material-symbols-outlined text-[#1b5e20] text-[18px]">payments</span>' +
      stipend +
      "</div>" +
      "</div>" +
      (skills ? '<div class="flex flex-wrap gap-xs mt-sm">' + skills + "</div>" : "") +
      "</div>" +
      '<div class="w-full mt-lg relative z-10">' + apply + "</div>" +
      timeLeftLine(s) +
      "</div>"
    );
  }

  // Recommended slots carry a match score and the reasons for it.
  function matchBlock(s) {
    if (s.match == null) return "";
    var reasons = (s.reasons || []).map(function (r) {
      return '<span class="px-2 py-0.5 rounded-full bg-primary/10 text-primary font-metadata text-metadata">' + esc(r) + "</span>";
    }).join("");
    return (
      '<div class="flex flex-col gap-1">' +
      '<div class="flex items-center justify-between gap-sm"><span class="font-metadata text-metadata text-on-surface-variant">' + esc(t("match", "Match")) + "</span>" +
      '<span class="font-metadata text-metadata font-bold text-primary">' + esc(s.match) + "%</span></div>" +
      '<div class="w-full h-1.5 bg-surface-container rounded-full overflow-hidden" role="img" aria-label="' + esc(t("match", "Match")) + " " + esc(s.match) + '%"><div class="h-full bg-primary rounded-full" style="width:' + Number(s.match) + '%"></div></div>' +
      (reasons ? '<div class="flex flex-wrap gap-1 mt-1">' + reasons + "</div>" : "") +
      "</div>"
    );
  }

  // Time left under each slot: hours, or minutes when under an hour (static/js/countdown.js keeps it live).
  function timeLeftLine(s) {
    var base = "mt-sm flex items-center justify-center gap-xs font-metadata text-metadata relative z-10 ";
    if (!s.application_deadline) {
      return '<div class="' + base + 'text-on-surface-variant"><span class="material-symbols-outlined text-[16px]">all_inclusive</span><span>' + esc(window.IPT.timeLeftText("")) + "</span></div>";
    }
    var left = new Date(s.application_deadline).getTime() - Date.now();
    var tone = s.deadline_passed ? "text-error" : (left <= 7 * 86400000 ? "text-[#a16207] font-semibold" : "text-on-surface-variant");
    return (
      '<div class="' + base + tone + '"><span class="material-symbols-outlined text-[16px]">hourglass_top</span>' +
      '<span data-timeleft="' + esc(s.application_deadline) + '">' + esc(window.IPT.timeLeftText(s.application_deadline)) + "</span></div>"
    );
  }

  // Quick filter chips: "Closing within 7 days" and "New this week" (one at a time; press again to clear).
  var chips = document.querySelectorAll("[data-quick]");
  var CHIP_ON = ["bg-primary", "text-on-primary", "border-primary"];
  var CHIP_OFF = ["bg-surface-container-lowest", "text-on-surface", "border-outline-variant"];
  function syncChips() {
    chips.forEach(function (chip) {
      var on = chip.getAttribute("data-quick") === quick;
      chip.setAttribute("aria-pressed", on ? "true" : "false");
      CHIP_ON.forEach(function (c) { chip.classList.toggle(c, on); });
      CHIP_OFF.forEach(function (c) { chip.classList.toggle(c, !on); });
    });
  }
  chips.forEach(function (chip) {
    chip.addEventListener("click", function () {
      var value = chip.getAttribute("data-quick");
      quick = quick === value ? "" : value;
      syncChips();
      load();
    });
  });

  function setDistrictDisabled(disabled, text) {
    district.disabled = disabled;
    district.innerHTML = '<option value="">' + text + "</option>";
  }

  region.addEventListener("change", function () {
    if (!region.value) {
      setDistrictDisabled(false, ANY_DISTRICT);
      load();
      return;
    }
    setDistrictDisabled(true, "Loading districts\u2026");
    fetch("/api/locations/districts/?region=" + region.value, { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        district.disabled = false;
        district.innerHTML = '<option value="">' + ANY_DISTRICT + "</option>";
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
  if (organization) {
    organization.addEventListener("input", function () { clearTimeout(window.__iptDebounce); window.__iptDebounce = setTimeout(load, 350); });
  }
  educationLevel.addEventListener("change", load);
  academicYear.addEventListener("change", load);
  if (keyword) keyword.addEventListener("input", function () { clearTimeout(window.__iptDebounce); window.__iptDebounce = setTimeout(load, 350); });
  if (minStipend) minStipend.addEventListener("input", function () { clearTimeout(window.__iptDebounce); window.__iptDebounce = setTimeout(load, 500); });
  if (sortSel) sortSel.addEventListener("change", load);
  if (paidOnly) paidOnly.addEventListener("change", load);

  // Start from the filters in the URL (e.g. "See results" on the My alerts page).
  var initial = new URLSearchParams(window.location.search);
  if (initial.get("quick") === "closing" || initial.get("quick") === "new") quick = initial.get("quick");
  syncChips();
  renderRecommended();
  function has(select, value) {
    for (var i = 0; i < select.options.length; i++) { if (select.options[i].value === value) return true; }
    return false;
  }
  if (initial.get("department")) department.value = initial.get("department");
  if (organization && initial.get("organization")) organization.value = initial.get("organization");
  if (keyword && initial.get("q")) keyword.value = initial.get("q");
  if (minStipend && /^\d+$/.test(initial.get("min_stipend") || "")) minStipend.value = initial.get("min_stipend");
  if (paidOnly && initial.get("paid_only") === "1") paidOnly.checked = true;
  if (sortSel && has(sortSel, initial.get("sort") || "x")) sortSel.value = initial.get("sort");
  if (initial.get("education_level") && has(educationLevel, initial.get("education_level"))) educationLevel.value = initial.get("education_level");
  if (initial.get("academic_year") && has(academicYear, initial.get("academic_year"))) academicYear.value = initial.get("academic_year");
  if (initial.get("region") && has(region, initial.get("region"))) {
    region.value = initial.get("region");
    fetch("/api/locations/districts/?region=" + region.value, { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        district.innerHTML = '<option value="">' + ANY_DISTRICT + "</option>";
        data.districts.forEach(function (d) {
          var opt = document.createElement("option");
          opt.value = d.id;
          opt.text = d.name;
          district.appendChild(opt);
        });
        if (initial.get("district") && has(district, initial.get("district"))) district.value = initial.get("district");
      })
      .catch(function () {})
      .then(load);
  } else {
    load();
  }
})();

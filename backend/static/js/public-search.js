/* Welcome-page slot search: updates the cards in place as the filters change.
   Without JavaScript the same <form> works as a normal GET to the welcome page. The cards are rendered by
   the server (includes/public_slots.html) so the markup and translations are always the same. */
(function () {
  "use strict";

  var form = document.getElementById("slot-search");
  var results = document.getElementById("slot-results");
  if (!form || !results) return;

  var region = form.elements.region;
  var district = form.elements.district;
  var industry = form.elements.industry;
  var level = form.elements.level;
  var clearLink = document.getElementById("search-clear");
  var timer = null;
  var latest = 0;

  function params() {
    var p = new URLSearchParams();
    [region, district, industry, level].forEach(function (el) {
      if (el && el.value && !el.disabled) { p.set(el.name, el.value.trim()); }
    });
    return p;
  }

  function option(value, text) {
    var o = document.createElement("option");
    o.value = value;
    o.textContent = text;
    return o;
  }

  function resetDistricts(disabled) {
    district.innerHTML = "";
    district.appendChild(option("", form.dataset.allDistricts));
    district.disabled = !!disabled;
  }

  function loadDistricts(regionId) {
    if (!regionId) { resetDistricts(true); return Promise.resolve(); }
    district.innerHTML = "";
    district.appendChild(option("", form.dataset.loadingDistricts));
    district.disabled = true;
    return fetch(form.dataset.districtsUrl + "?region=" + encodeURIComponent(regionId), { credentials: "same-origin" })
      .then(function (r) { if (!r.ok) throw new Error("bad response"); return r.json(); })
      .then(function (data) {
        resetDistricts(false);
        (data.districts || []).forEach(function (d) { district.appendChild(option(d.id, d.name)); });
      })
      .catch(function () { resetDistricts(true); });
  }

  function search() {
    var query = params().toString();
    var ticket = ++latest;
    results.setAttribute("aria-busy", "true");
    results.style.opacity = "0.6";
    fetch(form.dataset.fragmentUrl + (query ? "?" + query : ""), { credentials: "same-origin", headers: { "X-Requested-With": "XMLHttpRequest" } })
      .then(function (r) { if (!r.ok) throw new Error("bad response"); return r.text(); })
      .then(function (html) {
        if (ticket !== latest) return; // a newer search is already on its way
        results.innerHTML = html;
        if (clearLink) { clearLink.classList.toggle("hidden", !query); }
        try { history.replaceState(null, "", location.pathname + (query ? "?" + query : "") + "#slots"); } catch (e) { /* ignore */ }
      })
      .catch(function () {
        if (ticket !== latest) return;
        var msg = document.createElement("p");
        msg.className = "font-body-md text-body-md text-error text-center";
        msg.setAttribute("role", "alert");
        msg.textContent = form.dataset.error;
        results.innerHTML = "";
        results.appendChild(msg);
      })
      .then(function () {
        if (ticket !== latest) return;
        results.setAttribute("aria-busy", "false");
        results.style.opacity = "";
      });
  }

  region.addEventListener("change", function () {
    loadDistricts(region.value).then(search);
  });
  district.addEventListener("change", search);
  level.addEventListener("change", search);
  industry.addEventListener("input", function () { clearTimeout(timer); timer = setTimeout(search, 350); });
  industry.addEventListener("change", search);

  form.addEventListener("submit", function (e) { e.preventDefault(); clearTimeout(timer); search(); });
  if (clearLink) {
    clearLink.addEventListener("click", function (e) {
      e.preventDefault();
      form.reset();
      region.value = ""; industry.value = ""; level.value = "";
      resetDistricts(true);
      search();
    });
  }
})();

(function () {
  "use strict";

  var regionSelect = document.getElementById("id_region");
  var districtSelect = document.getElementById("id_district");
  if (!regionSelect || !districtSelect) return;

  var wardSelect = document.getElementById("id_ward");
  var hasWard = !!wardSelect;

  // filter mode is used by admin directory: "All regions"/"All districts" placeholders.
  var isFilter = window.IPT.cascadeMode === "filter";

  var regions = window.IPT.regions || [];
  var initialRegion = window.IPT.profileRegion;
  var initialDistrict = window.IPT.profileDistrict;
  var initialWard = window.IPT.profileWard;

  var REGION_PLACEHOLDER = isFilter ? "All regions" : "Select region";
  var DISTRICT_PLACEHOLDER = isFilter ? "All districts" : "Select district";
  var WARD_PLACEHOLDER = isFilter ? "All wards" : "Select ward";

  function buildOption(value, text, selected, disabled) {
    var opt = document.createElement("option");
    opt.value = value;
    opt.text = text;
    if (selected) opt.selected = true;
    if (disabled) opt.disabled = true;
    return opt;
  }

  function setDistrictState(disabled, placeholder) {
    districtSelect.innerHTML = "";
    districtSelect.appendChild(buildOption("", placeholder, !disabled, disabled));
    districtSelect.disabled = disabled;
  }

  function setWardState(disabled, placeholder) {
    if (!wardSelect) return;
    wardSelect.innerHTML = "";
    wardSelect.appendChild(buildOption("", placeholder, !disabled, disabled));
    wardSelect.disabled = disabled;
  }

  function loadWards(districtId, selectedWard) {
    if (!wardSelect) return;
    if (!districtId) {
      // In filter mode "All wards" stays enabled so admins can clear the ward
      // filter without picking a region/district first.
      setWardState(isFilter ? false : true, WARD_PLACEHOLDER);
      return;
    }
    setWardState(true, "Loading wards\u2026");
    fetch("/api/locations/wards/?district=" + districtId, { credentials: "same-origin" })
      .then(function (r) {
        if (!r.ok) throw new Error("bad response");
        return r.json();
      })
      .then(function (data) {
        wardSelect.innerHTML = "";
        wardSelect.appendChild(buildOption("", WARD_PLACEHOLDER, !selectedWard, false));
        data.wards.forEach(function (w) {
          wardSelect.appendChild(
            buildOption(w.id, w.name, String(w.id) === String(selectedWard), false)
          );
        });
        wardSelect.disabled = false;
      })
      .catch(function () {
        setWardState(isFilter ? false : true, "Couldn't load wards");
      });
  }

  function loadDistricts(regionId, selectedDistrict, selectedWard) {
    if (!regionId) {
      // In filter mode "All districts" stays enabled so the admin can clear the
      // district filter without picking a region first.
      setDistrictState(isFilter ? false : true, DISTRICT_PLACEHOLDER);
      loadWards(null, null);
      return;
    }
    setDistrictState(true, "Loading districts\u2026");
    fetch("/api/locations/districts/?region=" + regionId, { credentials: "same-origin" })
      .then(function (r) {
        if (!r.ok) throw new Error("bad response");
        return r.json();
      })
      .then(function (data) {
        districtSelect.innerHTML = "";
        districtSelect.appendChild(buildOption("", DISTRICT_PLACEHOLDER, !selectedDistrict, false));
        data.districts.forEach(function (d) {
          districtSelect.appendChild(
            buildOption(d.id, d.name, String(d.id) === String(selectedDistrict), false)
          );
        });
        districtSelect.disabled = false;
        loadWards(districtSelect.value || null, selectedWard);
      })
      .catch(function () {
        setDistrictState(isFilter ? false : true, "Couldn't load districts");
      });
  }

  regionSelect.addEventListener("change", function () {
    loadDistricts(regionSelect.value, null, null);
  });

  districtSelect.addEventListener("change", function () {
    loadWards(districtSelect.value || null, null);
  });

  // Seed dropdowns with provided data, then load districts for the saved region.
  if (regions.length) {
    regionSelect.innerHTML = "";
    regionSelect.appendChild(buildOption("", REGION_PLACEHOLDER, !initialRegion, false));
    regions.forEach(function (r) {
      regionSelect.appendChild(
        buildOption(r.id, r.name, String(r.id) === String(initialRegion), false)
      );
    });
  }
  loadDistricts(initialRegion, initialDistrict, initialWard);
})();

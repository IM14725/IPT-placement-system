/* Side drawer sections. A page with sections (profile / account & security, notifications / slot alerts...)
   lists them under its drawer item:

     <div class="side-group is-open" data-side-group>
       <div class="side-group-row"><a class="side-link" href="/page/">Parent</a><button data-side-toggle>...</button></div>
       <div class="side-sub"><a class="side-sub-link" href="/page/#security" data-sub-match="#security">...</a> ...</div>
     </div>

   The chevron opens/closes the list (the group of the current page starts open). The link for the section that
   is showing gets .active: matched by the address hash (#security), by ?tab=... for server-rendered tabs, or
   by the section the page reports (tabs.js sets IPT.activeTab). A link to the page you are already on only
   changes the hash, so the drawer is closed for you. */
(function () {
  "use strict";

  var groups = Array.prototype.slice.call(document.querySelectorAll("[data-side-group]"));
  if (!groups.length) return;

  function samePath(href) {
    var a = document.createElement("a");
    a.href = href;
    return a.pathname === location.pathname;
  }

  function isCurrent(link) {
    if (!samePath(link.getAttribute("href"))) return false;
    var match = link.getAttribute("data-sub-match") || "";
    var isDefault = link.hasAttribute("data-sub-default");
    if (match.charAt(0) === "#") {
      var shown = (window.IPT && window.IPT.activeTab) || location.hash.slice(1);
      return shown ? shown === match.slice(1) : isDefault;
    }
    if (match.charAt(0) === "?") {
      var want = match.slice(1);
      if (location.search.indexOf(want) !== -1) return true;
      return isDefault && !/[?&]tab=/.test(location.search);
    }
    return false;
  }

  function closeDrawer() {
    var toggle = document.getElementById("sidebar-toggle");
    if (document.body.classList.contains("sb-open") && toggle) toggle.click();
  }

  function mark() {
    groups.forEach(function (group) {
      Array.prototype.forEach.call(group.querySelectorAll(".side-sub-link"), function (link) {
        var on = isCurrent(link);
        link.classList.toggle("active", on);
        if (on) { link.setAttribute("aria-current", "page"); } else { link.removeAttribute("aria-current"); }
      });
    });
  }

  groups.forEach(function (group) {
    var toggle = group.querySelector("[data-side-toggle]");
    if (toggle) {
      toggle.addEventListener("click", function () {
        var open = group.classList.toggle("is-open");
        toggle.setAttribute("aria-expanded", open ? "true" : "false");
      });
    }
    Array.prototype.forEach.call(group.querySelectorAll(".side-sub-link"), function (link) {
      link.addEventListener("click", function () {
        if (samePath(link.getAttribute("href"))) { setTimeout(function () { mark(); closeDrawer(); }, 0); }
      });
    });
  });

  window.addEventListener("hashchange", mark);
  document.addEventListener("ipt:tab", mark);
  mark();
  setTimeout(mark, 300);
})();

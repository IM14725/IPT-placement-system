/* Live countdowns. Purely visual: the server enforces every deadline.

   1. Exact countdown (payment, cancel window):
        <span data-countdown="2026-11-12T14:00:00+00:00" data-expired="Expired"></span>   ->  "2h 14m 05s"
   2. Slot deadline "time left", under each slot:
        <span data-timeleft="2026-11-12T14:00:00+00:00"></span>   ->  "12 days left" (2+ days), "36 hours left",
        or "25 minutes left" when less than one hour remains, and "Applications closed" once it has passed.

   Cards the marketplace script draws after the page loaded are picked up too: the timer always runs,
   and IPT.tickCountdowns() / IPT.timeLeftText() are exposed for the scripts that draw them. */
(function () {
  "use strict";

  window.IPT = window.IPT || {};

  var TEXT = {
    day: "{n} day left", days: "{n} days left",
    hour: "{n} hour left", hours: "{n} hours left",
    minute: "{n} minute left", minutes: "{n} minutes left",
    closed: "Applications closed", none: "No deadline"
  };

  function text(key) {
    var custom = window.IPT.timeLeft || {};
    return custom[key] || TEXT[key];
  }

  function pad(n) { return n < 10 ? "0" + n : String(n); }

  function format(ms) {
    var total = Math.floor(ms / 1000);
    var days = Math.floor(total / 86400);
    var hours = Math.floor((total % 86400) / 3600);
    var mins = Math.floor((total % 3600) / 60);
    var secs = total % 60;
    if (days > 0) return days + "d " + hours + "h " + pad(mins) + "m";
    if (hours > 0) return hours + "h " + pad(mins) + "m " + pad(secs) + "s";
    return mins + "m " + pad(secs) + "s";
  }

  // Same rule as apps/core/timeleft.py: hours, or minutes when under one hour.
  function timeLeftText(iso) {
    if (!iso) return text("none");
    var end = new Date(iso).getTime();
    if (isNaN(end)) return "";
    var seconds = (end - Date.now()) / 1000;
    if (seconds <= 0) return text("closed");
    if (seconds < 3600) {
      var minutes = Math.max(1, Math.floor(seconds / 60));
      return text(minutes === 1 ? "minute" : "minutes").replace("{n}", minutes);
    }
    if (seconds >= 2 * 86400) {
      var days = Math.floor(seconds / 86400);
      return text(days === 1 ? "day" : "days").replace("{n}", days);
    }
    var hours = Math.floor(seconds / 3600);
    return text(hours === 1 ? "hour" : "hours").replace("{n}", hours);
  }

  function tick() {
    var exact = document.querySelectorAll("[data-countdown]");
    for (var i = 0; i < exact.length; i++) {
      var el = exact[i];
      var end = new Date(el.getAttribute("data-countdown")).getTime();
      if (isNaN(end)) continue;
      var left = end - Date.now();
      if (left <= 0) {
        el.textContent = el.getAttribute("data-expired") || "0m 00s";
        el.classList.add("is-expired");
      } else {
        el.textContent = format(left);
      }
    }
    var slots = document.querySelectorAll("[data-timeleft]");
    for (var j = 0; j < slots.length; j++) {
      var label = timeLeftText(slots[j].getAttribute("data-timeleft"));
      if (label && slots[j].textContent !== label) slots[j].textContent = label;
    }
  }

  window.IPT.timeLeftText = timeLeftText;
  window.IPT.tickCountdowns = tick;

  function start() {
    tick();
    setInterval(tick, 1000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();

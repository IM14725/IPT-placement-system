/* Header bell: unread badge + dropdown with the latest five notifications.
   Loads once per page (never on a timer: a background poll would keep the 10-minute idle session
   alive) and then stays current from the WebSocket's "ipt:notification" events. */
(function () {
  "use strict";

  var bell = document.getElementById("notif-bell");
  var panel = document.getElementById("notif-panel");
  var badge = document.getElementById("notif-badge");
  var items = document.getElementById("notif-items");
  if (!bell || !panel || !badge || !items) return;

  var unread = 0;
  var latest = [];

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function ago(iso) {
    var then = new Date(iso).getTime();
    if (isNaN(then)) return "";
    var mins = Math.max(0, Math.round((Date.now() - then) / 60000));
    if (mins < 1) return "now";
    if (mins < 60) return mins + "m";
    var hours = Math.round(mins / 60);
    if (hours < 24) return hours + "h";
    return Math.round(hours / 24) + "d";
  }

  function renderBadge() {
    if (unread > 0) {
      badge.textContent = unread > 99 ? "99+" : String(unread);
      badge.classList.remove("hidden");
    } else {
      badge.classList.add("hidden");
    }
  }

  function renderItems() {
    if (!latest.length) {
      items.innerHTML = '<p class="px-md py-md font-body-md text-body-md text-on-surface-variant">' + esc(items.dataset.empty) + "</p>";
      return;
    }
    items.innerHTML = latest.map(function (n) {
      return (
        '<a href="' + esc(n.url) + '" class="flex items-start gap-sm px-md py-sm border-b border-surface-container hover:bg-surface-container-low transition-colors' + (n.is_read ? "" : " bg-primary/5") + '">' +
        '<span class="mt-1.5 w-2 h-2 rounded-full shrink-0 ' + (n.is_read ? "bg-transparent" : "bg-primary") + '"></span>' +
        '<span class="flex flex-col min-w-0 flex-1">' +
        '<span class="font-body-md text-body-md text-on-surface ' + (n.is_read ? "font-medium" : "font-bold") + ' truncate">' + esc(n.title) + "</span>" +
        '<span class="font-metadata text-metadata text-on-surface-variant" style="display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden">' + esc(n.body) + "</span>" +
        "</span>" +
        '<span class="font-metadata text-metadata text-outline shrink-0">' + esc(ago(n.created_at)) + "</span>" +
        "</a>"
      );
    }).join("");
  }

  function load() {
    fetch("/notifications/summary/", { credentials: "same-origin", cache: "no-store", headers: { "X-Requested-With": "XMLHttpRequest" } })
      .then(function (r) { if (!r.ok) throw new Error("failed"); return r.json(); })
      .then(function (data) {
        unread = data.unread || 0;
        latest = data.latest || [];
        renderBadge();
        renderItems();
      })
      .catch(function () {
        items.innerHTML = '<p class="px-md py-md font-body-md text-body-md text-on-surface-variant">' + esc(items.dataset.error) + "</p>";
      });
  }

  function setOpen(open) {
    panel.classList.toggle("hidden", !open);
    bell.setAttribute("aria-expanded", open ? "true" : "false");
  }

  bell.addEventListener("click", function (e) {
    e.stopPropagation();
    setOpen(panel.classList.contains("hidden"));
  });
  document.addEventListener("click", function (e) {
    if (!panel.contains(e.target) && e.target !== bell) setOpen(false);
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") setOpen(false);
  });

  // Live push from the WebSocket (static/js/ws.js).
  document.addEventListener("ipt:notification", function (e) {
    var msg = e.detail || {};
    if (!msg.notification_id) return;
    unread += 1;
    latest.unshift({
      id: msg.notification_id,
      title: msg.title || "Notification",
      body: msg.body || "",
      is_read: false,
      created_at: msg.created_at || new Date().toISOString(),
      url: "/notifications/" + msg.notification_id + "/open/"
    });
    latest = latest.slice(0, 5);
    renderBadge();
    renderItems();
  });

  load();
})();

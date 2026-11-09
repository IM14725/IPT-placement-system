(function () {
  "use strict";

  var socket = null;
  var retries = 0;
  var MAX_RETRIES = 8;

  function connect() {
    if (!window.IPT.currentUserId) return;
    fetch("/api/auth/ws-ticket/", { credentials: "same-origin", cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data || !data.ticket) return;
        var proto = location.protocol === "https:" ? "wss://" : "ws://";
        var url = proto + location.hostname + ":8001/ws/notifications/" + encodeURIComponent(window.IPT.currentUserId) + "?ticket=" + encodeURIComponent(data.ticket);
        socket = new WebSocket(url);

        socket.onopen = function () { retries = 0; };

        socket.onmessage = function (event) {
          var msg;
          try { msg = JSON.parse(event.data); } catch (e) { return; }
          if (msg.type === "connected") return;
          if (msg.type === "session_revoked") {
            // The server already ended this session; just leave the (now anonymous) page.
            window.location.href = "/auth/login/?reason=" + (msg.reason === "signed_in_elsewhere" ? "elsewhere" : "expired");
            return;
          }
          window.IPT.toast("success", msg.title || "Notification", msg.body || "");
          var evt = new CustomEvent("ipt:notification", { detail: msg });
          document.dispatchEvent(evt);
        };

        socket.onclose = function () {
          if (retries < MAX_RETRIES) {
            retries += 1;
            setTimeout(connect, 1000 * Math.min(30, Math.pow(2, retries)));
          }
        };

        socket.onerror = function () { try { socket.close(); } catch (e) {} };
      })
      .catch(function () {});
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", connect);
  } else {
    connect();
  }
})();
(function () {
  "use strict";

  var socket = null;
  var retries = 0;
  var MAX_RETRIES = 8;

  function connect() {
    if (!window.IPT.currentUserId) return;
    fetch("/api/auth/my-token/", { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var url = "ws://" + location.hostname + ":8001/ws/notifications/" + window.IPT.currentUserId + "?token=" + data.token;
        socket = new WebSocket(url);

        socket.onopen = function () { retries = 0; };

        socket.onmessage = function (event) {
          var msg;
          try { msg = JSON.parse(event.data); } catch (e) { return; }
          if (msg.type === "connected") return;
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
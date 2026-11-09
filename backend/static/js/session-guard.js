(function () {
  "use strict";
  // The server decides when a session ends (10 minutes from login, or a login elsewhere).
  // This only notices it: poll the status endpoint and leave when it says the session is gone.
  var POLL_MS = 20000;
  var warned = false;

  function leave(reason) {
    window.location.href = "/auth/login/?reason=" + reason;
  }

  function check() {
    fetch("/auth/session-status/", { credentials: "same-origin", cache: "no-store" })
      .then(function (r) {
        if (r.status === 401) { leave("expired"); return null; }
        return r.json();
      })
      .then(function (data) {
        if (!data) return;
        if (data.expires_in <= 60 && !warned && window.IPT && window.IPT.toast) {
          warned = true;
          window.IPT.toast("warning", "Session ending soon", "For your security you will need to sign in again within a minute.");
        }
      })
      .catch(function () { /* offline: the server still enforces expiry */ });
  }

  setInterval(check, POLL_MS);
  document.addEventListener("visibilitychange", function () { if (!document.hidden) check(); });
})();

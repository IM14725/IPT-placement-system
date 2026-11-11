(function () {
  "use strict";
  // The server decides when a session ends: 10 minutes without activity, 3 hours in total,
  // or a login elsewhere.
  // This notices it (poll the status endpoint) and tells the server when the person is actually
  // using the page, which restarts the 10 minutes. Polling alone never extends the session.
  var POLL_MS = 20000;
  var EXTEND_MS = 10000; // at most one "still here" report per 10 s
  var WARN_AT = 30; // seconds left when the warning appears
  var warned = false;
  var lastExtend = 0;

  function leave(reason) {
    window.location.href = "/auth/login/?reason=" + reason;
  }

  function onStatus(data) {
    if (!data) return;
    if (data.expires_in > WARN_AT) {
      warned = false;
      return;
    }
    // The inactivity popup in base.html already covers the idle case; this covers the
    // hard time limit, when moving the mouse can no longer help.
    var idlePopup = document.getElementById("inactivity-modal");
    var idlePopupShowing = idlePopup && !idlePopup.classList.contains("hidden");
    if (!warned && !idlePopupShowing && window.IPT && window.IPT.toast) {
      warned = true;
      window.IPT.toast("warning", "Session ending soon", "For your security you will need to sign in again in " + data.expires_in + " seconds.");
    }
  }

  function check() {
    fetch("/auth/session-status/", { credentials: "same-origin", cache: "no-store" })
      .then(function (r) {
        if (r.status === 401) { leave("expired"); return null; }
        return r.json();
      })
      .then(onStatus)
      .catch(function () { /* offline: the server still enforces expiry */ });
  }

  function extend() {
    var now = Date.now();
    if (document.hidden || now - lastExtend < EXTEND_MS) return;
    lastExtend = now;
    fetch("/auth/session-extend/", {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      headers: { "X-CSRFToken": (window.IPT && window.IPT.csrf) || "" },
    })
      .then(function (r) {
        if (r.status === 401) { leave("expired"); return null; }
        return r.json();
      })
      .then(onStatus)
      .catch(function () { lastExtend = 0; /* try again on the next movement */ });
  }

  ["mousemove", "mousedown", "keydown", "touchstart", "scroll", "wheel"].forEach(function (evt) {
    document.addEventListener(evt, extend, { passive: true });
  });

  setInterval(check, POLL_MS);
  document.addEventListener("visibilitychange", function () { if (!document.hidden) check(); });
})();

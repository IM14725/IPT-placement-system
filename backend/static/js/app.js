window.IPT = window.IPT || {};

(function () {
  "use strict";

  function toast(kind, title, body) {
    const el = document.createElement("div");
    const colors = {
      success: "bg-[#e8f5e9] text-[#1b5e20]",
      error: "bg-error-container text-on-error-container",
      info: "bg-primary-fixed text-on-primary-fixed",
    };
    el.className =
      "rounded-xl shadow px-4 py-3 flex items-center gap-xs " + (colors[kind] || colors.info);
    el.innerHTML =
      "<span class='material-symbols-outlined text-[18px] shrink-0'>" +
      (kind === "success" ? "check_circle" : kind === "error" ? "error" : "info") +
      "</span>" +
      "<div><p class='font-semibold text-sm'>" + title + "</p>" +
      (body ? "<p class='text-xs mt-1'>" + body + "</p>" : "") +
      "</div>";
    document.getElementById("toast-container").appendChild(el);
    setTimeout(function () { el.remove(); }, 6000);
  }

  function postForm(url, data, opts) {
    opts = opts || {};
    return fetch(url, {
      method: "POST",
      headers: {
        "X-CSRFToken": window.IPT.csrf,
        ...(opts.json ? { "Content-Type": "application/json" } : {}),
      },
      body: opts.json ? JSON.stringify(data) : data,
      credentials: "same-origin",
    }).then(function (r) {
      if (opts.redirectOnOk && r.ok && r.redirected) return r.url;
      if (!r.ok) return r.json().catch(function () { return {}; }).then(function (j) {
        throw Object.assign(new Error(j.detail || "Request failed"), { status: r.status, json: j });
      });
      return r.json().catch(function () { return {}; });
    });
  }

  window.IPT.toast = toast;
  window.IPT.postForm = postForm;
})();
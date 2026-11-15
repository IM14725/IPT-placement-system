window.IPT = window.IPT || {};

(function () {
  "use strict";

  // Toast text comes from notifications (slot titles, organization names...): always escape it.
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function toast(kind, title, body) {
    title = esc(title);
    body = esc(body);
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

  // Shimmer placeholder cards shown while a list loads (styles are inline in base.html).
  window.IPT.skeletonCards = function (n) {
    var card = '<div class="ipt-sk-card" aria-hidden="true"><span class="ipt-sk" style="height:12px;width:40%"></span>' +
      '<span class="ipt-sk" style="height:20px;width:75%"></span><span class="ipt-sk" style="height:12px"></span>' +
      '<span class="ipt-sk" style="height:12px;width:60%"></span><span class="ipt-sk" style="height:36px;margin-top:6px"></span></div>';
    return new Array((n || 3) + 1).join(card);
  };
})();
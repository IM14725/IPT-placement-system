(function () {
  "use strict";

  var MIN_VISIBLE_MS = 450;

  /* ---- overlay (styles live in base.html <style>, not Tailwind) ---- */
  var overlay = null;

  function ensureOverlay() {
    if (overlay) return overlay;
    overlay = document.createElement("div");
    overlay.id = "ipt-form-loading";
    overlay.innerHTML =
      '<div class="ipt-loading-box">' +
      '<div class="ipt-spinner" aria-hidden="true"></div>' +
      '<span class="ipt-loading-text">Please wait&hellip;</span>' +
      "</div>";
    document.body.appendChild(overlay);
    return overlay;
  }

  function showOverlay() {
    ensureOverlay().style.display = "flex";
  }

  function hideOverlay() {
    var el = ensureOverlay();
    // Enforce a minimum visible duration so the user always sees the spinner,
    // even when the server responds within a few milliseconds.
    setTimeout(function () {
      el.style.display = "none";
    }, MIN_VISIBLE_MS);
  }

  /* ---- form handling ---- */

  function parseForm(html) {
    var tpl = document.createElement("template");
    tpl.innerHTML = html.trim();
    return (
      tpl.content.querySelector("form[data-loading-form]") ||
      tpl.content.querySelector("form")
    );
  }

  function containsForm(html) {
    var tpl = document.createElement("template");
    tpl.innerHTML = html.trim();
    return !!tpl.content.querySelector("form[data-loading-form], form");
  }

  function install(form) {
    if (form.hasAttribute("data-loading-installed")) return;
    form.setAttribute("data-loading-installed", "1");

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      if (form.hasAttribute("data-loading-submitting")) return;
      form.setAttribute("data-loading-submitting", "1");
      showOverlay();

      var action = new URL(
        form.getAttribute("action") || window.location.href,
        window.location.href
      ).href;

      fetch(action, {
        method: "POST",
        body: new FormData(form),
        headers: { "X-Requested-With": "XMLHttpRequest" },
      })
        .then(function (resp) {
          // Follows 3xx redirects automatically (success → final page).
          return resp.text().then(function (html) {
            if (resp.status >= 400) {
              // Rate-limited (429) or error: server re-rendered the page with the
              // form + errors. Swap the fresh <form> in place and dismiss the
              // spinner after the minimum visible time.
              var newForm = parseForm(html);
              if (newForm) {
                form.parentNode.replaceChild(newForm, form);
                install(newForm);
              }
              hideOverlay();
              return;
            }

            if (containsForm(html)) {
              // 200 with a form = validation error re-render.
              var errorForm = parseForm(html);
              if (errorForm) {
                form.parentNode.replaceChild(errorForm, form);
                install(errorForm);
              }
              hideOverlay();
              return;
            }

            // 200 without a form = the success page (e.g. dashboard after a
            // followed redirect). Navigate to its final URL to display it.
            window.location.href = resp.url;
          });
        })
        .catch(function () {
          hideOverlay();
        });
    });
  }

  function init() {
    document.querySelectorAll("form[data-loading-form]").forEach(install);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
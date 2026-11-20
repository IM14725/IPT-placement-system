(function () {
  "use strict";

  var box = document.querySelector("[data-voucher-box]");
  if (!box) return;

  var input = box.querySelector("[data-voucher-input]");
  var button = box.querySelector("[data-voucher-check]");
  var result = box.querySelector("[data-voucher-result]");
  if (!input || !button || !result) return;

  function csrfToken() {
    var form = box.closest("form");
    var field = form && form.querySelector("input[name=csrfmiddlewaretoken]");
    return field ? field.value : "";
  }

  function show(text, ok) {
    result.textContent = text;
    result.className = "font-body-md text-body-md " + (ok ? "text-primary font-semibold" : "text-error");
  }

  function check() {
    var code = input.value.trim();
    if (!code) {
      show("", true);
      return;
    }
    button.disabled = true;
    var body = new URLSearchParams();
    body.set("code", code);
    body.set("slot_id", box.getAttribute("data-slot-id"));
    fetch(box.getAttribute("data-check-url"), {
      method: "POST",
      headers: { "X-CSRFToken": csrfToken(), "Content-Type": "application/x-www-form-urlencoded" },
      credentials: "same-origin",
      body: body.toString(),
    })
      .then(function (r) {
        if (r.status === 429) throw new Error("rate");
        return r.json();
      })
      .then(function (data) {
        if (!data.valid) {
          show(data.message || box.getAttribute("data-msg-invalid"), false);
        } else {
          show(data.summary, true);
        }
      })
      .catch(function (e) {
        show(box.getAttribute(e && e.message === "rate" ? "data-msg-rate" : "data-msg-error"), false);
      })
      .then(function () {
        button.disabled = false;
      });
  }

  button.addEventListener("click", check);
  // Enter in the code box checks the code instead of submitting the whole application.
  input.addEventListener("keydown", function (event) {
    if (event.key === "Enter") {
      event.preventDefault();
      check();
    }
  });
})();

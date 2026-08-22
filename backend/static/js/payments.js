(function () {
  "use strict";

  var btn = document.getElementById("pay-btn");
  var status = document.getElementById("pay-status");
  if (!btn) return;

  btn.addEventListener("click", function () {
    var method = document.getElementById("pay-method").value;
    var phone = document.getElementById("pay-phone").value.trim();
    var ref = window.IPT.paymentRef;
    var amount = window.IPT.paymentAmount;

    if (!phone) {
      window.IPT.toast("error", "Phone required", "Enter your mobile money number.");
      return;
    }

    btn.disabled = true;
    btn.textContent = "Processing...";
    status.textContent = "Contacting gateway...";

    fetch(window.IPT.realtimeUrl + "/api/v1/payments/mock/initiate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reference_id: ref, amount: amount, method: method }),
    })
      .then(function (r) { return r.json(); })
      .then(function (init) {
        status.textContent = "Confirming payment...";
        return fetch(window.IPT.realtimeUrl + "/api/v1/payments/mock/callback", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Gateway-Signature": init.signature,
          },
          body: JSON.stringify({
            reference_id: ref,
            status: "PAID",
            gateway_txn_id: init.gateway_txn_id,
            amount: amount,
          }),
        });
      })
      .then(function (r) { return r.json(); })
      .then(function (result) {
        if (result.status !== "ok") throw new Error(result.detail || "Payment failed");
        window.IPT.toast("success", "Payment successful", "Your application has been submitted.");
        setTimeout(function () { window.location.href = "/student/applications/"; }, 1200);
      })
      .catch(function (e) {
        btn.disabled = false;
        btn.textContent = "Pay Now";
        status.textContent = "";
        window.IPT.toast("error", "Payment failed", e.message || "Could not complete payment.");
      });
  });
})();
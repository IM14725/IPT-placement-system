(function () {
  "use strict";

  var btn = document.getElementById("pay-btn");
  var status = document.getElementById("pay-status");
  var methodSelect = document.getElementById("pay-method");
  var phoneGroup = document.getElementById("phone-group");
  var phoneInput = document.getElementById("pay-phone");

  if (!btn) return;

  function getCookie(name) {
    let cookieValue = null;
    if (document.cookie && document.cookie !== '') {
        const cookies = document.cookie.split(';');
        for (let i = 0; i < cookies.length; i++) {
            const cookie = cookies[i].trim();
            if (cookie.substring(0, name.length + 1) === (name + '=')) {
                cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                break;
            }
        }
    }
    return cookieValue;
  }
  const csrftoken = getCookie('csrftoken');

  btn.addEventListener("click", function () {
    var method = methodSelect.value;
    var phone = phoneInput.value.trim();
    var appId = window.IPT.appId;
    var txUuid = window.IPT.paymentRef; // from template

    if (!phone) {
      window.IPT.toast("error", "Phone required", "Enter your mobile money number.");
      return;
    }

    btn.disabled = true;
    btn.textContent = "Processing...";
    status.textContent = "🚀 Requesting payment prompt... Please look at your phone handset.";

    var payload = {
        application_id: appId,
        network: method,
        phone_number: phone
    };

    fetch("/api/payments/initiate/", {
      method: "POST",
      headers: { 
        "Content-Type": "application/json",
        "X-CSRFToken": csrftoken
      },
      body: JSON.stringify(payload),
    })
      .then(function (r) { 
        if (!r.ok) {
           return r.json().then(err => { throw new Error(err.error || err.phone_number || "Payment failed") });
        }
        return r.json(); 
      })
      .then(function (init) {
        status.textContent = "📲 Selcom Prompt Sent! Check your phone screen now and enter your Mobile Money PIN.";
        startPolling(init.transaction_id || txUuid);
      })
      .catch(function (e) {
        btn.disabled = false;
        btn.textContent = "Pay Now";
        status.textContent = "❌ Error: " + (e.message || "Could not complete payment.");
        window.IPT.toast("error", "Payment failed", e.message || "Could not complete payment.");
      });
  });

  function startPolling(transactionId) {
    const maxAttempts = 20; // 20 attempts * 3 seconds = 60 second timeout
    let attempts = 0;

    function checkStatus() {
      attempts++;
      if (attempts > maxAttempts) {
        status.textContent = "⏰ Payment timed out. Please check your network connection and try again.";
        btn.disabled = false;
        return;
      }

      fetch(`/api/payments/status/${transactionId}/`, {
        method: 'GET',
        headers: {
          'X-CSRFToken': csrftoken
        }
      })
      .then(res => res.json())
      .then(data => {
        if (data.status === 'PAID' || data.status === 'SUCCESS') {
            status.innerHTML = "🎉 <strong>Payment Completed Successfully!</strong>";
            window.IPT.toast("success", "Payment successful", "Your application has been submitted.");
            setTimeout(function () { window.location.href = "/student/applications/"; }, 1200);
            return;
        }
        if (data.status === 'FAILED') {
            status.textContent = "❌ Payment failed or was canceled by user.";
            btn.disabled = false;
            return;
        }
        // Still pending
        setTimeout(checkStatus, 3000);
      })
      .catch(err => {
        console.error("Polling error:", err);
        setTimeout(checkStatus, 3000); // retry anyway
      });
    }

    setTimeout(checkStatus, 3000);
  }
})();
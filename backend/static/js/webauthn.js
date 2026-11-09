(function () {
  "use strict";
  // FIDO2 / WebAuthn helpers. Only public data crosses the wire; nothing is kept in
  // localStorage or sessionStorage.

  function b64uToBuf(s) {
    s = s.replace(/-/g, "+").replace(/_/g, "/");
    while (s.length % 4) s += "=";
    var bin = atob(s), out = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out.buffer;
  }
  function bufToB64u(buf) {
    var bytes = new Uint8Array(buf), s = "";
    for (var i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
    return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }
  function csrf() {
    var el = document.querySelector("[name=csrfmiddlewaretoken]");
    if (el) return el.value;
    var m = document.cookie.match(/csrftoken=([^;]+)/);
    return m ? m[1] : "";
  }
  function post(url, body) {
    return fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
      body: JSON.stringify(body || {}),
    }).then(function (r) {
      return r.json().then(function (j) { return { ok: r.ok, json: j }; });
    });
  }
  function supported() {
    return !!(window.PublicKeyCredential && navigator.credentials);
  }

  function register(name) {
    return post("/auth/mfa/webauthn/register-options/").then(function (res) {
      if (!res.ok) throw new Error(res.json.error || "Could not start registration.");
      var o = res.json;
      o.challenge = b64uToBuf(o.challenge);
      o.user.id = b64uToBuf(o.user.id);
      (o.excludeCredentials || []).forEach(function (c) { c.id = b64uToBuf(c.id); });
      return navigator.credentials.create({ publicKey: o });
    }).then(function (cred) {
      var payload = {
        id: cred.id,
        rawId: bufToB64u(cred.rawId),
        type: cred.type,
        response: {
          clientDataJSON: bufToB64u(cred.response.clientDataJSON),
          attestationObject: bufToB64u(cred.response.attestationObject),
          transports: cred.response.getTransports ? cred.response.getTransports() : [],
        },
        clientExtensionResults: cred.getClientExtensionResults ? cred.getClientExtensionResults() : {},
      };
      return post("/auth/mfa/webauthn/register-verify/", { credential: payload, name: name });
    }).then(function (res) {
      if (!res.ok) throw new Error(res.json.error || "Registration failed.");
      return res.json;
    });
  }

  function authenticate(nextUrl) {
    return post("/auth/mfa/webauthn/auth-options/").then(function (res) {
      if (!res.ok) throw new Error(res.json.error || "Could not start verification.");
      var o = res.json;
      o.challenge = b64uToBuf(o.challenge);
      (o.allowCredentials || []).forEach(function (c) { c.id = b64uToBuf(c.id); });
      return navigator.credentials.get({ publicKey: o });
    }).then(function (cred) {
      var r = cred.response;
      var payload = {
        id: cred.id,
        rawId: bufToB64u(cred.rawId),
        type: cred.type,
        response: {
          authenticatorData: bufToB64u(r.authenticatorData),
          clientDataJSON: bufToB64u(r.clientDataJSON),
          signature: bufToB64u(r.signature),
          userHandle: r.userHandle ? bufToB64u(r.userHandle) : null,
        },
        clientExtensionResults: cred.getClientExtensionResults ? cred.getClientExtensionResults() : {},
      };
      var url = "/auth/mfa/webauthn/auth-verify/" + (nextUrl ? "?next=" + encodeURIComponent(nextUrl) : "");
      return post(url, payload);
    }).then(function (res) {
      if (!res.ok) throw new Error(res.json.error || "Verification failed.");
      return res.json;
    });
  }

  window.IPTWebAuthn = { supported: supported, register: register, authenticate: authenticate };
})();

window.IPT = window.IPT || {};

(function () {
  "use strict";

  var IMAGE_RE = /^image\/(png|jpe?g|gif|webp|bmp)$/i;
  var PDF_RE = /\.pdf$/i;

  function fmt(bytes) {
    if (!bytes) return "";
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
    return (bytes / 1024 / 1024).toFixed(2) + " MB";
  }

  function buildPreview() {
    var wrap = document.createElement("div");
    wrap.className = "ipt-media-preview hidden";
    wrap.innerHTML =
      '<div class="ipt-media-thumb">' +
        '<img alt="Preview" class="ipt-media-img hidden">' +
        '<span class="ipt-media-icon material-symbols-outlined hidden"></span>' +
      "</div>" +
      '<div class="ipt-media-meta">' +
        '<span class="ipt-media-name"></span>' +
        '<div class="ipt-media-sub">' +
          '<span class="ipt-media-size"></span>' +
          '<span class="ipt-media-badge">' +
            '<span class="material-symbols-outlined">check_circle</span>Ready to submit' +
          "</span>" +
        "</div>" +
      "</div>" +
      '<button type="button" class="ipt-media-remove" title="Remove file">' +
        '<span class="material-symbols-outlined">delete</span>' +
      "</button>";
    return wrap;
  }

  function init(input) {
    if (input.dataset.iptPreview) return;
    input.dataset.iptPreview = "1";

    var box = input.closest(".ipt-field-box");
    var preview = buildPreview();
    if (box) box.insertAdjacentElement("afterend", preview);

    var img = preview.querySelector(".ipt-media-img");
    var icon = preview.querySelector(".ipt-media-icon");
    var objectUrl = null;

    function clear() {
      if (objectUrl) {
        URL.revokeObjectURL(objectUrl);
        objectUrl = null;
      }
      img.classList.add("hidden");
      icon.classList.add("hidden");
      preview.classList.add("hidden");
      input.value = "";
    }

    preview.querySelector(".ipt-media-remove").addEventListener("click", function () {
      clear();
      input.focus();
    });

    input.addEventListener("change", function () {
      var file = input.files && input.files[0];
      if (!file) {
        clear();
        return;
      }
      preview.querySelector(".ipt-media-name").textContent = file.name;
      preview.querySelector(".ipt-media-size").textContent = fmt(file.size);
      if (IMAGE_RE.test(file.type)) {
        icon.classList.add("hidden");
        if (objectUrl) URL.revokeObjectURL(objectUrl);
        objectUrl = URL.createObjectURL(file);
        img.src = objectUrl;
        img.classList.remove("hidden");
      } else {
        img.classList.add("hidden");
        icon.textContent = PDF_RE.test(file.name) ? "picture_as_pdf" : "description";
        icon.classList.toggle("pdf", PDF_RE.test(file.name));
        icon.classList.remove("hidden");
      }
      preview.classList.remove("hidden");
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("input[type='file']").forEach(init);
  });
})();
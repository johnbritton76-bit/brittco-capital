(function () {
  var docId = null;
  document.querySelectorAll(".drag-doc").forEach(function (row) {
    row.addEventListener("dragstart", function (e) {
      docId = row.getAttribute("data-doc");
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", docId);
    });
  });
  document.querySelectorAll(".drop-file").forEach(function (box) {
    box.addEventListener("dragover", function (e) {
      e.preventDefault();
      e.dataTransfer.dropEffect = e.dataTransfer.files && e.dataTransfer.files.length ? "copy" : "move";
      box.style.background = "#eef6fd";
    });
    box.addEventListener("dragleave", function () {
      box.style.background = "";
    });
    box.addEventListener("drop", function (e) {
      e.preventDefault();
      e.stopPropagation();
      box.style.background = "";
      var files = e.dataTransfer.files;
      var upload = box.getAttribute("data-upload");
      if (files && files.length && upload) {
        var fd = new FormData();
        for (var i = 0; i < files.length; i++) fd.append("docs", files[i]);
        fetch(upload, { method: "POST", body: fd, headers: { "X-Requested-With": "fetch" } }).then(function () {
          window.location.reload();
        });
        return;
      }
      var id = e.dataTransfer.getData("text/plain") || docId;
      var add = box.getAttribute("data-add");
      if (!id || !add) return;
      var body = new URLSearchParams();
      body.set("document_id", id);
      fetch(add, {
        method: "POST",
        headers: {
          "X-Requested-With": "fetch",
          "Content-Type": "application/x-www-form-urlencoded"
        },
        body: body.toString()
      }).then(function () { window.location.reload(); });
    });
  });
})();

// Submit-page logic: POST the URL, poll status, render the result.
// Also wires up "Copy" buttons used on both the submit and transcript pages.

(function () {
  "use strict";

  function copyText(text, btn) {
    var done = function () {
      var original = btn.textContent;
      btn.textContent = "Copied!";
      setTimeout(function () { btn.textContent = original; }, 1500);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done).catch(function () {
        fallbackCopy(text); done();
      });
    } else {
      fallbackCopy(text); done();
    }
  }

  function fallbackCopy(text) {
    var ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand("copy"); } catch (e) { /* noop */ }
    document.body.removeChild(ta);
  }

  // Delegated copy handler for any [data-target] (input value) or
  // [data-copy-text] (element textContent) button.
  document.addEventListener("click", function (e) {
    var btn = e.target.closest(".copy-btn");
    if (!btn) return;
    if (btn.dataset.target) {
      var el = document.getElementById(btn.dataset.target);
      if (el) copyText(el.value, btn);
    } else if (btn.dataset.copyText) {
      var src = document.getElementById(btn.dataset.copyText);
      if (src) copyText(src.innerText, btn);
    }
  });

  var form = document.getElementById("transcribe-form");
  if (!form) return; // not on the submit page

  var statusCard = document.getElementById("status");
  var workingBox = document.getElementById("working");
  var resultBox = document.getElementById("result");
  var errorBox = document.getElementById("error");
  var statusDetail = document.getElementById("status-detail");
  var submitBtn = document.getElementById("submit-btn");

  function show(el) { el.classList.remove("hidden"); }
  function hide(el) { el.classList.add("hidden"); }

  var pollTimer = null;

  function poll(id) {
    fetch("/api/status/" + encodeURIComponent(id))
      .then(function (r) {
        if (r.status === 410) throw new Error("This link has expired.");
        return r.json();
      })
      .then(function (data) {
        if (data.status === "done") {
          clearInterval(pollTimer);
          renderDone(id, data);
        } else if (data.status === "error") {
          clearInterval(pollTimer);
          renderError(data.error || "Transcription failed.");
        } else {
          statusDetail.textContent =
            data.status === "processing"
              ? "Transcribing audio with Whisper…"
              : "Queued…";
        }
      })
      .catch(function (err) {
        clearInterval(pollTimer);
        renderError(err.message || "Network error.");
      });
  }

  function renderDone(id, data) {
    hide(workingBox);
    show(resultBox);
    document.getElementById("share-url").value = data.share_url;
    document.getElementById("raw-url").value = data.raw_url;
    // Pull the transcript text from the raw endpoint for inline display.
    fetch(data.raw_url)
      .then(function (r) { return r.text(); })
      .then(function (txt) {
        document.getElementById("transcript-text").textContent = txt;
      })
      .catch(function () { /* link still works; inline preview optional */ });
  }

  function renderError(message) {
    hide(workingBox);
    document.getElementById("error-message").textContent = message;
    show(errorBox);
    submitBtn.disabled = false;
    submitBtn.textContent = "Transcribe";
  }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    submitBtn.disabled = true;
    submitBtn.textContent = "Submitting…";

    show(statusCard);
    show(workingBox);
    hide(resultBox);
    hide(errorBox);
    statusDetail.textContent = "Queued…";

    var payload = {
      url: document.getElementById("url").value.trim(),
      ttl_hours: parseInt(document.getElementById("ttl").value, 10),
      language: document.getElementById("language").value.trim() || null,
    };

    fetch("/api/transcribe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    })
      .then(function (r) {
        return r.json().then(function (data) {
          if (!r.ok) throw new Error(data.error || "Request failed.");
          return data;
        });
      })
      .then(function (data) {
        pollTimer = setInterval(function () { poll(data.id); }, 3000);
        poll(data.id);
      })
      .catch(function (err) {
        renderError(err.message || "Request failed.");
      });
  });
})();

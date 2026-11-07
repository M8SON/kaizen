// Kaizen control panel: Test buttons, slider readouts, wake scores, status polling.
(function () {
  "use strict";
  var csrf = document.querySelector('meta[name="csrf"]').content;

  function post(url, form) {
    var body = new FormData(form);
    return fetch(url, { method: "POST", body: body, headers: { "X-CSRF-Token": csrf },
                        credentials: "same-origin" })
      .then(function (r) { return r.json(); });
  }

  document.querySelectorAll("[data-test]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var form = btn.closest("form");
      var out = btn.parentElement.querySelector(".result");
      btn.disabled = true;
      out.className = "result";
      out.textContent = "Testing…";
      post("/api/test/" + btn.dataset.test, form).then(function (res) {
        out.className = "result " + (res.ok ? "good" : "bad");
        out.textContent = (res.ok ? "✓ " : "✗ ") + res.message;
        var anyway = form.querySelector(".anyway");
        if (anyway) anyway.hidden = res.ok;
      }).catch(function () {
        out.className = "result bad";
        out.textContent = "✗ Couldn't reach the panel";
      }).finally(function () { btn.disabled = false; });
    });
  });

  document.querySelectorAll("input[data-show]").forEach(function (input) {
    var out = input.parentElement.querySelector("output");
    input.addEventListener("input", function () { out.textContent = input.value; });
  });

  var wake = document.querySelector("[data-wake-scores]");
  if (wake) {
    wake.addEventListener("toggle", function () {
      if (!wake.open || wake.dataset.loaded) return;
      wake.dataset.loaded = "1";
      fetch("/api/wake-scores", { credentials: "same-origin" }).then(function (r) { return r.json(); })
        .then(function (res) {
          var tbody = wake.querySelector("tbody");
          tbody.textContent = "";
          if (!res.events.length) {
            tbody.innerHTML = "<tr><td>No wake attempts in the log yet.</td></tr>";
            return;
          }
          res.events.forEach(function (e) {
            var tr = document.createElement("tr");
            [e.time, e.score.toFixed(2), e.outcome].forEach(function (v, i) {
              var td = document.createElement("td");
              td.textContent = v;
              if (i === 1) td.className = "num";
              tr.appendChild(td);
            });
            tbody.appendChild(tr);
          });
        });
    });
  }

  var poll = document.querySelector("[data-status-poll]");
  if (poll) {
    setInterval(function () {
      if (document.hidden) return;
      fetch("/api/status", { credentials: "same-origin" }).then(function (r) { return r.json(); })
        .then(function (s) {
          function set(key, ok, text) {
            var el = poll.querySelector('[data-k="' + key + '"]');
            if (!el) return;
            el.textContent = text;
            if (ok !== null) el.className = "state " + (ok ? "good" : "bad");
          }
          var j = s.jarvis;
          set("jarvis", j.active === "active", j.active === "active"
              ? "Running" + (j.state === "idle" ? ', waiting for "Hey Jarvis"' : j.state === "busy" ? ", busy" : "")
              : j.active.charAt(0).toUpperCase() + j.active.slice(1));
          set("since", null, "Since " + (j.since || "unknown") +
              (j.restarts ? " · restarted " + j.restarts + " times by systemd" : ""));
          set("speaker", s.speaker.ok, s.speaker.dummy
              ? "Dummy Output: Jarvis can't be heard. Restart to fix." : s.speaker.name);
          set("microphone", s.microphone.ok, s.microphone.name);
          var n = s.network;
          set("network", true, n.iface + (n.ssid ? " · " + n.ssid : "") + (n.ip ? " · " + n.ip : ""));
        }).catch(function () {});
    }, 10000);
  }
})();

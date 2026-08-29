// Dialogs. One document-action dialog is reused across the dashboard, the
// fleet matrix and the vehicle page: a click on any cell fills it from that
// cell's data-* attributes and points the three forms at the right endpoints.
// The vehicle page adds a second, delete-confirmation dialog.
(function () {
  "use strict";

  var dialog = document.getElementById("doc-dialog");
  var deleteDialog = document.getElementById("delete-dialog");
  if (!dialog && !deleteDialog) return;

  var lastTrigger = null;

  function show(el) {
    if (!el) return;
    el.hidden = false;
    var first = el.querySelector("input:not([type=hidden])");
    if (first) first.focus();
  }

  function closeAll() {
    [dialog, deleteDialog].forEach(function (el) { if (el) el.hidden = true; });
    if (lastTrigger) { lastTrigger.focus(); lastTrigger = null; }
  }

  function anyOpen() {
    return [dialog, deleteDialog].some(function (el) { return el && !el.hidden; });
  }

  // ── the document-action dialog ─────────────────────────────────────────

  if (dialog) {
    var slots = {
      label: dialog.querySelector('[data-slot="label"]'),
      reg: dialog.querySelector('[data-slot="reg"]'),
      state: dialog.querySelector('[data-slot="state"]'),
      ladder: dialog.querySelector('[data-slot="ladder"]')
    };
    var forms = {
      renew: dialog.querySelector('[data-form="renew"]'),
      snooze: dialog.querySelector('[data-form="snooze"]'),
      unsnooze: dialog.querySelector('[data-form="unsnooze"]')
    };
    var dateInput = dialog.querySelector('input[name="new_date"]');
    var reasonInput = dialog.querySelector('input[name="reason"]');

    var STATE_TEXT = {
      na: function (d) { return "No " + d.label.toLowerCase() + " date recorded."; },
      ok: function (d) { return "Expires " + d.dateText + " · " + d.daysText + " away."; },
      soon: function (d) { return "Expires " + d.dateText + " · " + d.daysText + " away."; },
      overdue: function (d) { return "Expired " + d.dateText + " · " + d.daysText + "."; },
      snoozed: function (d) {
        var text = d.snoozeUntil
          ? "Snoozed until " + d.snoozeUntil
          : "Reminders ignored indefinitely";
        if (d.snoozeReason) text += " — “" + d.snoozeReason + "”";
        if (d.snoozeBy) text += " · set by " + d.snoozeBy;
        return text + ".";
      }
    };

    var ladderText = function (d) {
      if (d.status === "na") return "";
      var line = d.sent + " of " + d.total + " reminders sent";
      if (d.status === "snoozed") return line + " · the sweep skips this document while snoozed.";
      if (!d.nextOffset && d.nextOffset !== "0") return line + " · the schedule is exhausted.";
      var n = Number(d.nextOffset);
      return line + " · next at " + (n < 0 ? "−" : "+") + Math.abs(n) + "d, " + d.nextDate + ".";
    };

    var openDoc = function (button) {
      var d = button.dataset;
      lastTrigger = button;

      slots.label.textContent = d.label + " · " + d.nickname;
      slots.reg.textContent = d.reg;
      slots.state.textContent = (STATE_TEXT[d.status] || STATE_TEXT.ok)(d);
      slots.ladder.textContent = ladderText(d);

      // Renew is keyed on the registration number; snooze on the vehicle id —
      // matching the db/client.py helpers each one calls.
      forms.renew.action = "/vehicles/" + encodeURIComponent(d.reg) + "/renew";
      forms.snooze.action = "/vehicles/" + encodeURIComponent(d.vehicleId) + "/snooze";
      forms.unsnooze.action = "/vehicles/" + encodeURIComponent(d.vehicleId) + "/unsnooze";

      Array.prototype.forEach.call(
        dialog.querySelectorAll('[data-slot="field"]'),
        function (input) { input.value = d.field; }
      );

      dateInput.value = d.date || "";
      reasonInput.value = d.snoozeReason || "";

      // A snoozed document offers Unsnooze instead of another Snooze;
      // renewing stays available either way.
      var snoozed = d.status === "snoozed";
      forms.snooze.hidden = snoozed;
      forms.unsnooze.hidden = !snoozed;

      show(dialog);
    };
  }

  // ── delegation ─────────────────────────────────────────────────────────

  document.addEventListener("click", function (event) {
    if (event.target.closest('[data-action="close"]')) { closeAll(); return; }

    var cell = dialog && event.target.closest("button.doc");
    if (cell) { openDoc(cell); return; }

    if (event.target.closest('[data-action="open-delete"]')) {
      lastTrigger = event.target.closest("button");
      show(deleteDialog);
      return;
    }

    // A click on a backdrop but outside its panel dismisses.
    [dialog, deleteDialog].forEach(function (el) {
      if (el && !el.hidden && event.target === el) closeAll();
    });
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && anyOpen()) closeAll();
  });
})();

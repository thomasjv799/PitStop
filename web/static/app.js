// Theme control and dialogs. Loaded on every page.
(function () {
  "use strict";

  // ── theme ──────────────────────────────────────────────────────────────
  // Three states: "system" (no attribute, CSS follows prefers-color-scheme),
  // "light" and "dark". base.html applies the stored choice before first
  // paint; this only handles switching and the button's pressed state.

  var KEY = "pitstop-theme";

  function stored() {
    try { return localStorage.getItem(KEY) || "system"; } catch (e) { return "system"; }
  }

  function applyTheme(choice) {
    if (choice === "light" || choice === "dark") {
      document.documentElement.dataset.theme = choice;
    } else {
      delete document.documentElement.dataset.theme;
    }
    try {
      if (choice === "system") localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, choice);
    } catch (e) { /* private mode — the choice just won't persist */ }
    markPressed(choice);
  }

  function markPressed(choice) {
    var buttons = document.querySelectorAll("[data-theme-set]");
    Array.prototype.forEach.call(buttons, function (b) {
      b.setAttribute("aria-pressed", String(b.dataset.themeSet === choice));
    });
  }

  markPressed(stored());

  document.addEventListener("click", function (event) {
    var toggle = event.target.closest("[data-theme-set]");
    if (toggle) applyTheme(toggle.dataset.themeSet);
  });

  // ── dialogs ────────────────────────────────────────────────────────────
  // One document-action dialog is reused across the dashboard, the fleet
  // matrix and the vehicle page: a click on any cell fills it from that
  // cell's data-* attributes and points the three forms at the right
  // endpoints. The vehicle page adds a delete-confirmation dialog.

  var dialog = document.getElementById("doc-dialog");
  var deleteDialog = document.getElementById("delete-dialog");
  if (!dialog && !deleteDialog) return;

  var lastTrigger = null;
  var openDoc = null;

  // A slot may appear more than once in a dialog (the registration is both
  // stated and asked for), so fill every match.
  function fill(root, slot, text) {
    var nodes = root.querySelectorAll('[data-slot="' + slot + '"]');
    Array.prototype.forEach.call(nodes, function (n) { n.textContent = text || ""; });
  }

  function show(el) {
    if (!el) return;
    el.hidden = false;
    var first = el.querySelector("input:not([type=hidden]):not([type=radio])");
    if (first) first.focus();
  }

  function closeAll() {
    [dialog, deleteDialog].forEach(function (el) { if (el) el.hidden = true; });
    if (lastTrigger) { lastTrigger.focus(); lastTrigger = null; }
  }

  function anyOpen() {
    return [dialog, deleteDialog].some(function (el) { return el && !el.hidden; });
  }

  if (dialog) {
    var slots = {
      label: dialog.querySelector('[data-slot="label"]'),
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
          : "Reminders paused indefinitely";
        if (d.snoozeReason) text += " — “" + d.snoozeReason + "”";
        if (d.snoozeBy) text += " · set by " + d.snoozeBy;
        return text + ".";
      }
    };

    function ladderText(d) {
      if (d.status === "na") return "";
      var line = d.sent + " of " + d.total + " reminders sent";
      if (d.status === "snoozed") return line + " · the sweep skips this document while paused.";
      if (!d.nextOffset && d.nextOffset !== "0") return line + " · the schedule is exhausted.";
      var n = Number(d.nextOffset);
      return line + " · next at " + (n < 0 ? "−" : "+") + Math.abs(n) + "d, " + d.nextDate + ".";
    }

    openDoc = function (button) {
      var d = button.dataset;
      lastTrigger = button;

      slots.label.textContent = d.label + " · " + d.nickname + " (" + d.reg + ")";
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

      // A paused document offers Resume instead of another Snooze; renewing
      // stays available either way.
      var snoozed = d.status === "snoozed";
      forms.snooze.hidden = snoozed;
      forms.unsnooze.hidden = !snoozed;

      show(dialog);
    };
  }

  document.addEventListener("click", function (event) {
    if (event.target.closest('[data-action="close"]')) { closeAll(); return; }

    var cell = openDoc && event.target.closest("[data-doc]");
    if (cell) { openDoc(cell); return; }

    var del = event.target.closest('[data-action="open-delete"]');
    if (del && deleteDialog) {
      lastTrigger = del;
      // The dialog is shared by the fleet list and the vehicle page, so the
      // trigger says which vehicle it is about.
      fill(deleteDialog, "del-name", del.dataset.nickname || del.dataset.reg);
      fill(deleteDialog, "del-reg", del.dataset.reg);
      deleteDialog.querySelector('[data-form="delete"]').action =
        "/vehicles/" + encodeURIComponent(del.dataset.reg) + "/delete";
      deleteDialog.querySelector('input[name="confirm"]').value = "";
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

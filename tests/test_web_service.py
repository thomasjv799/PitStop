"""The dashboard view model — pure functions, no database."""

from datetime import date, datetime, timedelta

import pytest

from web import service
from web.config import DOCUMENTS

TODAY = date(2026, 8, 30)


def veh(vid, nick, reg, **dates):
    row = {
        "id": vid,
        "nickname": nick,
        "registration_number": reg,
        "owner_name": "Fleet Ops",
        "insurance_valid_until": None,
        "pucc_valid_until": None,
        "fitness_valid_until": None,
        "mv_tax_valid_until": None,
        "permit_valid_until": None,
    }
    row.update(dates)
    return row


# ── formatting ───────────────────────────────────────────────────────────


def test_format_date_matches_the_matrix_form():
    assert service.format_date(date(2027, 1, 12)) == "12 Jan 27"
    assert service.format_date(None) == ""


def test_format_days_uses_a_real_minus_sign_when_overdue():
    assert service.format_days(5) == "5d"
    assert service.format_days(0) == "0d"
    assert service.format_days(-11) == "−11d"
    assert "-" not in service.format_days(-11)  # U+2212, not ASCII hyphen


# ── classification ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "days, expected",
    [(None, "na"), (-1, "overdue"), (0, "soon"), (30, "soon"), (31, "ok"), (670, "ok")],
)
def test_classify_boundaries(days, expected):
    assert service.classify(days, snoozed=False) == expected


def test_snooze_outranks_urgency():
    # A snoozed document was deliberately dismissed; re-raising it as
    # overdue would undo that.
    assert service.classify(-67, snoozed=True) == "snoozed"
    assert service.classify(5, snoozed=True) == "snoozed"


def test_classify_still_reports_na_for_a_snoozed_field_with_no_date():
    assert service.classify(None, snoozed=True) == "na"


# ── the reminder ladder ──────────────────────────────────────────────────


def test_next_offset_matches_the_cron_schedule():
    # 5 days out: −7 has fired, −3 is next.
    assert service.next_offset(5) == -3
    # 136 days out: nothing has fired, −7 is first.
    assert service.next_offset(136) == -7
    # 11 days overdue: −7…+7 have fired, +15 is next.
    assert service.next_offset(-11) == 15


def test_next_offset_is_exhausted_past_the_last_step():
    assert service.next_offset(-31) is None
    assert service.next_offset(None) is None


def test_next_offset_on_the_expiry_day_itself():
    # today == expiry: offset 0 fires today, so +1 is what is still ahead.
    assert service.next_offset(0) == 1


# ── cells ────────────────────────────────────────────────────────────────


def _cell(vehicle, field, snoozes=None, reminders=None):
    return service.build_cell(
        vehicle, field, "PUCC", "Pollution (PUCC)", TODAY,
        snoozes or {}, reminders or {},
    )


def test_cell_carries_the_chip_text_and_status():
    v = veh(1, "Swift", "KL04AS1371", pucc_valid_until=date(2026, 9, 4))
    cell = _cell(v, "pucc_valid_until")
    assert cell["status"] == "soon"
    assert cell["days"] == 5
    assert cell["days_text"] == "5d"
    assert cell["date_text"] == "04 Sep 26"


def test_cell_counts_only_reminders_against_the_current_expiry_date():
    expiry = date(2026, 9, 4)
    v = veh(1, "Swift", "KL04AS1371", pucc_valid_until=expiry)
    reminders = {
        (1, "pucc_valid_until", expiry): {"sent": 1},
        # A previous cycle, against the date this document used to carry.
        (1, "pucc_valid_until", date(2025, 9, 4)): {"sent": 9},
    }
    cell = _cell(v, "pucc_valid_until", reminders=reminders)
    assert cell["reminders_sent"] == 1
    assert cell["reminders_total"] == 9
    assert cell["next_offset"] == -3
    assert cell["next_offset_date"] == "01 Sep 26"


def test_cell_reports_a_snooze_with_its_reason_and_author():
    v = veh(5, "Ace", "KL08BR1104", mv_tax_valid_until=date(2026, 6, 24))
    snoozes = {
        (5, "mv_tax_valid_until"): {
            "snoozed_until": None,
            "reason": "vehicle being sold",
            "created_by": "discord:thomas",
        }
    }
    cell = service.build_cell(
        v, "mv_tax_valid_until", "MV Tax", "MV Tax", TODAY, snoozes, {}
    )
    assert cell["status"] == "snoozed"
    assert cell["snoozed_until"] is None  # indefinite
    assert cell["snooze_reason"] == "vehicle being sold"
    assert cell["snooze_by"] == "discord:thomas"


def test_cell_with_no_date_is_not_available():
    cell = _cell(veh(1, "Swift", "KL04AS1371"), "permit_valid_until")
    assert cell["status"] == "na"
    assert cell["days"] is None
    assert cell["date_text"] == ""


# ── rows and the fleet ───────────────────────────────────────────────────


def test_row_worst_ignores_snoozed_documents():
    v = veh(5, "Ace", "KL08BR1104",
            mv_tax_valid_until=date(2026, 6, 24),     # 67 days overdue
            insurance_valid_until=date(2026, 11, 18))  # 80 days out
    snoozes = {(5, "mv_tax_valid_until"): {"snoozed_until": None, "reason": "", "created_by": ""}}
    row = service.build_row(v, TODAY, snoozes, {})
    assert row["worst"] == "ok"
    assert row["soonest"] == 80


def test_row_falls_back_to_the_registration_when_unnamed():
    row = service.build_row(veh(1, None, "KL04AS1371"), TODAY, {}, {})
    assert row["nickname"] == "KL04AS1371"


def test_fleet_sorts_by_nearest_expiry_and_sinks_undated_vehicles():
    vehicles = [
        veh(1, "Far", "A", insurance_valid_until=TODAY + timedelta(days=300)),
        veh(2, "Undated", "B"),
        veh(3, "Overdue", "C", pucc_valid_until=TODAY - timedelta(days=11)),
        veh(4, "Soon", "D", insurance_valid_until=TODAY + timedelta(days=3)),
    ]
    rows = service.build_fleet(vehicles, TODAY)
    assert [r["nickname"] for r in rows] == ["Overdue", "Soon", "Far", "Undated"]


def test_summarise_counts_each_vehicle_once_at_its_worst():
    vehicles = [
        # Overdue *and* due soon — counted once, as overdue.
        veh(1, "Both", "A",
            pucc_valid_until=TODAY - timedelta(days=11),
            insurance_valid_until=TODAY + timedelta(days=3)),
        veh(2, "Soon", "B", insurance_valid_until=TODAY + timedelta(days=3)),
        veh(3, "Clear", "C", insurance_valid_until=TODAY + timedelta(days=300)),
    ]
    stats = service.summarise(service.build_fleet(vehicles, TODAY))
    assert stats == {"total": 3, "overdue": 1, "soon": 1, "attention": 2}


def test_filter_rows_narrows_to_what_needs_doing():
    vehicles = [
        veh(1, "Clear", "A", insurance_valid_until=TODAY + timedelta(days=300)),
        veh(2, "Soon", "B", insurance_valid_until=TODAY + timedelta(days=3)),
    ]
    rows = service.build_fleet(vehicles, TODAY)
    assert [r["nickname"] for r in service.filter_rows(rows, "attention")] == ["Soon"]
    assert len(service.filter_rows(rows, "all")) == 2


# ── the action queue ─────────────────────────────────────────────────────


def _fleet_with(**dates):
    return service.build_fleet([veh(1, "Swift", "KL04AS1371", **dates)], TODAY)


def test_queue_lists_each_document_separately():
    # Two lapsed documents on one vehicle are two things to deal with;
    # collapsing them to one row would hide one of them.
    rows = _fleet_with(
        pucc_valid_until=TODAY - timedelta(days=11),
        insurance_valid_until=TODAY + timedelta(days=3),
    )
    items = service.queue_items(rows)
    assert [i["field"] for i in items] == ["pucc_valid_until", "insurance_valid_until"]


def test_queue_omits_documents_that_are_fine():
    rows = _fleet_with(insurance_valid_until=TODAY + timedelta(days=300))
    assert service.queue_items(rows) == []


def test_queue_sinks_snoozed_items_below_live_ones():
    v = veh(1, "Ace", "KL08BR1104",
            mv_tax_valid_until=TODAY - timedelta(days=67),
            pucc_valid_until=TODAY + timedelta(days=5))
    snoozes = {(1, "mv_tax_valid_until"): {"snoozed_until": None, "reason": "", "created_by": ""}}
    rows = [service.build_row(v, TODAY, snoozes, {})]
    items = service.queue_items(rows)
    # The snoozed one is still listed — so it can be undone — but last.
    assert [i["status"] for i in items] == ["soon", "snoozed"]


@pytest.mark.parametrize("days, expected", [
    (-11, "Insurance expired 11 days ago"),
    (-1, "Insurance expired 1 day ago"),
    (0, "Insurance expires today"),
    (1, "Insurance due in 1 day"),
    (5, "Insurance due in 5 days"),
])
def test_queue_headline_wording(days, expected):
    rows = _fleet_with(insurance_valid_until=TODAY + timedelta(days=days))
    assert service.queue_items(rows)[0]["headline"] == expected


# ── the timeline ─────────────────────────────────────────────────────────


def test_timeline_position_places_today_at_the_rail_fraction():
    assert service.timeline_position(0) == pytest.approx(40.0)
    assert service.timeline_position(-60) == pytest.approx(0.0)
    assert service.timeline_position(90) == pytest.approx(100.0)
    assert service.timeline_position(5) == pytest.approx(43.33, abs=0.01)


def test_timeline_position_clamps_outside_the_window():
    assert service.timeline_position(-400) == 0.0
    assert service.timeline_position(400) == 100.0


def test_timeline_keeps_stale_overdue_documents_pinned_left():
    rows = _fleet_with(mv_tax_valid_until=TODAY - timedelta(days=400))
    marks = service.timeline_rows(rows)[0]["marks"]
    assert [m["field"] for m in marks] == ["mv_tax_valid_until"]
    assert marks[0]["left"] == 0.0
    assert marks[0]["clamped"] is True


def test_timeline_drops_far_future_documents_and_empty_rows():
    rows = _fleet_with(insurance_valid_until=TODAY + timedelta(days=300))
    assert service.timeline_rows(rows) == []


def test_timeline_marks_are_ordered_left_to_right():
    rows = _fleet_with(
        insurance_valid_until=TODAY + timedelta(days=70),
        pucc_valid_until=TODAY - timedelta(days=11),
    )
    marks = service.timeline_rows(rows)[0]["marks"]
    assert [m["field"] for m in marks] == ["pucc_valid_until", "insurance_valid_until"]


# ── the vehicle detail page ──────────────────────────────────────────────


FULL = {
    "id": 1, "nickname": "Swift", "registration_number": "KL04AS1371",
    "owner_name": "Thomas V", "status": None, "vehicle_class": "LMV",
    "fuel_type": "Petrol", "permit_type": None,
    "registration_date": date(2019, 4, 2),
    "insurance_valid_until": date(2027, 1, 12),
    "pucc_valid_until": date(2026, 9, 4),
    "fitness_valid_until": None, "mv_tax_valid_until": None,
    "permit_valid_until": None,
}


def test_detail_lists_every_non_expiry_column():
    fields = {f["label"]: f["value"] for f in service.detail_fields(FULL)}
    assert fields["Registration"] == "KL04AS1371"
    assert fields["Class"] == "LMV"
    assert fields["Registered"] == "02 Apr 2019"
    # Empty columns are shown as empty rather than omitted.
    assert fields["Permit type"] == "—"
    assert fields["Status"] == "—"


def test_detail_ladder_lights_the_offsets_that_fired():
    log = [
        {"expiry_field": "pucc_valid_until", "expiry_date": date(2026, 9, 4),
         "trigger_offset": -7, "sent_at": None},
        # A previous cycle against the date this document used to carry.
        {"expiry_field": "pucc_valid_until", "expiry_date": date(2025, 9, 4),
         "trigger_offset": 30, "sent_at": None},
    ]
    detail = service.build_detail(FULL, TODAY, [], log)
    pucc = next(d for d in detail["documents"] if d["field"] == "pucc_valid_until")

    assert pucc["reminders_sent"] == 1        # the old cycle does not count
    assert len(pucc["ladder"]) == 9
    assert [s["offset"] for s in pucc["ladder"] if s["fired"]] == [-7]
    assert [s["offset"] for s in pucc["ladder"] if s["is_next"]] == [-3]


def test_detail_reports_the_archived_flag():
    assert service.build_detail(FULL, TODAY)["archived"] is False
    assert service.build_detail({**FULL, "status": "archived"}, TODAY)["archived"] is True


# ── accounts ─────────────────────────────────────────────────────────────


def test_build_users_reports_the_approval_state():
    now = datetime(2026, 8, 30, 9, 0)
    rows = [
        {"subject": "a", "email": "a@x.com", "name": "A", "role": "admin",
         "approved_at": now, "approved_by": "bootstrap",
         "created_at": now, "last_seen_at": now},
        {"subject": "b", "email": "b@x.com", "name": None, "role": "member",
         "approved_at": None, "approved_by": None,
         "created_at": now, "last_seen_at": now},
    ]
    users = service.build_users(rows)
    assert users[0]["approved"] is True and users[0]["state"] == "Approved"
    assert users[1]["approved"] is False and users[1]["state"] == "Awaiting approval"
    assert users[1]["name"] == "b@x.com"   # falls back to the address


# ── the add / edit form ──────────────────────────────────────────────────


@pytest.mark.parametrize("raw, expected", [
    ("KL04AS1371", "KL04AS1371"),
    ("kl 04-as 1371", "KL04AS1371"),
    ("  KL-04 AS-1371  ", "KL04AS1371"),
    ("kl04as1371", "KL04AS1371"),
])
def test_registration_is_normalised_to_one_canonical_form(raw, expected):
    # The bot, the cron sweep and the web app must agree on what a vehicle
    # is called, so spaces and hyphens are stripped on the way in.
    assert service.normalise_registration(raw) == expected


def test_normalise_registration_tolerates_nothing():
    assert service.normalise_registration("") == ""
    assert service.normalise_registration(None) == ""


def _form(**over):
    base = {"registration_number": "KL04AS1371"}
    base.update(over)
    return base


def test_validate_accepts_a_registration_and_nothing_else():
    result = service.validate_vehicle(_form())
    assert result["ok"] is True
    assert result["values"]["registration_number"] == "KL04AS1371"
    # Everything unfilled becomes a real NULL, not an empty string.
    assert result["values"]["nickname"] is None
    assert result["values"]["insurance_valid_until"] is None


def test_validate_requires_a_registration():
    assert "registration_number" in service.validate_vehicle(_form(registration_number=""))["errors"]
    assert "registration_number" in service.validate_vehicle(_form(registration_number="  -- "))["errors"]


def test_validate_rejects_a_registration_with_no_digits():
    errors = service.validate_vehicle(_form(registration_number="ABCDEF"))["errors"]
    assert "registration_number" in errors


def test_validate_rejects_an_over_long_registration():
    errors = service.validate_vehicle(_form(registration_number="KL04AS1371" * 3))["errors"]
    assert "registration_number" in errors


def test_validate_parses_dates_and_flags_bad_ones():
    ok = service.validate_vehicle(_form(insurance_valid_until="2027-01-12"))
    assert ok["values"]["insurance_valid_until"] == date(2027, 1, 12)

    bad = service.validate_vehicle(_form(pucc_valid_until="12/01/2027"))
    assert "pucc_valid_until" in bad["errors"]


def test_validate_treats_an_empty_date_as_no_date_not_an_error():
    result = service.validate_vehicle(_form(fitness_valid_until="", registration_date=""))
    assert result["ok"] is True
    assert result["values"]["fitness_valid_until"] is None


def test_validate_catches_an_expiry_before_the_vehicle_existed():
    # A document expiring before the vehicle was registered is a typo, not a
    # lapsed document.
    result = service.validate_vehicle(_form(
        registration_date="2019-04-02", insurance_valid_until="2018-01-01"))
    assert "insurance_valid_until" in result["errors"]
    assert "before the vehicle was registered" in result["errors"]["insurance_valid_until"]


def test_validate_allows_an_expiry_after_registration():
    result = service.validate_vehicle(_form(
        registration_date="2019-04-02", insurance_valid_until="2027-01-12"))
    assert result["ok"] is True


def test_validate_rejects_an_over_long_detail_field():
    assert "nickname" in service.validate_vehicle(_form(nickname="x" * 200))["errors"]


def test_form_from_vehicle_round_trips_into_the_edit_form():
    form = service.form_from_vehicle(FULL)
    assert form["registration_number"] == "KL04AS1371"
    assert form["vehicle_class"] == "LMV"
    assert form["insurance_valid_until"] == "2027-01-12"
    assert form["permit_valid_until"] == ""      # unset stays empty
    # What comes out of a vehicle must validate cleanly going back in.
    assert service.validate_vehicle(form)["ok"] is True


def test_blank_form_covers_every_input_the_template_renders():
    blank = service.blank_form()
    expected = {"registration_number", "registration_date"}
    expected |= set(service.DETAIL_INPUT_FIELDS)
    expected |= {f for f, _, _ in DOCUMENTS}
    assert set(blank) == expected
    assert all(v == "" for v in blank.values())


def test_rows_report_whether_they_are_archived():
    live = service.build_row(veh(1, "Swift", "KL04AS1371"), TODAY, {}, {})
    assert live["archived"] is False

    row = veh(1, "Swift", "KL04AS1371")
    row["status"] = "archived"
    assert service.build_row(row, TODAY, {}, {})["archived"] is True

    # The column is free text on an externally-created table, so an unrelated
    # value must not read as archived.
    row["status"] = "ACTIVE"
    assert service.build_row(row, TODAY, {}, {})["archived"] is False

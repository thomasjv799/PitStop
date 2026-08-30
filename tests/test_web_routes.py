"""Routes, the approval gate and the write actions, with the DB mocked."""

from datetime import date, datetime, timedelta
from unittest.mock import patch

import pytest

# The environment these need is set in tests/conftest.py, which pytest
# imports before any test module.
from fastapi.testclient import TestClient  # noqa: E402

from web.app import app  # noqa: E402

TODAY = date.today()


def _v(vid, nick, reg, owner, **dates):
    row = {
        "id": vid, "nickname": nick, "registration_number": reg,
        "owner_name": owner, "status": None, "vehicle_class": "LMV",
        "fuel_type": "Petrol", "permit_type": None,
        "registration_date": date(2019, 4, 2),
        "insurance_valid_until": None, "pucc_valid_until": None,
        "fitness_valid_until": None, "mv_tax_valid_until": None,
        "permit_valid_until": None,
    }
    row.update(dates)
    return row


VEHICLES = [
    _v(1, "Swift", "KL04AS1371", "Thomas V",
       insurance_valid_until=TODAY + timedelta(days=136),
       pucc_valid_until=TODAY + timedelta(days=5),
       fitness_valid_until=TODAY + timedelta(days=670),
       mv_tax_valid_until=TODAY + timedelta(days=213)),
    _v(2, "Innova", "KL07CH8842", "Priya Nair",
       insurance_valid_until=TODAY + timedelta(days=70),
       pucc_valid_until=TODAY - timedelta(days=11),
       fitness_valid_until=TODAY + timedelta(days=900),
       mv_tax_valid_until=TODAY + timedelta(days=213)),
    _v(5, "Ace", "KL08BR1104", "Fleet Ops",
       insurance_valid_until=TODAY + timedelta(days=80),
       pucc_valid_until=TODAY + timedelta(days=134),
       fitness_valid_until=TODAY + timedelta(days=600),
       mv_tax_valid_until=TODAY - timedelta(days=67)),
]

SNOOZES = [{
    "vehicle_id": 5, "expiry_field": "mv_tax_valid_until",
    "snoozed_until": None, "reason": "vehicle being sold",
    "created_by": "discord:thomas", "created_at": datetime.now(),
}]

REMINDERS = [{
    "vehicle_id": 1, "expiry_field": "pucc_valid_until",
    "expiry_date": TODAY + timedelta(days=5), "sent": 1,
    "last_sent": datetime.now(),
}]

LOG_ROWS = [
    {"expiry_field": "pucc_valid_until", "expiry_date": TODAY + timedelta(days=5),
     "trigger_offset": -7, "sent_at": datetime.now()},
]

SWEEP = {"last_sent": datetime(2026, 8, 30, 7, 0), "sent": 2}


@pytest.fixture
def db_rows():
    with patch("web.app.db.get_all_vehicles_with_expiry", return_value=VEHICLES), \
         patch("web.app.db.get_archived_vehicles", return_value=[]), \
         patch("web.app.db.get_active_snoozes", return_value=SNOOZES), \
         patch("web.app.db.get_reminder_counts", return_value=REMINDERS), \
         patch("web.app.db.get_reminder_offsets", return_value=LOG_ROWS), \
         patch("web.app.db.get_vehicle", side_effect=lambda reg, **k: next(
             (v for v in VEHICLES if v["registration_number"] == reg), None)), \
         patch("web.app.db.get_last_sweep", return_value=SWEEP):
        yield


@pytest.fixture
def client(db_rows):
    return TestClient(app)


@pytest.fixture
def signed_in(client):
    client.post("/login")
    return client


# ── sign-in ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", ["/", "/fleet", "/timeline", "/costs",
                                  "/vehicles/KL04AS1371", "/admin/users"])
def test_every_page_redirects_to_sign_in_when_anonymous(client, path):
    r = client.get(path, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


@pytest.mark.parametrize("path, data", [
    ("/vehicles/KL04AS1371/renew", {"field": "pucc_valid_until", "new_date": "2027-09-04"}),
    ("/vehicles/1/snooze", {"field": "pucc_valid_until", "duration": "7"}),
    ("/vehicles/1/unsnooze", {"field": "pucc_valid_until"}),
    ("/vehicles/KL04AS1371/archive", {"archived": "1"}),
    ("/vehicles/KL04AS1371/delete", {"confirm": "KL04AS1371"}),
])
def test_actions_reject_anonymous_writes(client, path, data):
    r = client.post(path, data=data, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_sign_in_page_shows_nothing_about_the_fleet(client):
    body = client.get("/login").text
    assert "Sign in" in body
    # No vehicle, owner, or count is visible before approval.
    for leak in ("KL04AS1371", "Priya Nair", "overdue", "vehicles tracked"):
        assert leak not in body


def test_dev_mode_signs_in_and_out(client):
    r = client.post("/login", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert "Sign out" in client.get("/").text
    client.post("/logout")
    assert client.get("/", follow_redirects=False).status_code == 303


# ── the approval gate ────────────────────────────────────────────────────


def test_unapproved_account_is_bounced_to_the_waiting_room(client):
    client.post("/login")
    row = {"subject": "g-1", "email": "new@example.com", "name": "New",
           "role": "member", "approved_at": None}
    with patch("web.auth.current_user", return_value={
            "sub": "g-1", "name": "New", "email": "new@example.com", "mode": "google"}), \
         patch("web.auth.db.get_web_user", return_value=row):
        r = client.get("/", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/pending"

        # The waiting room itself is reachable, and says nothing about the fleet.
        page = client.get("/pending")
        assert page.status_code == 200
        assert "hasn't granted access yet" in page.text
        assert "KL04AS1371" not in page.text


def test_approved_member_sees_data_but_not_the_admin_page(client):
    client.post("/login")
    row = {"subject": "g-2", "email": "m@example.com", "name": "Member",
           "role": "member", "approved_at": datetime.now()}
    with patch("web.auth.current_user", return_value={
            "sub": "g-2", "name": "Member", "email": "m@example.com", "mode": "google"}), \
         patch("web.auth.db.get_web_user", return_value=row):
        assert "KL04AS1371" in client.get("/fleet").text
        r = client.get("/admin/users", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/"


def test_deleted_account_is_signed_out_mid_session(client):
    client.post("/login")
    with patch("web.auth.current_user", return_value={
            "sub": "gone", "name": "", "email": "", "mode": "google"}), \
         patch("web.auth.db.get_web_user", return_value=None):
        r = client.get("/", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/login"


def test_json_callers_get_a_status_code_not_a_redirect(client):
    client.post("/login")
    row = {"subject": "g-3", "email": "n@example.com", "name": "N",
           "role": "member", "approved_at": None}
    with patch("web.auth.current_user", return_value={
            "sub": "g-3", "name": "N", "email": "", "mode": "google"}), \
         patch("web.auth.db.get_web_user", return_value=row):
        assert client.get("/", headers={"accept": "application/json"}).status_code == 403
    assert TestClient(app).get(
        "/", headers={"accept": "application/json"}).status_code == 401


# ── pages ────────────────────────────────────────────────────────────────


def test_dashboard_queues_documents_not_vehicles(signed_in):
    body = signed_in.get("/").text
    # Innova's PUCC is overdue and Swift's is due soon — two separate entries.
    assert "Pollution (PUCC) expired 11 days ago" in body
    assert "Pollution (PUCC) due in 5 days" in body
    # Ace's snoozed MV Tax is listed so it can be undone, worded as paused.
    assert "reminders paused" in body


def test_dashboard_tiles_count_the_fleet(signed_in):
    body = signed_in.get("/").text
    assert ">1<" in body and "overdue" in body
    assert "due within 30 days" in body


def test_matrix_renders_chips_and_plain_dates(signed_in):
    body = signed_in.get("/fleet").text
    assert 'class="badge badge-soon">5d<' in body
    assert 'class="badge badge-over">−11d<' in body
    assert 'class="plain"' in body
    assert 'class="na"' in body


def test_snoozed_document_is_struck_through_and_tagged(signed_in):
    body = signed_in.get("/fleet").text
    assert 'class="struck">−67d<' in body
    assert 'class="badge badge-mute">snoozed<' in body


def test_fleet_filters(signed_in):
    attention = signed_in.get("/fleet?scope=attention").text
    assert "KL04AS1371" in attention and "KL07CH8842" in attention
    assert "KL08BR1104" not in attention          # only overdue doc is snoozed
    assert "No archived vehicles" in signed_in.get("/fleet?scope=archived").text
    assert "KL08BR1104" in signed_in.get("/fleet?scope=bogus").text


def test_timeline_plots_only_what_is_inside_the_window(signed_in):
    body = signed_in.get("/timeline").text
    # Swift PUCC (+5), Innova insurance (+70) and PUCC (−11), Ace insurance (+80)
    # all land inside −60…+90 and are plotted.
    assert "KL04AS1371" in body and "KL07CH8842" in body and "KL08BR1104" in body
    assert 'title="Insurance ·' in body and 'title="Pollution (PUCC) ·' in body
    # Fitness (+670, +900, +600) is far outside the window and is not drawn;
    # Ace's MV Tax (−67) is overdue, so it pins to the left edge rather than
    # disappearing.
    assert 'title="Fitness / RC ·' not in body
    assert 'title="MV Tax ·' in body


def test_timeline_clamps_a_long_overdue_document_to_the_left_edge(signed_in):
    stale = [_v(9, "Stale", "KL09ZZ0001", "Nobody",
                mv_tax_valid_until=TODAY - timedelta(days=400))]
    with patch("web.app.db.get_all_vehicles_with_expiry", return_value=stale), \
         patch("web.app.db.get_active_snoozes", return_value=[]), \
         patch("web.app.db.get_reminder_counts", return_value=[]):
        body = signed_in.get("/timeline").text
    assert "KL09ZZ0001" in body
    assert 'style="left:0.0%"' in body or 'style="left:0%"' in body


def test_vehicle_detail_shows_every_column_and_the_ladder(signed_in):
    body = signed_in.get("/vehicles/KL04AS1371").text
    for label in ("Registration", "Nickname", "Owner", "Class", "Fuel",
                  "Permit type", "Registered", "Status"):
        assert f"<dt>{label}</dt>" in body
    assert "LMV" in body and "Petrol" in body
    assert "1 of 9 reminders sent" in body
    assert 'class="step' in body


def test_vehicle_detail_404s_for_an_unknown_registration(signed_in):
    assert signed_in.get("/vehicles/NOPE").status_code == 404


def test_costs_is_an_honest_stub(signed_in):
    body = signed_in.get("/costs").text
    assert "Not built yet" in body
    assert "KL04AS1371" not in body


def test_healthz_needs_no_session(client):
    assert client.get("/healthz").json() == {"status": "ok"}


# ── renew / snooze ───────────────────────────────────────────────────────


def test_renew_writes_the_new_date_and_returns_where_it_came_from(signed_in):
    with patch("web.app.db.update_vehicle_field", return_value=True) as update:
        r = signed_in.post(
            "/vehicles/KL04AS1371/renew",
            data={"field": "pucc_valid_until", "new_date": "2027-09-04",
                  "back": "/fleet?scope=attention"},
            follow_redirects=False,
        )
    update.assert_called_once_with("KL04AS1371", "pucc_valid_until", "2027-09-04")
    assert r.headers["location"].startswith("/fleet?scope=attention&notice=")


def test_renew_rejects_a_column_that_is_not_a_document(signed_in):
    with patch("web.app.db.update_vehicle_field") as update:
        r = signed_in.post("/vehicles/KL04AS1371/renew",
                           data={"field": "owner_name", "new_date": "2027-09-04"})
    assert r.status_code == 400
    update.assert_not_called()


def test_renew_rejects_an_unparseable_date(signed_in):
    with patch("web.app.db.update_vehicle_field") as update:
        r = signed_in.post("/vehicles/KL04AS1371/renew",
                           data={"field": "pucc_valid_until", "new_date": "next tuesday"},
                           follow_redirects=False)
    assert "error=" in r.headers["location"]
    update.assert_not_called()


@pytest.mark.parametrize("duration, expected_days", [("7", 7), ("14", 14), ("30", 30)])
def test_snooze_windows(signed_in, duration, expected_days):
    with patch("web.app.db.snooze_reminder") as snooze:
        signed_in.post("/vehicles/5/snooze",
                       data={"field": "mv_tax_valid_until", "duration": duration,
                             "reason": "being sold"},
                       follow_redirects=False)
    vehicle_id, field, until, reason, actor = snooze.call_args.args
    assert (vehicle_id, field, reason) == (5, "mv_tax_valid_until", "being sold")
    assert until == date.today() + timedelta(days=expected_days)
    assert actor == "web:thomas"   # same platform:id shape the bots write


def test_snooze_forever_stores_a_null_expiry(signed_in):
    with patch("web.app.db.snooze_reminder") as snooze:
        signed_in.post("/vehicles/5/snooze",
                       data={"field": "mv_tax_valid_until", "duration": "forever"},
                       follow_redirects=False)
    assert snooze.call_args.args[2] is None


def test_snooze_rejects_a_non_positive_window(signed_in):
    with patch("web.app.db.snooze_reminder") as snooze:
        r = signed_in.post("/vehicles/5/snooze",
                           data={"field": "mv_tax_valid_until", "duration": "0"},
                           follow_redirects=False)
    assert "error=" in r.headers["location"]
    snooze.assert_not_called()


def test_unsnooze_clears_the_row(signed_in):
    with patch("web.app.db.unsnooze_reminder", return_value=True) as unsnooze:
        r = signed_in.post("/vehicles/5/unsnooze",
                           data={"field": "mv_tax_valid_until"}, follow_redirects=False)
    unsnooze.assert_called_once_with(5, "mv_tax_valid_until")
    assert "notice=" in r.headers["location"]


# ── archive / delete ─────────────────────────────────────────────────────


def test_archive_and_restore(signed_in):
    with patch("web.app.db.set_vehicle_archived", return_value=True) as flag:
        signed_in.post("/vehicles/KL04AS1371/archive", data={"archived": "1"},
                       follow_redirects=False)
        assert flag.call_args.args == ("KL04AS1371", True)
        r = signed_in.post("/vehicles/KL04AS1371/archive", data={"archived": "0"},
                           follow_redirects=False)
        assert flag.call_args.args == ("KL04AS1371", False)
    assert r.headers["location"].startswith("/vehicles/KL04AS1371?")


def test_delete_requires_the_registration_typed_back(signed_in):
    with patch("web.app.db.delete_vehicle") as delete:
        r = signed_in.post("/vehicles/KL04AS1371/delete",
                           data={"confirm": "kl04"}, follow_redirects=False)
    delete.assert_not_called()
    assert "error=" in r.headers["location"]


def test_delete_accepts_the_exact_registration_case_insensitively(signed_in):
    with patch("web.app.db.delete_vehicle", return_value=True) as delete:
        r = signed_in.post("/vehicles/KL04AS1371/delete",
                           data={"confirm": " kl04as1371 "}, follow_redirects=False)
    delete.assert_called_once_with("KL04AS1371")
    assert r.headers["location"].startswith("/fleet?notice=")


# ── admin: approving accounts ────────────────────────────────────────────


ADMIN = {"sub": "g-admin", "name": "Owner", "email": "owner@example.com", "mode": "google"}
ADMIN_ROW = {"subject": "g-admin", "email": "owner@example.com", "name": "Owner",
             "role": "admin", "approved_at": datetime.now()}


@pytest.fixture
def as_admin(client):
    client.post("/login")
    with patch("web.auth.current_user", return_value=ADMIN), \
         patch("web.auth.db.get_web_user", return_value=ADMIN_ROW):
        yield client


def test_admin_sees_the_user_list_with_pending_first(as_admin):
    rows = [
        {"subject": "g-new", "email": "new@example.com", "name": "New",
         "role": "member", "approved_at": None, "approved_by": None,
         "created_at": datetime.now(), "last_seen_at": datetime.now()},
        dict(ADMIN_ROW, approved_by="bootstrap",
             created_at=datetime.now(), last_seen_at=datetime.now()),
    ]
    with patch("web.app.db.list_web_users", return_value=rows), \
         patch("web.app.db.count_pending_web_users", return_value=1):
        body = as_admin.get("/admin/users").text
    assert "new@example.com" in body
    assert "Awaiting approval" in body
    assert 'class="navcount">1<' in body    # the nav badge


def test_approving_an_account(as_admin):
    with patch("web.app.db.set_web_user_approved", return_value=True) as approve, \
         patch("web.app.db.count_web_admins", return_value=1):
        r = as_admin.post("/admin/users/g-new/approve", data={"approved": "1"},
                          follow_redirects=False)
    approve.assert_called_once_with("g-new", True, "web:g-admin")
    assert "notice=" in r.headers["location"]


def test_admin_cannot_revoke_their_own_access(as_admin):
    with patch("web.app.db.set_web_user_approved") as approve:
        r = as_admin.post("/admin/users/g-admin/approve", data={"approved": "0"},
                          follow_redirects=False)
    approve.assert_not_called()
    assert "error=" in r.headers["location"]


def test_the_last_admin_cannot_be_demoted(as_admin):
    with patch("web.app.db.count_web_admins", return_value=0), \
         patch("web.app.db.set_web_user_role") as role:
        r = as_admin.post("/admin/users/g-other/role", data={"role": "member"},
                          follow_redirects=False)
    role.assert_not_called()
    assert "error=" in r.headers["location"]


def test_promoting_a_member(as_admin):
    with patch("web.app.db.count_web_admins", return_value=1), \
         patch("web.app.db.set_web_user_role", return_value=True) as role:
        as_admin.post("/admin/users/g-new/role", data={"role": "admin"},
                      follow_redirects=False)
    role.assert_called_once_with("g-new", "admin")


def test_unknown_role_is_rejected(as_admin):
    with patch("web.app.db.set_web_user_role") as role:
        r = as_admin.post("/admin/users/g-new/role", data={"role": "root"})
    assert r.status_code == 400
    role.assert_not_called()


# ── adding and editing a vehicle ─────────────────────────────────────────


NEW = {
    "registration_number": "kl 09-zz 4242",
    "nickname": "Jeep",
    "owner_name": "Thomas V",
    "vehicle_class": "LMV",
    "fuel_type": "Diesel",
    "permit_type": "",
    "registration_date": "2021-06-01",
    "insurance_valid_until": "2027-05-05",
    "pucc_valid_until": "",
    "fitness_valid_until": "",
    "mv_tax_valid_until": "",
    "permit_valid_until": "",
}


def test_add_form_renders_every_document_and_detail_input(signed_in):
    body = signed_in.get("/vehicles/new").text
    assert 'name="registration_number"' in body
    for field, _, long in [
        ("insurance_valid_until", "", "Insurance"),
        ("pucc_valid_until", "", "Pollution (PUCC)"),
        ("fitness_valid_until", "", "Fitness / RC"),
        ("mv_tax_valid_until", "", "MV Tax"),
        ("permit_valid_until", "", "Permit"),
    ]:
        assert f'name="{field}"' in body
    for field in ("nickname", "owner_name", "vehicle_class", "fuel_type", "permit_type"):
        assert f'name="{field}"' in body


def test_new_is_not_swallowed_by_the_registration_route(signed_in):
    # /vehicles/{registration} is declared after /vehicles/new; if the order
    # ever flips, "new" gets treated as a registration and 404s.
    assert signed_in.get("/vehicles/new").status_code == 200


def test_adding_a_vehicle_writes_normalised_values(signed_in):
    with patch("web.app.db.registration_exists", return_value=False), \
         patch("web.app.db.create_vehicle", return_value={"id": 9}) as create:
        r = signed_in.post("/vehicles/new", data=NEW, follow_redirects=False)

    values = create.call_args.args[0]
    assert values["registration_number"] == "KL09ZZ4242"     # spaces stripped
    assert values["nickname"] == "Jeep"
    assert values["insurance_valid_until"] == date(2027, 5, 5)
    assert values["pucc_valid_until"] is None                # blank → NULL
    assert values["permit_type"] is None                     # blank → NULL
    assert "_existing" not in values                         # internal marker
    assert r.headers["location"].startswith("/vehicles/KL09ZZ4242?")


def test_adding_a_duplicate_registration_is_a_field_error(signed_in):
    with patch("web.app.db.registration_exists", return_value=True), \
         patch("web.app.db.create_vehicle") as create:
        r = signed_in.post("/vehicles/new", data=NEW)
    create.assert_not_called()
    assert r.status_code == 200                    # form redisplayed, not a redirect
    assert "already tracked" in r.text


def test_add_form_redisplays_errors_without_losing_input(signed_in):
    bad = dict(NEW, registration_number="", nickname="Jeep")
    with patch("web.app.db.create_vehicle") as create:
        r = signed_in.post("/vehicles/new", data=bad)
    create.assert_not_called()
    assert "A registration number is required." in r.text
    assert 'value="Jeep"' in r.text                 # what was typed survives


def test_add_survives_a_unique_violation_that_beat_the_check(signed_in):
    # The pre-check is racy; the constraint is the real guard, and losing
    # that race must be a field error rather than a 500.
    from psycopg2 import errors as pgerrors
    with patch("web.app.db.registration_exists", return_value=False), \
         patch("web.app.db.create_vehicle", side_effect=pgerrors.UniqueViolation()):
        r = signed_in.post("/vehicles/new", data=NEW)
    assert r.status_code == 200
    assert "already tracked" in r.text


def test_edit_form_is_prefilled_from_the_vehicle(signed_in):
    body = signed_in.get("/vehicles/KL04AS1371/edit").text
    assert 'value="KL04AS1371"' in body
    assert 'value="Swift"' in body
    assert 'value="LMV"' in body
    assert 'value="Petrol"' in body


def test_edit_form_404s_for_an_unknown_vehicle(signed_in):
    assert signed_in.get("/vehicles/NOPE/edit").status_code == 404


def test_saving_an_edit_updates_by_id(signed_in):
    changed = dict(NEW, registration_number="KL04AS1371", nickname="Swift VXI")
    with patch("web.app.db.registration_exists", return_value=False), \
         patch("web.app.db.update_vehicle", return_value={"id": 1}) as update:
        r = signed_in.post("/vehicles/KL04AS1371/edit", data=changed, follow_redirects=False)

    vehicle_id, values = update.call_args.args
    assert vehicle_id == 1                          # keyed on id, not registration
    assert values["nickname"] == "Swift VXI"
    assert r.headers["location"].startswith("/vehicles/KL04AS1371?")


def test_edit_can_change_the_registration_and_redirects_to_the_new_one(signed_in):
    renamed = dict(NEW, registration_number="KL04AS9999")
    with patch("web.app.db.registration_exists", return_value=False), \
         patch("web.app.db.update_vehicle", return_value={"id": 1}):
        r = signed_in.post("/vehicles/KL04AS1371/edit", data=renamed, follow_redirects=False)
    assert r.headers["location"].startswith("/vehicles/KL04AS9999?")


def test_edit_uniqueness_check_excludes_the_vehicle_itself(signed_in):
    same = dict(NEW, registration_number="KL04AS1371")
    with patch("web.app.db.registration_exists", return_value=False) as exists, \
         patch("web.app.db.update_vehicle", return_value={"id": 1}):
        signed_in.post("/vehicles/KL04AS1371/edit", data=same, follow_redirects=False)
    # Without excluding_id the vehicle would collide with its own row.
    assert exists.call_args.kwargs == {"excluding_id": 1}


def test_edit_rejects_taking_another_vehicles_registration(signed_in):
    clash = dict(NEW, registration_number="KL07CH8842")
    with patch("web.app.db.registration_exists", return_value=True), \
         patch("web.app.db.update_vehicle") as update:
        r = signed_in.post("/vehicles/KL04AS1371/edit", data=clash)
    update.assert_not_called()
    assert "already tracked" in r.text


def test_add_and_edit_reject_anonymous_writes(client):
    for path in ("/vehicles/new", "/vehicles/KL04AS1371/edit"):
        r = client.post(path, data=NEW, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/login"
        assert client.get(path, follow_redirects=False).headers["location"] == "/login"


# ── row actions on the fleet list ────────────────────────────────────────


def test_fleet_rows_offer_edit_archive_and_delete(signed_in):
    # Delete used to exist only on the detail page, one level down, which
    # made it effectively invisible from the list.
    body = signed_in.get("/fleet").text
    assert 'href="/vehicles/KL04AS1371/edit"' in body
    assert 'action="/vehicles/KL04AS1371/archive"' in body
    assert 'data-action="open-delete"' in body
    assert 'data-reg="KL04AS1371"' in body
    # And the shared confirm dialog is on the page to receive them.
    assert 'id="delete-dialog"' in body
    assert 'data-form="delete"' in body


def test_fleet_row_offers_archive_for_a_live_vehicle(signed_in):
    body = signed_in.get("/fleet").text
    assert 'aria-label="Archive KL04AS1371"' in body
    assert 'aria-label="Restore KL04AS1371"' not in body


def test_archived_row_offers_restore_instead_of_archive(signed_in):
    archived = [dict(VEHICLES[0], status="archived")]
    with patch("web.app.db.get_archived_vehicles", return_value=archived):
        body = signed_in.get("/fleet?scope=archived").text
    assert 'aria-label="Restore KL04AS1371"' in body
    assert 'aria-label="Archive KL04AS1371"' not in body
    assert 'value="0"' in body          # the archive form posts un-archive


def test_vehicle_page_delete_trigger_carries_its_vehicle(signed_in):
    body = signed_in.get("/vehicles/KL04AS1371").text
    assert 'data-action="open-delete"' in body
    assert 'data-reg="KL04AS1371"' in body
    assert 'data-nickname="Swift"' in body
    assert 'id="delete-dialog"' in body


def test_deleting_from_the_fleet_list_hits_the_same_endpoint(signed_in):
    # The dialog is shared, so this is the detail page's path exercised from
    # the list; the guard must behave identically.
    with patch("web.app.db.delete_vehicle", return_value=True) as delete:
        r = signed_in.post("/vehicles/KL04AS1371/delete",
                           data={"confirm": "KL04AS1371"}, follow_redirects=False)
    delete.assert_called_once_with("KL04AS1371")
    assert r.headers["location"].startswith("/fleet?notice=")


# ── digest recipients ────────────────────────────────────────────────────


RECIPIENTS = [
    {"id": 1, "email": "owner@example.com", "name": "Thomas", "active": True,
     "created_at": datetime(2026, 8, 30, 9, 0), "created_by": "web:thomas"},
    {"id": 2, "email": "priya@example.com", "name": "Priya", "active": False,
     "created_at": datetime(2026, 8, 29, 9, 0), "created_by": "web:thomas"},
]


@pytest.fixture
def admin_page(as_admin):
    """The admin page with both lists readable."""
    with patch("web.app.db.list_web_users", return_value=[]), \
         patch("web.app.db.count_pending_web_users", return_value=0), \
         patch("web.app.db.list_notification_recipients", return_value=RECIPIENTS):
        yield as_admin


def test_admin_page_offers_a_field_to_add_a_recipient(admin_page):
    body = admin_page.get("/admin/users").text
    assert 'action="/admin/recipients"' in body
    assert 'name="email"' in body
    assert "Reminder emails" in body
    # Recipients need no PitStop account — that is the point of a separate list.
    assert "do not need a PitStop account" in body


def test_admin_page_lists_recipients_and_their_state(admin_page):
    body = admin_page.get("/admin/users").text
    assert "owner@example.com" in body and "priya@example.com" in body
    assert "Receiving" in body and "Paused" in body


def test_admin_page_survives_an_unreadable_recipient_list(as_admin):
    with patch("web.app.db.list_web_users", return_value=[]), \
         patch("web.app.db.count_pending_web_users", return_value=0), \
         patch("web.app.db.list_notification_recipients", side_effect=OSError("db down")):
        r = as_admin.get("/admin/users")
    assert r.status_code == 200
    assert "Could not read the recipient list" in r.text


def test_empty_list_says_what_the_digest_falls_back_to(as_admin, monkeypatch):
    monkeypatch.setenv("EMAIL_TO", "fallback@example.com")
    with patch("web.app.db.list_web_users", return_value=[]), \
         patch("web.app.db.count_pending_web_users", return_value=0), \
         patch("web.app.db.list_notification_recipients", return_value=[]):
        body = as_admin.get("/admin/users").text
    assert "Nobody is on the list yet" in body
    assert "fallback@example.com" in body


def test_adding_a_recipient_normalises_the_address(as_admin):
    with patch("web.app.db.add_notification_recipient",
               return_value={"id": 3, "inserted": True}) as add:
        r = as_admin.post("/admin/recipients",
                          data={"email": "  Priya@Example.COM ", "name": " Priya "},
                          follow_redirects=False)
    add.assert_called_once_with("priya@example.com", "Priya", "web:g-admin")
    assert "notice=" in r.headers["location"]


def test_adding_a_malformed_address_is_refused(as_admin):
    with patch("web.app.db.add_notification_recipient") as add:
        r = as_admin.post("/admin/recipients", data={"email": "notanemail"},
                          follow_redirects=False)
    add.assert_not_called()
    assert "error=" in r.headers["location"]


def test_re_adding_an_existing_address_says_so_and_does_not_duplicate(as_admin):
    # ON CONFLICT DO UPDATE returns the existing row with inserted=False
    # rather than failing, so the address is reactivated, not duplicated.
    with patch("web.app.db.add_notification_recipient",
               return_value={"id": 1, "inserted": False}):
        r = as_admin.post("/admin/recipients", data={"email": "a@b.com"},
                          follow_redirects=False)
    assert "already+on+the+list" in r.headers["location"]
    assert "error=" not in r.headers["location"]      # not a failure


def test_a_genuinely_new_address_reads_as_added(as_admin):
    with patch("web.app.db.add_notification_recipient",
               return_value={"id": 9, "inserted": True}):
        r = as_admin.post("/admin/recipients", data={"email": "new@b.com"},
                          follow_redirects=False)
    assert "will+receive" in r.headers["location"]


def test_a_failing_insert_does_not_500(as_admin):
    with patch("web.app.db.add_notification_recipient", side_effect=OSError("db down")):
        r = as_admin.post("/admin/recipients", data={"email": "a@b.com"},
                          follow_redirects=False)
    assert r.status_code == 303 and "error=" in r.headers["location"]


def test_pausing_and_resuming_a_recipient(as_admin):
    with patch("web.app.db.set_notification_recipient_active", return_value=True) as flag:
        as_admin.post("/admin/recipients/1/active", data={"active": "0"},
                      follow_redirects=False)
        assert flag.call_args.args == (1, False)
        as_admin.post("/admin/recipients/1/active", data={"active": "1"},
                      follow_redirects=False)
        assert flag.call_args.args == (1, True)


def test_removing_a_recipient(as_admin):
    with patch("web.app.db.delete_notification_recipient", return_value=True) as rm:
        r = as_admin.post("/admin/recipients/2/delete", follow_redirects=False)
    rm.assert_called_once_with(2)
    assert "notice=" in r.headers["location"]


def test_recipient_routes_are_admin_only(client):
    # An approved member must not be able to redirect the reminder mail.
    row = {"subject": "g-2", "email": "m@example.com", "name": "Member",
           "role": "member", "approved_at": datetime.now()}
    with patch("web.auth.current_user", return_value={
            "sub": "g-2", "name": "M", "email": "", "mode": "google"}), \
         patch("web.auth.db.get_web_user", return_value=row), \
         patch("web.app.db.add_notification_recipient") as add:
        r = client.post("/admin/recipients", data={"email": "attacker@evil.com"},
                        follow_redirects=False)
    add.assert_not_called()
    assert r.status_code == 303 and r.headers["location"] == "/"


def test_recipient_routes_reject_anonymous(client):
    for path in ("/admin/recipients", "/admin/recipients/1/active",
                 "/admin/recipients/1/delete"):
        r = client.post(path, data={"email": "a@b.com"}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/login"

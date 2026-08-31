-- Re-sync identity sequences with the data.
--
-- The rows were restored from the DropHunter backup with explicit ids, which
-- does not advance a sequence. reminder_log held ids up to 40 while its
-- sequence still sat at 3, so every insert drew an id that already existed and
-- collided on the primary key.
--
-- That failed *silently* because the sweep's insert used
-- `ON CONFLICT DO NOTHING` with no target, which swallows any unique
-- violation. The sweep sent its reminders, logged "2 reminder(s) sent", and
-- wrote nothing — so the same reminders would have fired again every day.
-- db/client.py now names the intended constraint so a primary-key collision
-- raises instead of disappearing.
--
-- Idempotent. Re-run this after any future restore or bulk import.

SELECT setval(pg_get_serial_sequence('public.' || t, 'id'),
              GREATEST(COALESCE(m, 0), 1),
              m IS NOT NULL)
FROM (VALUES
  ('reminder_log',            (SELECT max(id) FROM public.reminder_log)),
  ('vehicles',                (SELECT max(id) FROM public.vehicles)),
  ('reminder_snooze',         (SELECT max(id) FROM public.reminder_snooze)),
  ('notification_recipients', (SELECT max(id) FROM public.notification_recipients)),
  ('web_users',               (SELECT max(id) FROM public.web_users))
) AS s(t, m);

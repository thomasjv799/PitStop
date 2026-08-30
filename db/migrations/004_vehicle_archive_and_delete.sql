-- Archiving and permanent deletion of vehicles.
--
-- Archive is a status flag: the row stays, but the fleet, the cron sweep and
-- the bot all skip it. `status` is free text on an externally-created table,
-- so we match the archived value exactly and leave every other value —
-- including NULL — alone.
--
-- Permanent deletion needs reminder_log to follow the vehicle out.
-- reminder_snooze already cascades; reminder_log was created without an
-- ON DELETE clause, so a delete would fail on the foreign key.

ALTER TABLE reminder_log DROP CONSTRAINT IF EXISTS reminder_log_vehicle_id_fkey;

ALTER TABLE reminder_log
    ADD CONSTRAINT reminder_log_vehicle_id_fkey
    FOREIGN KEY (vehicle_id) REFERENCES vehicles(id) ON DELETE CASCADE;

-- Archived vehicles are read out of almost every query; index the exclusion.
CREATE INDEX IF NOT EXISTS vehicles_status ON vehicles (status);

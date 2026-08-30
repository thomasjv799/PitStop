-- Lock the tables to the anon and authenticated roles.
--
-- Supabase exposes every table in `public` through PostgREST to the `anon`
-- and `authenticated` roles. With row-level security off, anyone holding the
-- project's publishable (anon) key can read and write every row — vehicles,
-- owner names, registration marks, and the web_users account table.
--
-- Nothing in this project uses the anon key. The bot, the cron sweep and the
-- web app all connect with DATABASE_URI as `postgres`, and DropHunter's
-- backup writes with the service role. Both of those roles carry BYPASSRLS,
-- so enabling RLS does not affect them:
--
--     rolname       rolbypassrls
--     postgres      true      <- DATABASE_URI
--     service_role  true      <- backup / server-side keys
--     anon          false     <- publishable key, what we are closing
--     authenticated false
--
-- Enabling RLS with no policies therefore denies anon and authenticated
-- everything, and leaves every application untouched. Policies are added
-- only if a browser client is ever pointed at this database.
--
-- To reverse a single table:
--     ALTER TABLE public.<table> DISABLE ROW LEVEL SECURITY;

-- PitStop
ALTER TABLE public.vehicles          ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.reminder_log      ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.reminder_snooze   ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.web_users         ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.chat_messages     ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.chat_summary      ENABLE ROW LEVEL SECURITY;

-- DropHunter shares this database.
ALTER TABLE public.games                   ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.price_history           ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.notifications_log       ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.watches                 ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.watch_price_history     ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.watch_notifications_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.allowed_users           ENABLE ROW LEVEL SECURITY;

-- Baby Shopping v0.32
-- In-app notification phase. Push is intentionally paused until the next phase.

alter table public.notification_preferences
  alter column push_enabled set default false;

update public.notification_preferences
set push_enabled = false,
    updated_at = now()
where push_enabled = true;

create or replace function private.seed_notification_preferences_v032()
returns trigger
language plpgsql
security definer
set search_path = public, private, pg_temp
as $$
begin
  insert into public.notification_preferences(
    household_id,user_id,family_missing_enabled,price_deal_enabled,low_stock_enabled,push_enabled
  ) values(new.household_id,new.user_id,true,true,true,false)
  on conflict(household_id,user_id) do nothing;
  return new;
end;
$$;

-- Keep push infrastructure intact, but do not dispatch while in-app behavior is under test.
alter table public.notifications disable trigger notifications_push_dispatch_v032;

-- Deal scans are server-side only.
revoke all on function public.scan_deal_notifications_v032() from public, anon, authenticated;
grant execute on function public.scan_deal_notifications_v032() to service_role;

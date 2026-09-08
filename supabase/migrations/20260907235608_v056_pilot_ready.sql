-- Baby Smart List v0.56: pilot readiness, privacy-safe product analytics,
-- structured feedback, and reversible deletion requests.

create table if not exists public.pilot_events (
  id uuid primary key default gen_random_uuid(),
  household_id uuid not null references public.households(id) on delete cascade,
  user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  event_name text not null check (event_name = any (array[
    'app_open',
    'onboarding_completed',
    'household_joined',
    'product_added',
    'product_updated',
    'exact_package_selected',
    'stock_marked_missing',
    'push_enabled',
    'push_test_success',
    'notification_opened',
    'price_comparison_opened',
    'shopping_basket_opened',
    'purchase_recorded',
    'feedback_submitted',
    'install_prompt_shown',
    'app_installed',
    'deletion_requested'
  ])),
  metadata jsonb not null default '{}'::jsonb
    check (jsonb_typeof(metadata) = 'object' and pg_column_size(metadata) <= 4096),
  session_id text check (session_id is null or char_length(session_id) between 8 and 80),
  client_version text not null default 'v0.56'
    check (char_length(client_version) between 1 and 20),
  created_at timestamptz not null default now()
);

comment on table public.pilot_events is
  'Minimal v0.56 pilot usage events. Metadata must not contain email, names, free text, or precise location.';

create index if not exists pilot_events_household_created_idx
  on public.pilot_events (household_id, created_at desc);
create index if not exists pilot_events_name_created_idx
  on public.pilot_events (event_name, created_at desc);
create index if not exists pilot_events_user_created_idx
  on public.pilot_events (user_id, created_at desc);

alter table public.pilot_events enable row level security;
revoke all on table public.pilot_events from public, anon, authenticated;
grant insert on table public.pilot_events to authenticated;
grant all on table public.pilot_events to service_role;

drop policy if exists pilot_events_insert_own_household on public.pilot_events;
create policy pilot_events_insert_own_household
on public.pilot_events
for insert
to authenticated
with check (
  user_id = (select auth.uid())
  and private.is_household_member(household_id)
);

create table if not exists public.pilot_feedback_reports (
  id uuid primary key default gen_random_uuid(),
  household_id uuid not null references public.households(id) on delete cascade,
  user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  category text not null check (category = any (array[
    'bug', 'suggestion', 'data', 'notification', 'other'
  ])),
  message text not null check (char_length(btrim(message)) between 3 and 2000),
  context jsonb not null default '{}'::jsonb
    check (jsonb_typeof(context) = 'object' and pg_column_size(context) <= 8192),
  status text not null default 'new'
    check (status = any (array['new', 'reviewing', 'resolved', 'closed'])),
  created_at timestamptz not null default now(),
  resolved_at timestamptz
);

comment on table public.pilot_feedback_reports is
  'Structured pilot bug reports and suggestions submitted by household members.';

create index if not exists pilot_feedback_reports_household_created_idx
  on public.pilot_feedback_reports (household_id, created_at desc);
create index if not exists pilot_feedback_reports_status_created_idx
  on public.pilot_feedback_reports (status, created_at desc);
create index if not exists pilot_feedback_reports_user_created_idx
  on public.pilot_feedback_reports (user_id, created_at desc);

alter table public.pilot_feedback_reports enable row level security;
revoke all on table public.pilot_feedback_reports from public, anon, authenticated;
grant insert, select on table public.pilot_feedback_reports to authenticated;
grant all on table public.pilot_feedback_reports to service_role;

drop policy if exists pilot_feedback_reports_insert_own_household on public.pilot_feedback_reports;
create policy pilot_feedback_reports_insert_own_household
on public.pilot_feedback_reports
for insert
to authenticated
with check (
  user_id = (select auth.uid())
  and status = 'new'
  and resolved_at is null
  and private.is_household_member(household_id)
);

drop policy if exists pilot_feedback_reports_select_own on public.pilot_feedback_reports;
create policy pilot_feedback_reports_select_own
on public.pilot_feedback_reports
for select
to authenticated
using (user_id = (select auth.uid()));

create table if not exists public.data_deletion_requests (
  id uuid primary key default gen_random_uuid(),
  household_id uuid not null references public.households(id) on delete cascade,
  user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  request_scope text not null check (request_scope = any (array['account', 'household'])),
  reason text check (reason is null or char_length(reason) <= 1000),
  status text not null default 'pending'
    check (status = any (array['pending', 'processing', 'completed', 'cancelled'])),
  requested_at timestamptz not null default now(),
  resolved_at timestamptz
);

comment on table public.data_deletion_requests is
  'Reversible account or household deletion requests for manual pilot administration.';

create unique index if not exists data_deletion_requests_one_open_idx
  on public.data_deletion_requests (user_id, request_scope)
  where status in ('pending', 'processing');
create index if not exists data_deletion_requests_status_requested_idx
  on public.data_deletion_requests (status, requested_at);
create index if not exists data_deletion_requests_household_requested_idx
  on public.data_deletion_requests (household_id, requested_at desc);
create index if not exists data_deletion_requests_user_requested_idx
  on public.data_deletion_requests (user_id, requested_at desc);

alter table public.data_deletion_requests enable row level security;
revoke all on table public.data_deletion_requests from public, anon, authenticated;
grant insert, select on table public.data_deletion_requests to authenticated;
grant update (status) on table public.data_deletion_requests to authenticated;
grant all on table public.data_deletion_requests to service_role;

drop policy if exists data_deletion_requests_insert_own on public.data_deletion_requests;
create policy data_deletion_requests_insert_own
on public.data_deletion_requests
for insert
to authenticated
with check (
  user_id = (select auth.uid())
  and status = 'pending'
  and resolved_at is null
  and private.is_household_member(household_id)
  and (
    request_scope = 'account'
    or (request_scope = 'household' and private.is_household_owner(household_id))
  )
);

drop policy if exists data_deletion_requests_select_own on public.data_deletion_requests;
create policy data_deletion_requests_select_own
on public.data_deletion_requests
for select
to authenticated
using (user_id = (select auth.uid()));

drop policy if exists data_deletion_requests_cancel_own on public.data_deletion_requests;
create policy data_deletion_requests_cancel_own
on public.data_deletion_requests
for update
to authenticated
using (user_id = (select auth.uid()) and status = 'pending')
with check (user_id = (select auth.uid()) and status = 'cancelled');

create or replace function public.get_pilot_readiness_v056(p_household_id uuid)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_user uuid := auth.uid();
  v_member_count integer := 0;
  v_push_ready_members integer := 0;
  v_active_products integer := 0;
  v_exact_products integer := 0;
  v_image_products integer := 0;
  v_quantity_products integer := 0;
  v_location_ready boolean := false;
  v_is_owner boolean := false;
begin
  if v_user is null then
    raise exception 'authentication_required';
  end if;

  if p_household_id is null or not exists (
    select 1
    from public.household_members hm
    where hm.household_id = p_household_id
      and hm.user_id = v_user
  ) then
    raise exception 'household_access_denied';
  end if;

  select
    count(*)::integer,
    bool_or(hm.user_id = v_user and hm.role = 'owner')
  into v_member_count, v_is_owner
  from public.household_members hm
  where hm.household_id = p_household_id;

  select count(distinct hm.user_id)::integer
  into v_push_ready_members
  from public.household_members hm
  where hm.household_id = p_household_id
    and not exists (
      select 1
      from public.notification_preferences np
      where np.household_id = hm.household_id
        and np.user_id = hm.user_id
        and np.push_enabled = false
    )
    and exists (
      select 1
      from public.push_subscriptions ps
      where ps.user_id = hm.user_id
        and ps.disabled_at is null
    );

  select
    count(*)::integer,
    count(*) filter (
      where nullif(btrim(p.preferred_barcode), '') is not null
    )::integer,
    count(*) filter (
      where p.quantity is not null
    )::integer,
    count(*) filter (
      where nullif(btrim(p.package_image_path), '') is not null
        or exists (
          select 1
          from public.baby_product_catalog c
          where c.active = true
            and c.barcode = p.preferred_barcode
            and c.image_url ~ '^https://'
        )
    )::integer
  into v_active_products, v_exact_products, v_quantity_products, v_image_products
  from public.products p
  where p.household_id = p_household_id
    and p.is_active = true;

  select (
    h.city is not null
    and btrim(h.city) <> ''
    and h.latitude is not null
    and h.longitude is not null
    and h.search_radius_km between 1 and 50
  )
  into v_location_ready
  from public.households h
  where h.id = p_household_id;

  return jsonb_build_object(
    'member_count', v_member_count,
    'push_ready_members', v_push_ready_members,
    'active_products', v_active_products,
    'exact_products', v_exact_products,
    'image_products', v_image_products,
    'quantity_products', v_quantity_products,
    'location_ready', coalesce(v_location_ready, false),
    'is_owner', coalesce(v_is_owner, false),
    'server_ready', (
      v_member_count >= 2
      and v_push_ready_members = v_member_count
      and v_active_products >= 3
      and v_exact_products >= 3
      and coalesce(v_location_ready, false)
    )
  );
end;
$$;

comment on function public.get_pilot_readiness_v056(uuid) is
  'Returns household-level pilot readiness counts after verifying the caller is a household member.';

revoke all on function public.get_pilot_readiness_v056(uuid) from public, anon, authenticated;
grant execute on function public.get_pilot_readiness_v056(uuid) to authenticated, service_role;

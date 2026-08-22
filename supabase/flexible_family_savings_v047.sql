-- Baby Smart List v0.47: flexible inventory, safe family details and savings.
-- Existing products and purchase history are retained. Products leave an
-- inventory through archival rather than destructive deletion.

begin;

alter table public.products
  add column if not exists is_active boolean not null default true;

alter table public.household_members
  add column if not exists display_name text;

alter table public.household_members
  alter column display_name set default coalesce(
    nullif(btrim((auth.jwt() -> 'user_metadata') ->> 'full_name'), ''),
    nullif(split_part(coalesce(auth.jwt() ->> 'email', ''), '@', 1), ''),
    'בן/בת משפחה'
  );

update public.household_members as member
set display_name = coalesce(
  nullif(btrim(auth_user.raw_user_meta_data ->> 'full_name'), ''),
  nullif(split_part(coalesce(auth_user.email, ''), '@', 1), ''),
  'בן/בת משפחה'
)
from auth.users as auth_user
where auth_user.id = member.user_id
  and nullif(btrim(member.display_name), '') is null;

update public.household_members
set display_name = 'בן/בת משפחה'
where nullif(btrim(display_name), '') is null;

alter table public.household_members
  alter column display_name set not null;

-- Keep the strongest existing row for each built-in need; archive duplicates
-- so historical purchase_events and inventory_snapshots remain available.
with ranked_products as (
  select
    id,
    row_number() over (
      partition by household_id, need_key
      order by
        (nullif(preferred_barcode, '') is not null) desc,
        case stock_status::text
          when 'urgent' then 0
          when 'out' then 1
          when 'low' then 2
          else 3
        end,
        updated_at desc,
        created_at desc
    ) as row_rank
  from public.products
  where is_active
    and need_key <> 'other'
)
update public.products as product
set is_active = false
from ranked_products as ranked
where product.id = ranked.id
  and ranked.row_rank > 1;

update public.products
set allow_alternatives = false
where need_key = 'formula'
  and allow_alternatives is distinct from false;

create unique index if not exists products_one_active_need_v047
  on public.products (household_id, need_key)
  where is_active and need_key <> 'other';

create unique index if not exists household_members_one_family_v047
  on public.household_members (user_id);

create or replace view public.household_monthly_savings_v047
with (security_invoker = true)
as
select
  household_id,
  date_trunc('month', purchased_at at time zone 'Asia/Jerusalem')::date as month_start,
  count(*)::integer as purchase_count,
  coalesce(sum(greatest(coalesce(calculated_savings, 0), 0)), 0)::numeric(12, 2)
    as actual_savings,
  coalesce(sum(paid_price * package_count), 0)::numeric(12, 2) as total_spent
from public.purchase_events
group by household_id, date_trunc('month', purchased_at at time zone 'Asia/Jerusalem')::date;

revoke all on public.household_monthly_savings_v047 from public;
revoke all on public.household_monthly_savings_v047 from anon;
grant select on public.household_monthly_savings_v047 to authenticated, service_role;

comment on view public.household_monthly_savings_v047 is
  'Monthly purchase totals and realized savings; invoker security preserves household RLS.';

commit;

-- v0.53: re-enable device push, synchronize exact barcodes and suppress repeats.

alter table public.notification_preferences
  alter column push_enabled set default true;

-- Push was disabled globally by v0.32 while the transport was being repaired.
-- Restore it for existing household members; each device still requires the
-- user's browser permission and an active subscription.
update public.notification_preferences
set push_enabled = true,
    updated_at = now()
where push_enabled = false;

create or replace function private.seed_notification_preferences_v032()
returns trigger
language plpgsql
security definer
set search_path = 'public', 'private', 'pg_temp'
as $function$
begin
  insert into public.notification_preferences(
    household_id,user_id,family_missing_enabled,price_deal_enabled,low_stock_enabled,push_enabled
  ) values(new.household_id,new.user_id,true,true,true,true)
  on conflict(household_id,user_id) do nothing;
  return new;
end;
$function$;

-- Keep the legacy barcode column in sync with the exact catalog preference so
-- every configured product exposes one canonical barcode to older clients too.
update public.products
set barcode = preferred_barcode
where preferred_barcode is not null
  and barcode is distinct from preferred_barcode;

create or replace function private.sync_exact_product_barcode_v053()
returns trigger
language plpgsql
security definer
set search_path = ''
as $function$
begin
  if new.preferred_barcode is not null then
    new.barcode := new.preferred_barcode;
  elsif new.need_key <> 'other' then
    new.barcode := null;
  end if;
  return new;
end;
$function$;

drop trigger if exists products_sync_exact_barcode_v053 on public.products;
create trigger products_sync_exact_barcode_v053
before insert or update of preferred_barcode, need_key on public.products
for each row execute function private.sync_exact_product_barcode_v053();

-- Remove only byte-for-byte repeated deal cards, retaining the newest copy for
-- each recipient and product. Other stock episodes remain untouched.
with ranked as (
  select id,
         row_number() over (
           partition by user_id, notification_type, product_id, title, body
           order by created_at desc, id desc
         ) as copy_number
  from public.notifications
  where notification_type = 'price_deal'
)
delete from public.notifications n
using ranked r
where n.id = r.id
  and r.copy_number > 1;

create or replace function private.route_notification_event()
returns trigger
language plpgsql
security definer
set search_path = ''
as $function$
declare
  v_window interval;
  v_bucket_seconds bigint;
  v_bucket bigint;
  v_fingerprint text;
  v_dedupe_key text;
  v_count integer := 0;
begin
  v_window := case new.event_type
    when 'family_missing' then interval '2 hours'
    when 'low_stock' then interval '18 hours'
    when 'price_deal' then interval '7 days'
    else interval '30 minutes'
  end;

  v_bucket_seconds := case new.event_type
    when 'family_missing' then 7200
    when 'low_stock' then 64800
    when 'price_deal' then 604800
    else 1800
  end;
  v_bucket := floor(extract(epoch from coalesce(new.created_at, now())) / v_bucket_seconds)::bigint;
  v_fingerprint := md5(concat_ws('|',
    new.event_type,
    coalesce(new.product_id::text, 'none'),
    new.title,
    new.body,
    coalesce(new.data->>'barcode', ''),
    coalesce(new.data->>'chain_id', ''),
    coalesce(new.data->>'branch_code', ''),
    coalesce(new.data->>'effective_price', ''),
    coalesce(new.data->>'promo_total_price', ''),
    coalesce(new.data->>'promo_min_quantity', ''),
    coalesce(new.data->>'promo_end_at', '')
  ));
  v_dedupe_key := concat('v053:', new.event_type, ':', v_fingerprint, ':', v_bucket);

  begin
    insert into public.notifications (
      household_id,
      user_id,
      notification_type,
      product_id,
      title,
      body,
      data,
      dedupe_key
    )
    select
      new.household_id,
      hm.user_id,
      new.event_type,
      new.product_id,
      new.title,
      new.body,
      new.data || jsonb_build_object('event_id', new.id),
      v_dedupe_key
    from public.household_members hm
    where hm.household_id = new.household_id
      and (
        new.event_type <> 'family_missing'
        or new.actor_user_id is null
        or hm.user_id is distinct from new.actor_user_id
      )
      and private.notification_pref_enabled(
        new.household_id,
        hm.user_id,
        new.event_type
      )
      and not exists (
        select 1
        from public.notifications n
        where n.user_id = hm.user_id
          and n.notification_type = new.event_type
          and n.product_id is not distinct from new.product_id
          and n.created_at > now() - v_window
          and (
            new.event_type <> 'price_deal'
            or (
              n.title = new.title
              and n.body = new.body
            )
          )
      )
    on conflict (user_id, dedupe_key) where dedupe_key is not null do nothing;

    get diagnostics v_count = row_count;

    update private.notification_events
    set processed_at = now(),
        recipients_count = v_count,
        process_error = null
    where id = new.id;
  exception when others then
    update private.notification_events
    set processed_at = now(),
        recipients_count = 0,
        process_error = sqlstate || ': ' || sqlerrm
    where id = new.id;
  end;

  return new;
end;
$function$;

alter table public.notifications enable trigger notifications_push_dispatch_v032;

comment on function private.route_notification_event() is
  'v0.53 unified in-app/push route with per-recipient cooldown and deterministic dedupe keys';

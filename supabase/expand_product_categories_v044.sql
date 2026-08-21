-- Baby Smart List v0.44: allow all existing dashboard baby-product categories.
-- This script is safe to rerun and does not delete existing products or prices.

begin;

create or replace function private.normalize_baby_retail_price_inputs()
returns trigger
language plpgsql
security invoker
set search_path = public, private
as $$
begin
  if new.package_quantity is not null and new.package_quantity <= 0 then
    new.package_quantity := null;
  end if;
  if new.promo_price is not null and new.promo_price <= 0 then
    new.promo_price := null;
  end if;
  if new.promo_min_quantity is not null and new.promo_min_quantity <= 0 then
    new.promo_min_quantity := null;
  end if;
  if new.promo_total_price is not null and new.promo_total_price <= 0 then
    new.promo_total_price := null;
  end if;
  return new;
end;
$$;

revoke all on function private.normalize_baby_retail_price_inputs() from public, anon, authenticated;
grant execute on function private.normalize_baby_retail_price_inputs() to service_role;

drop trigger if exists normalize_baby_retail_price_inputs_trigger on public.baby_retail_prices;
create trigger normalize_baby_retail_price_inputs_trigger
before insert or update of package_quantity, promo_price, promo_min_quantity, promo_total_price
on public.baby_retail_prices
for each row
execute function private.normalize_baby_retail_price_inputs();

alter table public.baby_retail_prices
  drop constraint if exists baby_retail_prices_need_key_check;

alter table public.baby_retail_prices
  add constraint baby_retail_prices_need_key_check
  check (need_key in (
    'diapers', 'wipes', 'formula', 'diaper_cream', 'changing_pads',
    'diaper_bags', 'baby_wash', 'bath_oil', 'body_cream', 'baby_laundry'
  ));

create or replace view public.baby_normalized_prices
with (security_invoker = true)
as
select
  p.chain_id,
  p.branch_code,
  p.barcode,
  p.need_key,
  p.dimension_type,
  p.dimension_value,
  p.brand,
  p.product_name,
  p.package_quantity,
  p.package_unit,
  p.regular_price,
  p.promo_price,
  offer.effective_price,
  p.promo_description,
  p.promo_start_at,
  p.promo_end_at,
  p.requires_club,
  p.source_updated_at,
  p.last_seen_at,
  p.raw_source,
  n.canonical_package_quantity,
  n.canonical_package_unit,
  n.quantity_source,
  n.raw_quantity_conflict,
  n.unresolved_quantity_conflict,
  case
    when p.need_key = 'formula'
      and n.canonical_package_unit = 'גרם'
      and n.canonical_package_quantity > 0
      then round(offer.effective_price * 100.0 / n.canonical_package_quantity, 4)
    when p.need_key in ('diaper_cream', 'body_cream')
      and n.canonical_package_unit = 'גרם'
      and n.canonical_package_quantity > 0
      then round(offer.effective_price * 100.0 / n.canonical_package_quantity, 4)
    when p.need_key in ('baby_wash', 'bath_oil', 'baby_laundry')
      and n.canonical_package_unit = 'מ״ל'
      and n.canonical_package_quantity > 0
      then round(offer.effective_price * 100.0 / n.canonical_package_quantity, 4)
    when p.need_key in ('diapers', 'wipes', 'changing_pads', 'diaper_bags')
      and n.canonical_package_unit = 'יחידות'
      and n.canonical_package_quantity > 0
      then round(offer.effective_price / n.canonical_package_quantity, 4)
    when p.need_key not in ('diapers', 'wipes', 'formula')
      then offer.effective_price
    else null::numeric
  end as normalized_unit_price,
  case
    when p.need_key in ('formula', 'diaper_cream', 'body_cream')
      and n.canonical_package_unit = 'גרם'
      and n.canonical_package_quantity > 0
      then '₪ ל-100 גרם'
    when p.need_key in ('baby_wash', 'bath_oil', 'baby_laundry')
      and n.canonical_package_unit = 'מ״ל'
      and n.canonical_package_quantity > 0
      then '₪ ל-100 מ״ל'
    when p.need_key in ('diapers', 'wipes', 'changing_pads', 'diaper_bags')
      and n.canonical_package_unit = 'יחידות'
      and n.canonical_package_quantity > 0
      then '₪ ליחידה'
    when p.need_key not in ('diapers', 'wipes', 'formula')
      then '₪ לאריזה'
    else null::text
  end as normalized_unit_label,
  p.promo_min_quantity,
  p.promo_total_price
from public.baby_retail_prices p
left join public.baby_product_quantity_normalization n using (barcode)
cross join lateral (
  select case
    when p.promo_price is not null
      and p.promo_price > 0
      and p.promo_price < p.regular_price
      and (p.promo_start_at is null or p.promo_start_at <= now())
      and (p.promo_end_at is null or p.promo_end_at >= now())
      then p.promo_price
    else p.regular_price
  end as effective_price
) offer;

grant select on public.baby_normalized_prices to authenticated, service_role;

commit;

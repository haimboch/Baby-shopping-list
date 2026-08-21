-- Baby Smart List v0.46 — MVP readiness and trustworthy basket data.
-- Safe to rerun. This keeps users' products and history intact.

begin;

-- Formula is never substituted automatically in a basket.
update public.products
set allow_alternatives = false
where need_key = 'formula'
  and allow_alternatives is distinct from false;

-- Only active, verified baby products are counted. The view is safe for the
-- authenticated client: it respects the caller's RLS policies.
create or replace view public.baby_mvp_data_health_v046
with (security_invoker = true)
as
with active_catalog as (
  select barcode, need_key, image_url, image_checked_at
  from public.baby_product_catalog
  where active = true
),
catalog_stats as (
  select
    count(*)::integer as active_catalog_products,
    count(*) filter (where image_url is not null)::integer as catalog_products_with_image,
    count(*) filter (where image_checked_at is not null)::integer as catalog_products_checked,
    round(
      100.0 * count(*) filter (where image_url is not null)
      / nullif(count(*), 0),
      1
    ) as catalog_image_coverage
  from active_catalog
),
price_stats as (
  select
    p.chain_id,
    max(p.last_seen_at) as latest_price_at,
    count(*) filter (
      where p.last_seen_at >= now() - interval '36 hours'
    )::integer as fresh_price_rows,
    count(distinct p.barcode)::integer as known_baby_barcodes
  from public.baby_retail_prices p
  inner join active_catalog c
    on c.barcode = p.barcode
    and c.need_key = p.need_key
  where p.regular_price is not null
    and p.regular_price > 0
  group by p.chain_id
)
select
  s.chain_id,
  s.latest_price_at,
  s.fresh_price_rows,
  s.known_baby_barcodes,
  c.active_catalog_products,
  c.catalog_products_with_image,
  c.catalog_products_checked,
  c.catalog_image_coverage
from price_stats s
cross join catalog_stats c;

grant select on public.baby_mvp_data_health_v046 to authenticated, service_role;

commit;

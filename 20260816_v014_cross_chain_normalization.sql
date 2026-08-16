-- v0.14 Cross-chain quantity normalization
-- Applied to Supabase project baby-shopping on 2026-08-16.
-- Keeps retailer raw data untouched and derives canonical package metadata.

create or replace view public.baby_product_quantity_normalization
with (security_invoker = true)
as
with observed as (
    select
        p.barcode,
        min(p.need_key) as need_key,
        min(nullif(p.brand, '')) as observed_brand,
        min(nullif(p.product_name, '')) as observed_product_name,
        count(distinct p.chain_id) as retailer_count,
        count(distinct p.chain_id) filter (where p.package_quantity > 0) as quantity_retailer_count,
        count(distinct p.package_quantity) filter (where p.package_quantity > 0) as distinct_quantity_count,
        count(distinct p.package_unit) filter (
            where p.package_unit is not null and btrim(p.package_unit) <> ''
        ) as distinct_unit_count,
        min(p.package_quantity) filter (where p.package_quantity > 0) as single_quantity,
        min(p.package_unit) filter (
            where p.package_unit is not null and btrim(p.package_unit) <> ''
        ) as single_unit,
        array_agg(distinct p.package_quantity order by p.package_quantity)
            filter (where p.package_quantity > 0) as observed_quantities,
        array_agg(distinct p.package_unit order by p.package_unit)
            filter (where p.package_unit is not null and btrim(p.package_unit) <> '') as observed_units
    from public.baby_retail_prices p
    group by p.barcode
),
catalog as (
    select
        c.barcode,
        c.need_key as catalog_need_key,
        nullif(c.brand, '') as catalog_brand,
        nullif(c.product_name, '') as catalog_product_name,
        c.package_quantity as catalog_package_quantity,
        nullif(c.package_unit, '') as catalog_package_unit,
        c.source_name as catalog_source_name,
        c.verified_at as catalog_verified_at
    from public.baby_product_catalog c
)
select
    o.barcode,
    coalesce(c.catalog_need_key, o.need_key) as need_key,
    coalesce(c.catalog_brand, o.observed_brand) as brand,
    coalesce(c.catalog_product_name, o.observed_product_name) as product_name,
    case
        when c.catalog_package_quantity is not null and c.catalog_package_quantity > 0
            then c.catalog_package_quantity
        when o.distinct_quantity_count = 1
            then o.single_quantity
        else null
    end as canonical_package_quantity,
    case
        when c.catalog_package_quantity is not null
             and c.catalog_package_quantity > 0
             and c.catalog_package_unit is not null
            then c.catalog_package_unit
        when o.distinct_unit_count = 1
            then o.single_unit
        else null
    end as canonical_package_unit,
    case
        when c.catalog_package_quantity is not null and c.catalog_package_quantity > 0
            then 'catalog'
        when o.distinct_quantity_count = 1 and o.quantity_retailer_count >= 2
            then 'retailer_consensus'
        when o.distinct_quantity_count = 1 and o.quantity_retailer_count = 1
            then 'single_retailer'
        when o.distinct_quantity_count > 1
            then 'conflict'
        else 'unknown'
    end as quantity_source,
    (o.distinct_quantity_count > 1) as raw_quantity_conflict,
    (
        (c.catalog_package_quantity is null or c.catalog_package_quantity <= 0)
        and o.distinct_quantity_count > 1
    ) as unresolved_quantity_conflict,
    o.retailer_count,
    o.quantity_retailer_count,
    o.observed_quantities,
    o.observed_units,
    c.catalog_package_quantity,
    c.catalog_package_unit,
    c.catalog_source_name,
    c.catalog_verified_at
from observed o
left join catalog c using (barcode);

create or replace view public.baby_normalized_prices
with (security_invoker = true)
as
select
    p.*,
    n.canonical_package_quantity,
    n.canonical_package_unit,
    n.quantity_source,
    n.raw_quantity_conflict,
    n.unresolved_quantity_conflict,
    case
        when n.canonical_package_quantity is null or n.canonical_package_quantity <= 0 then null
        when p.need_key = 'formula' and n.canonical_package_unit = 'גרם'
            then round((p.effective_price * 100.0 / n.canonical_package_quantity)::numeric, 4)
        when p.need_key in ('diapers', 'wipes') and n.canonical_package_unit = 'יחידות'
            then round((p.effective_price / n.canonical_package_quantity)::numeric, 4)
        else null
    end as normalized_unit_price,
    case
        when p.need_key = 'formula' and n.canonical_package_unit = 'גרם'
            then '₪ ל-100 גרם'
        when p.need_key in ('diapers', 'wipes') and n.canonical_package_unit = 'יחידות'
            then '₪ ליחידה'
        else null
    end as normalized_unit_label
from public.baby_retail_prices p
left join public.baby_product_quantity_normalization n using (barcode);

create or replace view public.baby_chain_product_best_prices
with (security_invoker = true)
as
with ranked as (
    select
        p.*,
        row_number() over (
            partition by p.chain_id, p.barcode
            order by p.effective_price asc,
                     p.source_updated_at desc nulls last,
                     p.branch_code
        ) as rn
    from public.baby_normalized_prices p
)
select
    chain_id,
    branch_code,
    barcode,
    need_key,
    brand,
    product_name,
    effective_price as best_price,
    canonical_package_quantity,
    canonical_package_unit,
    normalized_unit_price,
    normalized_unit_label,
    quantity_source,
    raw_quantity_conflict,
    unresolved_quantity_conflict,
    source_updated_at,
    last_seen_at
from ranked
where rn = 1;

grant select on public.baby_product_quantity_normalization
    to anon, authenticated, service_role;
grant select on public.baby_normalized_prices
    to anon, authenticated, service_role;
grant select on public.baby_chain_product_best_prices
    to anon, authenticated, service_role;

-- Baby Shopping v0.30.1
-- Fix ambiguous stock_status reference inside inventory RPC functions.

create or replace function public.set_product_inventory(
  p_product_id uuid,
  p_quantity numeric,
  p_unit_label text default null,
  p_change_kind text default 'correction'
)
returns table (
  product_id uuid,
  quantity numeric,
  unit_label text,
  stock_status public.stock_status
)
language plpgsql
security definer
set search_path = public, private, auth, pg_temp
as $$
declare
  v_product public.products%rowtype;
  v_kind text;
  v_unit text;
begin
  if p_quantity is null or p_quantity < 0 then
    raise exception 'inventory_quantity_must_be_non_negative';
  end if;

  if p_change_kind not in ('initial','correction') then
    raise exception 'invalid_set_inventory_change_kind';
  end if;

  select p.*
  into v_product
  from public.products as p
  where p.id = p_product_id
  for update;

  if not found then
    raise exception 'product_not_found';
  end if;

  if not private.is_household_member(v_product.household_id) then
    raise exception 'not_household_member';
  end if;

  v_kind := case
    when v_product.quantity is null then 'initial'
    else p_change_kind
  end;

  v_unit := coalesce(
    nullif(btrim(p_unit_label), ''),
    nullif(btrim(v_product.unit_label), ''),
    public.inventory_default_unit(v_product.need_key)
  );

  if v_product.quantity is distinct from p_quantity
     or v_product.unit_label is distinct from v_unit then

    update public.products as p
    set
      quantity = p_quantity,
      unit_label = v_unit,
      stock_status = case
        when p_quantity = 0 then 'out'::public.stock_status
        when v_product.quantity is null then 'ok'::public.stock_status
        else p.stock_status
      end,
      updated_by = auth.uid(),
      updated_at = now()
    where p.id = p_product_id;

    if v_product.quantity is distinct from p_quantity then
      insert into public.inventory_snapshots (
        household_id, product_id, quantity_before, quantity_after,
        delta, change_kind, created_by
      )
      values (
        v_product.household_id,
        v_product.id,
        v_product.quantity,
        p_quantity,
        case when v_product.quantity is null then 0
             else p_quantity - v_product.quantity end,
        v_kind,
        auth.uid()
      );
    end if;
  end if;

  return query
  select p.id, p.quantity, p.unit_label, p.stock_status
  from public.products as p
  where p.id = p_product_id;
end;
$$;

create or replace function public.change_product_inventory(
  p_product_id uuid,
  p_delta numeric,
  p_change_kind text
)
returns table (
  product_id uuid,
  quantity numeric,
  unit_label text,
  stock_status public.stock_status
)
language plpgsql
security definer
set search_path = public, private, auth, pg_temp
as $$
declare
  v_product public.products%rowtype;
  v_before numeric;
  v_after numeric;
  v_kind text;
  v_unit text;
begin
  if p_delta is null or p_delta = 0 then
    raise exception 'inventory_delta_must_be_non_zero';
  end if;

  if p_change_kind not in ('consumption','restock','purchase') then
    raise exception 'invalid_inventory_change_kind';
  end if;

  if p_change_kind = 'consumption' and p_delta >= 0 then
    raise exception 'consumption_delta_must_be_negative';
  end if;

  if p_change_kind in ('restock','purchase') and p_delta <= 0 then
    raise exception 'restock_delta_must_be_positive';
  end if;

  select p.*
  into v_product
  from public.products as p
  where p.id = p_product_id
  for update;

  if not found then
    raise exception 'product_not_found';
  end if;

  if not private.is_household_member(v_product.household_id) then
    raise exception 'not_household_member';
  end if;

  if v_product.quantity is null and p_delta < 0 then
    raise exception 'inventory_not_initialized';
  end if;

  v_before := v_product.quantity;
  v_after := coalesce(v_before, 0) + p_delta;

  if v_after < 0 then
    raise exception 'inventory_cannot_be_negative';
  end if;

  v_kind := case when v_before is null then 'initial'
                 else p_change_kind end;

  v_unit := coalesce(
    nullif(btrim(v_product.unit_label), ''),
    public.inventory_default_unit(v_product.need_key)
  );

  update public.products as p
  set
    quantity = v_after,
    unit_label = v_unit,
    stock_status = case
      when v_after = 0 then 'out'::public.stock_status
      when p_change_kind in ('restock','purchase') or v_before is null
        then 'ok'::public.stock_status
      else p.stock_status
    end,
    updated_by = auth.uid(),
    updated_at = now()
  where p.id = p_product_id;

  insert into public.inventory_snapshots (
    household_id, product_id, quantity_before, quantity_after,
    delta, change_kind, created_by
  )
  values (
    v_product.household_id,
    v_product.id,
    v_before,
    v_after,
    case when v_before is null then 0 else p_delta end,
    v_kind,
    auth.uid()
  );

  return query
  select p.id, p.quantity, p.unit_label, p.stock_status
  from public.products as p
  where p.id = p_product_id;
end;
$$;

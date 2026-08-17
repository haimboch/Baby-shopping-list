-- Baby Shopping v0.31
-- Pilot onboarding: atomic household bootstrap + selected starter products.

create or replace function public.create_pilot_household_v031(
  p_name text,
  p_city text,
  p_latitude double precision,
  p_longitude double precision,
  p_radius integer,
  p_products jsonb
)
returns uuid
language plpgsql
security definer
set search_path = public, private, auth, pg_temp
as $$
declare
  v_user uuid := auth.uid();
  v_household uuid;
  v_item jsonb;
  v_key text;
  v_qty numeric;
  v_dim text;
  v_product_id uuid;
  v_selected_count integer;
begin
  if v_user is null then
    raise exception 'authentication_required';
  end if;

  if exists (
    select 1 from public.household_members hm where hm.user_id = v_user
  ) then
    raise exception 'user_already_has_household';
  end if;

  if nullif(btrim(p_name), '') is null then
    raise exception 'household_name_required';
  end if;

  if nullif(btrim(p_city), '') is null then
    raise exception 'city_required';
  end if;

  if p_latitude is null or p_longitude is null
     or p_latitude < 29 or p_latitude > 34
     or p_longitude < 34 or p_longitude > 36.5 then
    raise exception 'valid_israel_location_required';
  end if;

  if p_radius not in (5,10,15,20,30) then
    raise exception 'invalid_search_radius';
  end if;

  if p_products is null or jsonb_typeof(p_products) <> 'array' then
    raise exception 'starter_products_array_required';
  end if;

  v_selected_count := jsonb_array_length(p_products);
  if v_selected_count < 1 or v_selected_count > 10 then
    raise exception 'select_between_1_and_10_starter_products';
  end if;

  -- households_add_creator and households_seed_starter_products already run
  -- after this insert. We deliberately reuse those stable triggers, then trim
  -- and configure the starter set selected by the onboarding wizard.
  insert into public.households (
    name, created_by, city, latitude, longitude, search_radius_km
  ) values (
    btrim(p_name), v_user, btrim(p_city), p_latitude, p_longitude, p_radius
  )
  returning id into v_household;

  -- Reject unknown/duplicate starter keys before changing the seeded set.
  if exists (
    select 1
    from jsonb_array_elements(p_products) e
    where coalesce(e->>'need_key','') not in (
      'diapers','wipes','formula','diaper_cream','changing_pads',
      'diaper_bags','bath_oil','baby_wash','body_cream','baby_laundry'
    )
  ) then
    raise exception 'unsupported_starter_product';
  end if;

  if (
    select count(*)
    from (
      select distinct e->>'need_key' as need_key
      from jsonb_array_elements(p_products) e
    ) x
  ) <> v_selected_count then
    raise exception 'duplicate_starter_product';
  end if;

  delete from public.products p
  where p.household_id = v_household
    and not exists (
      select 1
      from jsonb_array_elements(p_products) e
      where e->>'need_key' = p.need_key
    );

  for v_item in select * from jsonb_array_elements(p_products)
  loop
    v_key := v_item->>'need_key';
    v_dim := nullif(btrim(v_item->>'dimension_value'), '');
    v_qty := case
      when v_item ? 'quantity' and jsonb_typeof(v_item->'quantity') = 'number'
      then (v_item->>'quantity')::numeric
      else null
    end;

    if v_qty is not null and v_qty < 0 then
      raise exception 'starter_quantity_must_be_non_negative';
    end if;

    if v_key = 'diapers' then
      if v_dim is null then v_dim := '1'; end if;
      if v_dim not in ('0','1','2','3','4','5','6','7','8') then
        raise exception 'invalid_diaper_size';
      end if;
    elsif v_key = 'formula' then
      if v_dim is null then v_dim := '1'; end if;
      if v_dim not in ('1','2','3') then
        raise exception 'invalid_formula_stage';
      end if;
    else
      v_dim := null;
    end if;

    update public.products p
    set
      dimension_type = case
        when v_key = 'diapers' then 'size'
        when v_key = 'formula' then 'stage'
        else 'none'
      end,
      dimension_value = v_dim,
      comparison_mode = case when v_key = 'formula' then '100g' else 'unit' end,
      allow_alternatives = true,
      unit_label = case
        when v_key = 'formula' then 'קופסאות'
        when v_key in ('diapers','wipes') then 'חבילות'
        else 'יחידות'
      end,
      quantity = v_qty,
      stock_status = case
        when v_qty = 0 then 'out'::public.stock_status
        else 'ok'::public.stock_status
      end,
      updated_by = v_user,
      updated_at = now()
    where p.household_id = v_household
      and p.need_key = v_key
    returning p.id into v_product_id;

    if v_product_id is null then
      raise exception 'starter_seed_missing:%', v_key;
    end if;

    if v_qty is not null then
      insert into public.inventory_snapshots (
        household_id, product_id, quantity_before, quantity_after,
        delta, change_kind, created_by
      ) values (
        v_household, v_product_id, null, v_qty, 0, 'initial', v_user
      );
    end if;

    v_product_id := null;
  end loop;

  return v_household;
end;
$$;

revoke all on function public.create_pilot_household_v031(
  text,text,double precision,double precision,integer,jsonb
) from public, anon;

grant execute on function public.create_pilot_household_v031(
  text,text,double precision,double precision,integer,jsonb
) to authenticated;

-- Private household package photos for exact products missing an official image.

alter table public.products
  add column if not exists package_image_path text;

insert into storage.buckets(id,name,public,file_size_limit,allowed_mime_types)
values(
  'baby-package-images',
  'baby-package-images',
  false,
  5242880,
  array['image/jpeg','image/png','image/webp']::text[]
)
on conflict(id) do update set
  public = excluded.public,
  file_size_limit = excluded.file_size_limit,
  allowed_mime_types = excluded.allowed_mime_types;

drop policy if exists baby_package_images_select_household on storage.objects;
create policy baby_package_images_select_household
on storage.objects for select to authenticated
using (
  bucket_id = 'baby-package-images'
  and exists (
    select 1 from public.household_members hm
    where hm.household_id::text = (storage.foldername(name))[1]
      and hm.user_id = (select auth.uid())
  )
);

drop policy if exists baby_package_images_insert_household on storage.objects;
create policy baby_package_images_insert_household
on storage.objects for insert to authenticated
with check (
  bucket_id = 'baby-package-images'
  and exists (
    select 1 from public.household_members hm
    where hm.household_id::text = (storage.foldername(name))[1]
      and hm.user_id = (select auth.uid())
  )
);

drop policy if exists baby_package_images_update_household on storage.objects;
create policy baby_package_images_update_household
on storage.objects for update to authenticated
using (
  bucket_id = 'baby-package-images'
  and exists (
    select 1 from public.household_members hm
    where hm.household_id::text = (storage.foldername(name))[1]
      and hm.user_id = (select auth.uid())
  )
)
with check (
  bucket_id = 'baby-package-images'
  and exists (
    select 1 from public.household_members hm
    where hm.household_id::text = (storage.foldername(name))[1]
      and hm.user_id = (select auth.uid())
  )
);

comment on column public.products.package_image_path is
  'Private Supabase Storage path for a household package photo; never a public URL';

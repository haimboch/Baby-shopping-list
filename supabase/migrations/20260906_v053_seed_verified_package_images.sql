-- Exact package photos verified against Super-Pharm's official product CDN.
-- The barcode is part of each immutable image path; no name-based matching.

update public.baby_product_catalog
set image_url = case barcode
      when '7290003026319' then 'https://superpharmstorage.blob.core.windows.net/hybris/products/desktop/large/7290003026319.jpg'
      when '7290019074502' then 'https://superpharmstorage.blob.core.windows.net/hybris/products/desktop/large/7290019074502.jpg'
      when '7290121602402' then 'https://superpharmstorage.blob.core.windows.net/hybris/products/desktop/large/7290121602402.jpg'
      when '8712400802536' then 'https://superpharmstorage.blob.core.windows.net/hybris/products/desktop/large/8712400802536.jpg'
    end,
    image_source = 'Super-Pharm official CDN · verified barcode',
    image_checked_at = now()
where barcode in (
  '7290003026319',
  '7290019074502',
  '7290121602402',
  '8712400802536'
);

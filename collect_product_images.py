"""Collect verified package photos independently from the price workers."""

from __future__ import annotations

import json
import os

from baby_worker.product_images import ProductImageEnricher, image_limits_from_environment
from baby_worker.supabase_rest import SupabaseREST


def main() -> None:
    image_limit, cheapersal_limit = image_limits_from_environment()
    stats = ProductImageEnricher(
        db=SupabaseREST(),
        api_key=os.environ.get("CHEAPERSAL_API_KEY", ""),
        limit=image_limit,
        cheapersal_limit=cheapersal_limit,
    ).enrich_missing_images()
    print("PRODUCT_IMAGE_RESULT=" + json.dumps(stats, ensure_ascii=False))
    for error in stats["errors"][:20]:
        print(f"::warning title=Product image lookup::{error}")


if __name__ == "__main__":
    main()

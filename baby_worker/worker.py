from __future__ import annotations
import argparse
import asyncio
import json
import os
import sys
import traceback
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from .xmlfeeds import file_kind, parse_price_rows, parse_promotions, parse_stores
from .supabase_rest import SupabaseREST
from .enrichment import MetadataEnricher

SCRAPER_TO_DB = {
    "SUPER_PHARM": "super_pharm",
    "RAMI_LEVY": "rami_levy",
    "YOHANANOF": "yochananof",
    "SHUFERSAL": "shufersal",  # split to Be later by subchain
}
CHAIN_TO_SCRAPER = {
    "super_pharm": "SUPER_PHARM",
    "rami_levy": "RAMI_LEVY",
    "yochananof": "YOHANANOF",
    "shufersal": "SHUFERSAL",
    "be": "SHUFERSAL",
}

def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()

def csv_env(name: str, default: str) -> list[str]:
    return [x.strip() for x in os.environ.get(name, default).split(",") if x.strip()]

def be_subchains() -> set[str]:
    return {x.lstrip("0") or "0" for x in csv_env("BE_SUBCHAIN_IDS", "005,5")}

def resolved_chain(source_scraper: str, subchain_id: str | None, product_or_store_name: str | None = None) -> str:
    if source_scraper != "SHUFERSAL":
        return SCRAPER_TO_DB[source_scraper]
    normalized = (str(subchain_id).lstrip("0") or "0") if subchain_id not in (None, "") else None
    if normalized and normalized in be_subchains():
        return "be"
    if product_or_store_name and str(product_or_store_name).strip().lower().startswith("be "):
        return "be"
    return "shufersal"

def normalize_timestamp(value: str | None) -> str | None:
    if not value:
        return None
    s = str(value).strip()
    # Keep valid-ish ISO as-is. DB can parse it.
    if "T" in s:
        return s
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y%m%d%H%M", "%Y%m%d%H%M%S", "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            pass
    return None

def merge_prices_and_promos(price_rows: list[dict[str, Any]], promos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pmap = {(r["source_name"], r["branch_code"], r["barcode"]): r for r in promos}
    out = []
    for r in price_rows:
        promo = pmap.get((r["source_name"], r["branch_code"], r["barcode"]))
        row = dict(r)
        if promo:
            row.update({
                "promo_price": promo.get("promo_price"),
                "promo_description": promo.get("promo_description"),
                "promo_start_at": normalize_timestamp(promo.get("promo_start_at")),
                "promo_end_at": normalize_timestamp(promo.get("promo_end_at")),
                "requires_club": bool(promo.get("requires_club")),
            })
            raw = dict(row.get("raw_source") or {})
            raw["promo"] = promo.get("raw_promo")
            row["raw_source"] = raw
        promo_price = row.get("promo_price")
        regular = float(row["regular_price"])
        row["effective_price"] = float(promo_price) if promo_price and 0 < float(promo_price) < regular else regular
        row["source_updated_at"] = normalize_timestamp(row.get("source_updated_at"))
        row["last_seen_at"] = utcnow()
        out.append(row)
    return out

def to_db_price(row: dict[str, Any]) -> dict[str, Any]:
    chain_id = resolved_chain(row["source_name"], row.get("subchain_id"), row.get("product_name"))
    return {
        "chain_id": chain_id,
        "branch_code": str(row["branch_code"]),
        "barcode": str(row["barcode"]),
        "need_key": row["need_key"],
        "dimension_type": row["dimension_type"],
        "dimension_value": row.get("dimension_value"),
        "brand": row.get("brand"),
        "product_name": row.get("product_name"),
        "package_quantity": row.get("package_quantity"),
        "package_unit": row.get("package_unit"),
        "regular_price": row["regular_price"],
        "promo_price": row.get("promo_price"),
        # effective_price is a GENERATED ALWAYS column in Supabase/Postgres.
        # Do not send it on INSERT/UPSERT; the database calculates it automatically.
        "promo_description": row.get("promo_description"),
        "promo_start_at": row.get("promo_start_at"),
        "promo_end_at": row.get("promo_end_at"),
        "requires_club": bool(row.get("requires_club")),
        "source_updated_at": row.get("source_updated_at"),
        "last_seen_at": row.get("last_seen_at") or utcnow(),
        "raw_source": row.get("raw_source"),
    }

def to_db_branch(row: dict[str, Any]) -> dict[str, Any]:
    chain_id = resolved_chain(row["source_name"], row.get("subchain_id"), row.get("branch_name"))
    return {
        "chain_id": chain_id,
        "branch_code": str(row["branch_code"]),
        "branch_name": row.get("branch_name"),
        "city": row.get("city"),
        "address": row.get("address"),
        "active": True,
        "last_seen_at": utcnow(),
    }

async def collect_source(scraper_name: str, file_limit: int | None) -> dict[str, Any]:
    # Set this BEFORE importing the scraper package. Some package configuration
    # is read during import.
    os.environ["ENABLED_FILE_TYPES"] = "STORE_FILE,PRICE_FILE,PRICE_FULL_FILE,PROMO_FILE,PROMO_FULL_FILE"

    try:
        from il_supermarket_scarper import ScarpingTask
        from il_supermarket_scarper.utils import _now, Logger
    except ImportError as e:
        raise RuntimeError("Missing il-supermarket-scraper. Run: pip install -r requirements.txt") from e

    Logger.set_logging_level(os.environ.get("SCRAPER_LOG_LEVEL", "WARNING"))

    scraper = ScarpingTask(
        output_configuration={"output_mode": "queue", "queue_type": "memory"},
        status_configuration={"database_type": "json", "base_path": "status_logs"},
        multiprocessing=1,
        enabled_scrapers=[scraper_name],
    )
    scraper.start(limit=file_limit, when_date=_now())

    price_rows: list[dict[str, Any]] = []
    promo_rows: list[dict[str, Any]] = []
    store_rows: list[dict[str, Any]] = []
    files_seen = 0
    errors: list[str] = []
    kind_counts: dict[str, int] = defaultdict(int)
    sample_files: list[str] = []

    try:
        for name, file_output in scraper.consume().items():
            async for msg in file_output.queue_handler.get_all_messages():
                file_name = msg.get("file_name") or ""
                content = msg.get("file_content") or b""

                if not file_name or not content:
                    errors.append(
                        f"queue message missing file_name/file_content; keys={list(msg.keys())}"
                    )
                    continue

                files_seen += 1
                kind = file_kind(file_name)
                kind_counts[kind] += 1
                if len(sample_files) < 12:
                    sample_files.append(file_name)

                try:
                    if kind == "stores":
                        store_rows.extend(parse_stores(content, scraper_name, file_name))
                    elif kind in ("price_full", "price"):
                        # Incremental PRICE_FILE and full PRICE_FULL_FILE use the
                        # same item fields for the data we need.
                        price_rows.extend(parse_price_rows(content, scraper_name, file_name))
                    elif kind in ("promo_full", "promo"):
                        promo_rows.extend(parse_promotions(content, scraper_name, file_name))
                    else:
                        errors.append(f"unrecognized file kind: {file_name}")
                except Exception as exc:
                    errors.append(f"{file_name}: {type(exc).__name__}: {exc}")
    finally:
        try:
            scraper.stop()
        except Exception:
            pass
        scraper.join()

    print(
        f"🔎 {scraper_name} file kinds: {dict(kind_counts)} | "
        f"sample files: {sample_files[:5]}"
    )

    return {
        "files_seen": files_seen,
        "price_rows": price_rows,
        "promo_rows": promo_rows,
        "store_rows": store_rows,
        "errors": errors,
        "kind_counts": dict(kind_counts),
        "sample_files": sample_files,
    }

async def run_chain(scraper_name: str, db: SupabaseREST | None, dry_run: bool, file_limit: int | None, enricher: MetadataEnricher | None = None):
    source_db_name = SCRAPER_TO_DB[scraper_name]
    run_id = None
    if db and not dry_run:
        run = db.insert_returning("feed_ingestion_runs", {
            "chain_id": source_db_name,
            "status": "running",
            "details": {"scraper": scraper_name},
        })
        run_id = run.get("id")

    try:
        data = await collect_source(scraper_name, file_limit)
        merged = merge_prices_and_promos(data["price_rows"], data["promo_rows"])
        prices = [to_db_price(x) for x in merged]

        # The same branch+barcode can appear more than once across incremental
        # PRICE files. PostgreSQL cannot UPSERT the same conflict key twice in
        # a single INSERT ... ON CONFLICT statement, so collapse duplicates
        # before sending a batch to Supabase. Last row wins.
        price_map = {
            (p["chain_id"], p["branch_code"], p["barcode"]): p
            for p in prices
        }
        duplicate_price_rows = len(prices) - len(price_map)
        prices = list(price_map.values())

        # One-time metadata enrichment for previously unknown barcodes.
        # Prices still come from the retailer transparency files. Cheapersal is
        # used only to fill persistent catalog metadata such as full name,
        # brand, size/stage and package quantity.
        enrichment_stats = {"attempted": 0, "catalog_saved": 0, "skipped": 0, "errors": []}
        if enricher and db and not dry_run and prices:
            enrichment_stats = enricher.enrich_missing_catalog(prices)

        branches = [to_db_branch(x) for x in data["store_rows"]]
        branch_map = {
            (b["chain_id"], b["branch_code"]): b
            for b in branches
        }
        branches = list(branch_map.values())

        # Ensure a branch FK target exists even if a Stores file wasn't present in this run.
        known = {(b["chain_id"], b["branch_code"]) for b in branches}
        for p in prices:
            key = (p["chain_id"], p["branch_code"])
            if key not in known:
                branches.append({
                    "chain_id": p["chain_id"],
                    "branch_code": p["branch_code"],
                    "branch_name": None, "city": None, "address": None,
                    "active": True, "last_seen_at": utcnow(),
                })
                known.add(key)

        if dry_run:
            print(json.dumps({
                "scraper": scraper_name,
                "files_seen": data["files_seen"],
                "branches": len(branches),
                "baby_prices": len(prices),
                "examples": prices[:5],
                "parse_errors": data["errors"][:5],
            }, ensure_ascii=False, indent=2))
        elif db:
            db.upsert("retail_branches", branches, "chain_id,branch_code")
            db.upsert("baby_retail_prices", prices, "chain_id,branch_code,barcode")
            # Since SHUFERSAL supplies both, mark both successful if rows were actually seen.
            touched = sorted({p["chain_id"] for p in prices} | {b["chain_id"] for b in branches})
            for chain_id in touched:
                db.patch("retail_chains", {"id": chain_id}, {"last_success_at": utcnow()})
            if run_id:
                db.patch("feed_ingestion_runs", {"id": run_id}, {
                    "status": "success",
                    "finished_at": utcnow(),
                    "files_seen": data["files_seen"],
                    "products_seen": len(data["price_rows"]),
                    "baby_products_saved": len(prices),
                    "details": {
                        "scraper": scraper_name,
                        "branches_saved": len(branches),
                        "promo_rows_seen": len(data["promo_rows"]),
                        "duplicate_price_rows_collapsed": duplicate_price_rows,
                        "metadata_enrichment": enrichment_stats,
                        "file_kind_counts": data.get("kind_counts", {}),
                        "sample_files": data.get("sample_files", [])[:12],
                        "parse_errors": data["errors"][:25],
                    },
                })
        print(
            f"✅ {scraper_name}: {len(prices)} unique baby price rows "
            f"from {data['files_seen']} files "
            f"({duplicate_price_rows} duplicate rows collapsed)"
        )
    except Exception as exc:
        if db and run_id and not dry_run:
            try:
                db.patch("feed_ingestion_runs", {"id": run_id}, {
                    "status": "failed",
                    "finished_at": utcnow(),
                    "error_message": f"{type(exc).__name__}: {exc}"[:1500],
                    "details": {"traceback": traceback.format_exc()[-5000:]},
                })
            except Exception:
                pass
        print(f"❌ {scraper_name}: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise

async def async_main(args):
    requested = [x.strip() for x in args.chains.split(",") if x.strip()]
    unsupported = [x for x in requested if x == "hiper_cohen"]
    if unsupported:
        print("⚠️ hiper_cohen is pending exact official feed verification and will be skipped.", file=sys.stderr)

    scraper_names = []
    for chain in requested:
        if chain == "hiper_cohen":
            continue
        scr = CHAIN_TO_SCRAPER.get(chain)
        if not scr:
            raise SystemExit(f"Unknown chain: {chain}")
        if scr not in scraper_names:
            scraper_names.append(scr)

    db = None if args.dry_run else SupabaseREST()
    enricher = None
    if db and not args.dry_run:
        enricher = MetadataEnricher(
            db=db,
            api_key=os.environ.get("CHEAPERSAL_API_KEY", ""),
            limit=int(os.environ.get("ENRICHMENT_LIMIT", "8")),
        )
    for scraper_name in scraper_names:
        await run_chain(scraper_name, db, args.dry_run, args.file_limit, enricher)

def main():
    parser = argparse.ArgumentParser(description="Baby price-transparency worker")
    parser.add_argument(
        "--chains",
        default=os.environ.get("ENABLED_CHAINS", "super_pharm,shufersal,be,rami_levy,yochananof"),
        help="Comma-separated DB chain ids",
    )
    raw_limit = os.environ.get("WORKER_FILE_LIMIT", "").strip()
    parser.add_argument("--file-limit", type=int, default=int(raw_limit) if raw_limit else None)
    parser.add_argument("--dry-run", action="store_true", default=os.environ.get("DRY_RUN") == "1")
    args = parser.parse_args()
    asyncio.run(async_main(args))

if __name__ == "__main__":
    main()

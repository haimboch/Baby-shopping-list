from __future__ import annotations
import os
from typing import Any, Iterable
import requests

class SupabaseREST:
    def __init__(self, url: str | None = None, service_key: str | None = None):
        self.url = (url or os.environ.get("SUPABASE_URL") or "").rstrip("/")
        self.key = service_key or os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or ""
        if not self.url or not self.key:
            raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are required")
        self.base = f"{self.url}/rest/v1"
        self.headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
        }

    def _check(self, r: requests.Response) -> requests.Response:
        if not r.ok:
            body = r.text[:1200]
            raise RuntimeError(f"Supabase {r.status_code}: {body}")
        return r

    def insert_returning(self, table: str, row: dict[str, Any]) -> dict[str, Any]:
        h = {**self.headers, "Prefer": "return=representation"}
        r = requests.post(f"{self.base}/{table}", headers=h, json=row, timeout=45)
        self._check(r)
        data = r.json()
        return data[0] if isinstance(data, list) and data else data

    def upsert(self, table: str, rows: list[dict[str, Any]], on_conflict: str, batch: int = 250):
        if not rows:
            return
        h = {**self.headers, "Prefer": "resolution=merge-duplicates,return=minimal"}
        for i in range(0, len(rows), batch):
            chunk = rows[i:i+batch]
            r = requests.post(
                f"{self.base}/{table}",
                params={"on_conflict": on_conflict},
                headers=h,
                json=chunk,
                timeout=90,
            )
            self._check(r)

    def select(self, table: str, params: dict[str, str]) -> list[dict[str, Any]]:
        r = requests.get(f"{self.base}/{table}", params=params, headers=self.headers, timeout=45)
        self._check(r)
        data = r.json()
        return data if isinstance(data, list) else []

    def patch(self, table: str, filters: dict[str, str], row: dict[str, Any]):
        params = {k: f"eq.{v}" for k, v in filters.items()}
        h = {**self.headers, "Prefer": "return=minimal"}
        r = requests.patch(f"{self.base}/{table}", params=params, headers=h, json=row, timeout=45)
        self._check(r)

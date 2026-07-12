from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Mapping

from .utils import clean_part_description, extract_core_keyword, load_dotenv

EBAY_API_BASE = "https://api.ebay.com"
RETRYABLE_HTTP_STATUS = {429, 500, 502, 503, 504}


class EbayApiError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, fatal: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.fatal = fatal


def request_json(
    url: str,
    *,
    method: str = "GET",
    headers: Mapping[str, str] | None = None,
    data: bytes | None = None,
    max_attempts: int = 3,
) -> dict[str, Any]:
    for attempt in range(1, max(1, max_attempts) + 1):
        req = urllib.request.Request(url, data=data, headers=dict(headers or {}), method=method)
        try:
            with urllib.request.urlopen(req, timeout=180) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in RETRYABLE_HTTP_STATUS and attempt < max_attempts:
                retry_after = (exc.headers or {}).get("Retry-After")
                time.sleep(float(retry_after) if retry_after else float(attempt))
                continue
            detail = exc.read().decode("utf-8", errors="replace")
            raise EbayApiError(f"HTTP {exc.code}: {detail[:1000]}", status=exc.code) from exc
        except urllib.error.URLError as exc:
            if attempt < max_attempts:
                time.sleep(float(attempt))
                continue
            raise EbayApiError(str(exc)) from exc
    raise AssertionError("unreachable")


class EbayClient:
    """Tiny eBay Browse API client for one-record retrieval."""

    def __init__(self, *, client_id: str | None = None, client_secret: str | None = None) -> None:
        load_dotenv()
        self.client_id = client_id or os.getenv("EBAY_CLIENT_ID", "")
        self.client_secret = client_secret or os.getenv("EBAY_CLIENT_SECRET", "")
        self._token = ""
        self._token_expires_at = 0.0

    def access_token(self) -> str:
        if self._token and time.time() < self._token_expires_at:
            return self._token
        if not self.client_id or not self.client_secret:
            raise EbayApiError("Missing EBAY_CLIENT_ID / EBAY_CLIENT_SECRET", fatal=True)
        credentials = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        payload = urllib.parse.urlencode({
            "grant_type": "client_credentials",
            "scope": "https://api.ebay.com/oauth/api_scope",
        }).encode()
        try:
            data = request_json(
                f"{EBAY_API_BASE}/identity/v1/oauth2/token",
                method="POST",
                headers={"Authorization": f"Basic {credentials}", "Content-Type": "application/x-www-form-urlencoded"},
                data=payload,
            )
        except EbayApiError as exc:
            exc.fatal = True
            raise
        self._token = str(data["access_token"])
        self._token_expires_at = time.time() + max(0, int(data.get("expires_in", 7200)) - 60)
        return self._token

    def _browse_get(self, path: str, params: Mapping[str, Any]) -> dict[str, Any]:
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
        return request_json(
            f"{EBAY_API_BASE}{path}?{query}",
            headers={
                "Authorization": f"Bearer {self.access_token()}",
                "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
                "Accept": "application/json",
            },
        )

    def search_by_part_number(self, *, part_number: str, limit: int, category_id: str | None) -> dict[str, Any]:
        items = self._search_items(part_number, limit=limit, category_id=category_id)
        return {"items": items, "searchMeta": {"level": "mpn", "query": part_number, "categoryId": category_id, "resultCount": len(items)}}

    def search_by_compatibility(self, *, query: str, category_id: str, compatibility_filter: str, limit: int) -> dict[str, Any]:
        items = self._search_items(query, limit=limit, category_id=category_id, extra_params={
            "compatibility_filter": compatibility_filter,
            "fieldgroups": "COMPATIBILITY_MATCH_ONLY",
        })
        return {"items": items, "searchMeta": {
            "level": "compat", "query": query, "categoryId": category_id,
            "compatFilter": compatibility_filter, "resultCount": len(items),
        }}

    def search_by_keyword(self, *, vehicle_year: str, vehicle_make: str, vehicle_model: str, part_description: str, limit: int, category_id: str | None) -> dict[str, Any]:
        cleaned = clean_part_description(part_description)
        vehicle_prefix = " ".join(x for x in (vehicle_year, vehicle_make, vehicle_model) if x).strip()
        attempts: list[dict[str, str]] = []
        full_query = f"{vehicle_prefix} {cleaned}".strip()
        attempts.append({"subLevel": "full", "query": full_query})
        items = self._search_items(full_query, limit=limit, category_id=category_id) if full_query else []
        if items:
            return {"items": items, "searchMeta": {"level": "keyword", "subLevel": "full", "query": full_query, "categoryId": category_id, "resultCount": len(items), "attempts": attempts}}

        core = extract_core_keyword(cleaned)
        if core and core.lower() != cleaned.lower():
            core_query = f"{vehicle_prefix} {core}".strip()
            attempts.append({"subLevel": "core", "query": core_query})
            items = self._search_items(core_query, limit=limit, category_id=category_id)
            return {"items": items, "searchMeta": {"level": "keyword", "subLevel": "core", "query": core_query, "categoryId": category_id, "resultCount": len(items), "attempts": attempts}}

        return {"items": [], "searchMeta": {"level": "keyword", "subLevel": "full", "query": full_query, "categoryId": category_id, "resultCount": 0, "attempts": attempts}}

    def _search_items(
        self,
        query: str,
        *,
        limit: int,
        category_id: str | None,
        extra_params: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"q": query, "limit": str(limit)}
        if category_id:
            params["category_ids"] = category_id
        params.update(extra_params or {})
        return self._browse_get("/buy/browse/v1/item_summary/search", params).get("itemSummaries") or []

    def get_item(self, item_id: str) -> dict[str, Any]:
        encoded = urllib.parse.quote(item_id, safe="")
        return self._browse_get(f"/buy/browse/v1/item/{encoded}", {"fieldgroups": "PRODUCT"})

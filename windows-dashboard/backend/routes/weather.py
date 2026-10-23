"""Weather widget backed by wttr.in (no API key required)."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from services.config import config

router = APIRouter(prefix="/api/weather", tags=["weather"])

_cache: dict[str, Any] = {"key": None, "fetched_at": 0.0, "payload": None}
_lock = threading.Lock()


def _location() -> str:
    configured = (config.get("weather.location") or "").strip()
    return configured


def _fetch(location: str) -> dict[str, Any]:
    units = "u" if str(config.get("weather.units", "metric")) == "imperial" else "m"
    query = urllib.parse.quote(location) if location else ""
    url = f"https://wttr.in/{query}?format=j1&{units}"
    request = urllib.request.Request(
        url, headers={"User-Agent": "curl/8.0 (WindowsDashboard)", "Accept": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        raw = response.read().decode("utf-8", errors="replace")
    data = json.loads(raw)

    current = (data.get("current_condition") or [{}])[0]
    area = ((data.get("nearest_area") or [{}])[0])
    area_name = ""
    try:
        area_name = area["areaName"][0]["value"]
        region = area.get("region", [{}])[0].get("value", "")
        country = area.get("country", [{}])[0].get("value", "")
        area_name = ", ".join(part for part in (area_name, region, country) if part)
    except (KeyError, IndexError, TypeError):
        area_name = location or "Unknown location"

    def num(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    forecast = []
    for day in (data.get("weather") or [])[:3]:
        hourly = day.get("hourly") or []
        midday = hourly[len(hourly) // 2] if hourly else {}
        forecast.append(
            {
                "date": day.get("date"),
                "max": num(day.get("maxtempC") if units == "m" else day.get("maxtempF")),
                "min": num(day.get("mintempC") if units == "m" else day.get("mintempF")),
                "avg": num(day.get("avgtempC") if units == "m" else day.get("avgtempF")),
                "chance_of_rain": num((midday.get("chanceofrain") or 0)),
                "description": ((midday.get("weatherDesc") or [{}])[0].get("value") or "").strip(),
                "code": midday.get("weatherCode"),
            }
        )

    payload = {
        "ok": True,
        "location": area_name,
        "units": "imperial" if units == "u" else "metric",
        "temp": num(current.get("temp_C") if units == "m" else current.get("temp_F")),
        "feels_like": num(current.get("FeelsLikeC") if units == "m" else current.get("FeelsLikeF")),
        "humidity": num(current.get("humidity")),
        "pressure": num(current.get("pressure")),
        "wind_kmph": num(current.get("windspeedKmph")),
        "wind_dir": current.get("winddir16Point"),
        "visibility": num(current.get("visibility")),
        "uv_index": num(current.get("uvIndex")),
        "precip_mm": num(current.get("precipMM")),
        "description": ((current.get("weatherDesc") or [{}])[0].get("value") or "").strip(),
        "code": current.get("weatherCode"),
        "observation_time": current.get("localObsDateTime"),
        "forecast": forecast,
        "fetched_at": time.time(),
        "provider": "wttr.in",
    }
    return payload


@router.get("")
def weather(force: bool = Query(False)) -> dict[str, Any]:
    if not config.get("weather.enabled", True):
        return {"ok": False, "disabled": True, "error": "Weather is disabled in settings"}

    location = _location()
    refresh = max(int(config.get("weather.refresh_minutes", 15) or 15), 1) * 60
    key = f"{location}|{config.get('weather.units')}"
    with _lock:
        cached = _cache
        if (
            not force
            and cached["payload"]
            and cached["key"] == key
            and (time.time() - cached["fetched_at"]) < refresh
        ):
            return cached["payload"]

    try:
        payload = _fetch(location)
    except urllib.error.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"wttr.in returned HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError):
        raise HTTPException(status_code=503, detail="Weather service unreachable (offline?)") from None
    except (ValueError, KeyError, IndexError):
        raise HTTPException(status_code=502, detail="Unexpected response from wttr.in") from None

    with _lock:
        _cache.update({"key": key, "fetched_at": time.time(), "payload": payload})
    return payload

"""Network information endpoints: interfaces, ping, ports and speed test."""

from __future__ import annotations

import ipaddress
import json
import re
import socket
import statistics
import threading
import time
import urllib.request
from typing import Any

import psutil
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from services.config import config

router = APIRouter(prefix="/api/network", tags=["network"])

PING_TARGETS = [
    {"name": "Google DNS", "host": "8.8.8.8"},
    {"name": "Cloudflare", "host": "1.1.1.1"},
    {"name": "Google", "host": "google.com"},
    {"name": "Cloudflare DNS", "host": "one.one.one.one"},
]

COMMON_PORTS = [21, 22, 23, 25, 53, 80, 110, 143, 443, 445, 587, 993, 1194, 3000,
                3306, 3389, 5432, 5900, 6379, 8000, 8080, 8443, 8420, 9000, 27017]

_external_cache: dict[str, Any] = {"ip": None, "fetched_at": 0.0, "error": None}
_external_lock = threading.Lock()


class PingRequest(BaseModel):
    host: str = Field(default="8.8.8.8", min_length=1, max_length=255)
    count: int = Field(default=4, ge=1, le=10)


class PortScanRequest(BaseModel):
    host: str = Field(default="127.0.0.1", min_length=1, max_length=255)
    ports: list[int] | None = None
    timeout: float = Field(default=0.35, ge=0.05, le=2.0)


# --------------------------------------------------------------------------- #
# Info
# --------------------------------------------------------------------------- #
def _default_gateway() -> str | None:
    """Best-effort default gateway lookup (no extra dependencies)."""
    try:
        import subprocess

        from services.platform_utils import IS_WINDOWS

        if IS_WINDOWS:
            out = subprocess.run(
                ["ipconfig"], capture_output=True, text=True, timeout=8,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).stdout
            match = re.search(r"Default Gateway[ .]*: *([0-9a-fA-F:.]+)", out)
            if match:
                return match.group(1)
        else:
            out = subprocess.run(["ip", "route"], capture_output=True, text=True, timeout=5).stdout
            for line in out.splitlines():
                if line.startswith("default via "):
                    return line.split()[2]
    except Exception:  # noqa: BLE001
        return None
    return None


def _dns_servers() -> list[str]:
    servers: list[str] = []
    try:
        import subprocess

        from services.platform_utils import IS_MACOS, IS_WINDOWS

        if IS_WINDOWS:
            out = subprocess.run(
                ["ipconfig", "/all"], capture_output=True, text=True, timeout=8,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).stdout
            for match in re.finditer(r"DNS Servers[ .]*: *([0-9a-fA-F:.]+)((?:\n\s+[0-9a-fA-F:.]+)*)", out):
                servers.append(match.group(1))
                for extra in match.group(2).strip().splitlines():
                    extra = extra.strip()
                    if extra:
                        servers.append(extra)
        elif IS_MACOS:
            out = subprocess.run(["scutil", "--dns"], capture_output=True, text=True, timeout=5).stdout
            servers = re.findall(r"nameserver\[\d+\] : (\S+)", out)
        else:
            try:
                with open("/etc/resolv.conf", encoding="utf-8") as fh:
                    for line in fh:
                        if line.startswith("nameserver"):
                            servers.append(line.split()[1])
            except OSError:
                pass
    except Exception:  # noqa: BLE001
        pass
    return list(dict.fromkeys(servers))[:6]


@router.get("/info")
def info() -> dict[str, Any]:
    """Network summary used by the network card."""
    interfaces = []
    try:
        stats = psutil.net_if_stats()
        addrs = psutil.net_if_addrs()
        io = psutil.net_io_counters(pernic=True)
    except (OSError, AttributeError):
        stats, addrs, io = {}, {}, {}

    for name, entries in addrs.items():
        ipv4 = [e.address for e in entries if e.family == socket.AF_INET]
        ipv6 = [e.address for e in entries if e.family == socket.AF_INET6]
        mac = next(
            (e.address for e in entries if getattr(psutil, "AF_LINK", -1) == e.family), None
        )
        if not ipv4 and not ipv6:
            continue
        stat = stats.get(name)
        counters = io.get(name)
        interfaces.append(
            {
                "name": name,
                "ipv4": ipv4,
                "ipv6": [a.split("%")[0] for a in ipv6],
                "mac": mac,
                "up": bool(stat.isup) if stat else False,
                "speed_mbps": int(stat.speed) if stat and stat.speed else None,
                "mtu": int(stat.mtu) if stat and stat.mtu else None,
                "bytes_sent": counters.bytes_sent if counters else 0,
                "bytes_recv": counters.bytes_recv if counters else 0,
            }
        )
    interfaces.sort(key=lambda i: (not i["up"], i["name"]))

    from services.monitor import monitor

    wifi = monitor._wifi()  # noqa: SLF001 - shared detection logic
    return {
        "interfaces": interfaces,
        "wifi": wifi,
        "gateway": _default_gateway(),
        "dns": _dns_servers(),
        "hostname": socket.gethostname(),
        "hostname_fqdn": socket.getfqdn(),
        "external_ip": _external_cache.get("ip"),
        "external_error": _external_cache.get("error"),
        "targets": PING_TARGETS,
    }


def _fetch_external_ip(force: bool = False) -> dict[str, Any]:
    with _external_lock:
        if not force and _external_cache["ip"] and time.time() - _external_cache["fetched_at"] < 300:
            return dict(_external_cache)
    ip, error = None, None
    providers = [
        ("https://api.ipify.org?format=json", "json:ip"),
        ("https://www.cloudflare.com/cdn-cgi/trace", "trace"),
        ("https://ifconfig.me/ip", "text"),
    ]
    for url, mode in providers:
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "WindowsDashboard/1.0"})
            with urllib.request.urlopen(request, timeout=6) as response:
                body = response.read().decode("utf-8", errors="replace").strip()
            if mode == "json:ip":
                ip = str(json.loads(body).get("ip") or "").strip()
            elif mode == "trace":
                for line in body.splitlines():
                    if line.startswith("ip="):
                        ip = line.split("=", 1)[1].strip()
                        break
            else:
                ip = body.splitlines()[0].strip() if body else None
            if ip:
                try:
                    ipaddress.ip_address(ip)
                except ValueError:
                    ip = None
                else:
                    break
        except Exception as exc:  # noqa: BLE001
            error = str(exc)
            continue
    with _external_lock:
        _external_cache.update({"ip": ip, "fetched_at": time.time(), "error": None if ip else error})
        return dict(_external_cache)


@router.get("/external-ip")
def external_ip(force: bool = Query(False)) -> dict[str, Any]:
    return _fetch_external_ip(force=force)


# --------------------------------------------------------------------------- #
# Ping
# --------------------------------------------------------------------------- #
def _parse_ping(output: str) -> dict[str, Any]:
    """Parse ping output from Windows, macOS and Linux into numbers.

    Windows:  ``Reply from 8.8.8.8: bytes=32 time=12ms TTL=117``
    Linux:    ``64 bytes from 127.0.0.1: icmp_seq=1 ttl=127 time=0.021 ms``
    macOS:    ``64 bytes from 1.1.1.1: icmp_seq=0 ttl=57 time=8.123 ms``
    """
    samples: list[float] = []
    for line in output.splitlines():
        lowered = line.lower()
        if not any(token in lowered for token in ("bytes from", "reply from", "ttl=")):
            continue
        # Ignore summary lines such as `..., 0% packet loss, time 2050ms`.
        if "packet loss" in lowered or "transmitted" in lowered:
            continue
        match = re.search(r"time\s*([=<])\s*([\d.,]+)\s*ms", line, re.IGNORECASE)
        if not match:
            continue
        try:
            value = float(match.group(2).replace(",", "."))
        except ValueError:
            continue
        if match.group(1) == "<":  # Windows reports sub-millisecond replies as <1ms
            value = value / 2
        samples.append(round(value, 3))

    sent_actual = None
    received = None
    loss = None
    match = re.search(r"(\d+)\s+packets transmitted,\s*(\d+)\s+(?:packets\s+)?received", output, re.I)
    if match:
        sent_actual, received = int(match.group(1)), int(match.group(2))
    else:
        match = re.search(r"Sent\s*=\s*(\d+),\s*Received\s*=\s*(\d+)", output, re.I)
        if match:
            sent_actual, received = int(match.group(1)), int(match.group(2))
    match = re.search(r"([\d.]+)%\s*(?:packet\s*)?loss", output, re.I)
    if match:
        try:
            loss = float(match.group(1))
        except ValueError:
            loss = None

    # Round-trip summary (Linux/macOS and Windows formats).
    stats_min = stats_avg = stats_max = None
    match = re.search(r"=\s*([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)\s*ms", output)
    if match:
        stats_min, stats_avg, stats_max = (float(match.group(i)) for i in (1, 2, 3))
    else:
        win = re.search(r"Minimum\s*=\s*(\d+)ms,\s*Maximum\s*=\s*(\d+)ms,\s*Average\s*=\s*(\d+)ms",
                        output, re.I)
        if win:
            stats_min, stats_max, stats_avg = (float(win.group(i)) for i in (1, 2, 3))

    # Prefer the tool's own summary (it accounts for packets we couldn't parse).
    if stats_avg is not None:
        minimum, maximum, average = stats_min, stats_max, stats_avg
    elif samples:
        minimum = min(samples)
        maximum = max(samples)
        average = statistics.mean(samples)
    else:
        minimum = maximum = average = None
    jitter = statistics.pstdev(samples) if len(samples) > 1 else 0.0

    return {
        "samples": samples,
        "sent": sent_actual,
        "received": received if received is not None else len(samples),
        "loss": loss,
        "min": round(minimum, 2) if minimum is not None else None,
        "max": round(maximum, 2) if maximum is not None else None,
        "avg": round(average, 2) if average is not None else None,
        "jitter": round(jitter, 2),
    }


@router.post("/ping")
def ping(body: PingRequest) -> dict[str, Any]:
    """Ping a host and return latency statistics."""
    host = body.host.strip()
    if not re.fullmatch(r"[A-Za-z0-9.\-_:]+", host):
        raise HTTPException(status_code=400, detail="Invalid hostname")
    from services.platform_utils import IS_WINDOWS, run

    count = body.count
    cmd = ["ping", "-n", str(count), "-w", "2000", host] if IS_WINDOWS else [
        "ping", "-c", str(count), "-W", "2", host
    ]
    try:
        proc = run(cmd, timeout=count * 2.5 + 5)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Ping failed: {exc}") from exc
    output = (proc.stdout or "") + (proc.stderr or "")
    parsed = _parse_ping(output)
    sent = parsed["sent"] or count
    received = parsed["received"] if parsed["sent"] else min(parsed["received"], sent)
    loss = parsed["loss"]
    if loss is None:
        loss = round(max(0.0, (sent - received) / sent * 100), 1) if sent else 0.0
    return {
        "host": host,
        "sent": sent,
        "received": received,
        "loss": round(loss, 1),
        "min": parsed["min"],
        "max": parsed["max"],
        "avg": parsed["avg"],
        "jitter": parsed["jitter"],
        "samples": parsed["samples"],
        "raw": output.strip()[:2000],
        "ok": received > 0,
    }


# --------------------------------------------------------------------------- #
# Ports
# --------------------------------------------------------------------------- #
@router.get("/ports")
def listening_ports() -> dict[str, Any]:
    """Currently listening TCP/UDP sockets with owning processes."""
    items: list[dict[str, Any]] = []
    warnings: list[str] = []
    try:
        connections = psutil.net_connections(kind="inet")
    except psutil.AccessDenied:
        connections = []
        warnings.append("Run the dashboard as administrator to see all sockets and process names.")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    name_cache: dict[int, str] = {}
    for conn in connections:
        if conn.status not in (psutil.CONN_LISTEN, psutil.CONN_NONE):
            continue
        pid = conn.pid or 0
        if pid and pid not in name_cache:
            try:
                name_cache[pid] = psutil.Process(pid).name()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                name_cache[pid] = ""
        try:
            addr = conn.laddr
            ip = addr.ip if hasattr(addr, "ip") else addr[0]
            port = addr.port if hasattr(addr, "port") else addr[1]
        except (AttributeError, IndexError, TypeError):
            continue
        items.append(
            {
                "ip": ip,
                "port": port,
                "proto": "TCP" if conn.type == socket.SOCK_STREAM else "UDP",
                "family": "IPv4" if conn.family == socket.AF_INET else "IPv6",
                "pid": pid,
                "process": name_cache.get(pid, ""),
                "status": conn.status,
            }
        )
    items.sort(key=lambda i: (i["port"], i["ip"]))
    return {"ports": items, "count": len(items), "warnings": warnings,
            "admin": _is_admin()}


def _is_admin() -> bool:
    from services.platform_utils import is_admin

    return is_admin()


@router.post("/portscan")
def port_scan(body: PortScanRequest) -> dict[str, Any]:
    """Basic connect() scan of local ports (localhost by default)."""
    host = body.host.strip()
    if not re.fullmatch(r"[A-Za-z0-9.\-_:]+", host):
        raise HTTPException(status_code=400, detail="Invalid host")

    ports = body.ports or COMMON_PORTS
    ports = [p for p in ports if isinstance(p, int) and 0 < p < 65536][:200]
    results: list[dict[str, Any]] = []

    def probe(port: int) -> dict[str, Any]:
        started = time.perf_counter()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(body.timeout)
        try:
            code = sock.connect_ex((host, port))
        except OSError:
            code = 1
        finally:
            sock.close()
        latency = round((time.perf_counter() - started) * 1000, 1)
        return {"port": port, "open": code == 0, "latency": latency}

    threads = [threading.Thread(target=lambda p=p: results.append(probe(p))) for p in ports]
    started = time.time()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=body.timeout + 1)
    results.sort(key=lambda r: r["port"])
    return {
        "host": host,
        "open": [r for r in results if r["open"]],
        "scan_time": round(time.time() - started, 2),
        "scanned": len(ports),
    }


# --------------------------------------------------------------------------- #
# Speed test
# --------------------------------------------------------------------------- #
@router.post("/speedtest")
def speed_test() -> dict[str, Any]:
    """Measure download throughput against Cloudflare's public speed endpoint."""
    if not config.get("network.speedtest", True):
        raise HTTPException(status_code=403, detail="Speed test is disabled in settings")

    url = "https://speed.cloudflare.com/__down?bytes=25000000"
    bytes_read = 0
    started = time.perf_counter()
    budget = 12.0
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "WindowsDashboard/1.0"})
        with urllib.request.urlopen(request, timeout=10) as response:
            while True:
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                bytes_read += len(chunk)
                if time.perf_counter() - started > budget:
                    break
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Speed test failed: {exc}") from exc

    elapsed = max(time.perf_counter() - started, 0.001)
    mbps = round((bytes_read * 8) / elapsed / 1_000_000, 2)
    return {
        "mbps": mbps,
        "mbytes_per_s": round(bytes_read / elapsed / 1_000_000, 2),
        "bytes": bytes_read,
        "seconds": round(elapsed, 2),
        "server": "speed.cloudflare.com",
    }


@router.get("/wifi")
def wifi() -> dict[str, Any]:
    from services.monitor import monitor

    data = monitor._wifi()  # noqa: SLF001
    return {"wifi": data, "available": data is not None}


@router.get("/flush-dns")
def flush_dns_get() -> dict[str, Any]:
    raise HTTPException(status_code=405, detail="Use POST /api/controls/flush-dns")

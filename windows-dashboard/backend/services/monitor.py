"""System monitoring service.

Collects a single snapshot dictionary with everything the dashboard's
"System" cards need.  Expensive/static information (CPU model, GPU list,
OS details) is gathered once and cached; dynamic values are sampled on every
call.  Network and disk speeds are derived from counter deltas, so the
service must be called repeatedly (the websocket loop does this every 2s).
"""

from __future__ import annotations

import json
import os
import platform
import socket
import subprocess
import threading
import time
from collections import deque
from typing import Any, Deque

import psutil

from services.platform_utils import IS_MACOS, IS_WINDOWS, run, run_powershell, which

# Prime psutil's CPU counters so the first reading is meaningful.
psutil.cpu_percent(interval=None)
try:  # pragma: no cover - not available on every platform
    psutil.cpu_percent(interval=None, percpu=True)
except Exception:
    pass


def _bytes(value: float | int | None) -> int:
    return int(value or 0)


def _gb(value: float | int | None, digits: int = 1) -> float:
    return round(_bytes(value) / (1024 ** 3), digits)


class Monitor:
    """Thread-safe system monitor with a small history buffer."""

    HISTORY_LEN = 90

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._primed = False
        self._last_net: tuple[float, int, int] | None = None
        self._last_disk_io: tuple[float, int, int] | None = None
        self._cached_gpu: list[dict[str, Any]] | None = None
        self._cached_static: dict[str, Any] | None = None
        self._nvidia = which("nvidia-smi")
        self._history: Deque[dict[str, Any]] = deque(maxlen=self.HISTORY_LEN)
        self._start_ts = time.time()
        # Cached slow lookups (PowerShell/netsh take 100-700 ms - far too slow
        # to run on every 2 s tick).
        self._win_temp: float | None = None
        self._win_temp_at = 0.0
        self._win_temp_busy = False
        self._wifi_cache: dict[str, Any] | None = None
        self._wifi_at = 0.0

    # ------------------------------------------------------------------ #
    # Static information
    # ------------------------------------------------------------------ #
    def static_info(self, refresh: bool = False) -> dict[str, Any]:
        with self._lock:
            if self._cached_static is not None and not refresh:
                return dict(self._cached_static)
            info = {
                "hostname": socket.gethostname(),
                "user": os.environ.get("USERNAME") or os.environ.get("USER") or "user",
                "os": self._os_info(),
                "cpu": self._cpu_info(),
                "boot_time": psutil.boot_time(),
                "python": platform.python_version(),
                "dashboard_started": self._start_ts,
                "platform": platform.system(),
            }
            self._cached_static = info
            return dict(info)

    def _os_info(self) -> dict[str, Any]:
        name = platform.system() or os.name
        release = platform.release()
        version = platform.version()
        edition = ""
        display = ""
        if IS_WINDOWS:
            try:
                out = run_powershell(
                    "Get-CimInstance Win32_OperatingSystem | "
                    "Select-Object Caption,Version,BuildNumber,OSArchitecture | ConvertTo-Json -Compress",
                    timeout=15,
                )
                if out.returncode == 0 and out.stdout.strip():
                    data = json.loads(out.stdout)
                    edition = str(data.get("Caption") or "").strip()
                    display = edition or f"Windows {release}"
                    version = str(data.get("Version") or version)
            except (OSError, ValueError, subprocess.SubprocessError):
                pass
            if not display:
                display = f"Windows {release}"
        elif IS_MACOS:
            display = f"macOS {platform.mac_ver()[0] or release}"
        else:
            try:
                with open("/etc/os-release", encoding="utf-8") as fh:
                    for line in fh:
                        if line.startswith("PRETTY_NAME="):
                            display = line.split("=", 1)[1].strip().strip('"')
                            break
            except OSError:
                display = ""
            display = display or f"{name} {release}"
        return {
            "system": name,
            "release": release,
            "version": version,
            "edition": edition,
            "display": display,
            "machine": platform.machine(),
            "arch": platform.architecture()[0],
        }

    def _cpu_info(self) -> dict[str, Any]:
        name = ""
        try:
            if IS_WINDOWS:
                out = run_powershell(
                    "(Get-CimInstance Win32_Processor | Select-Object -First 1 Name).Name",
                    timeout=15,
                )
                if out.returncode == 0:
                    name = out.stdout.strip()
            elif IS_MACOS:
                out = run(["sysctl", "-n", "machdep.cpu.brand_string"], timeout=5)
                name = out.stdout.strip() if out.returncode == 0 else ""
            else:
                with open("/proc/cpuinfo", encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        if line.lower().startswith("model name"):
                            name = line.split(":", 1)[1].strip()
                            break
        except (OSError, subprocess.SubprocessError):
            name = ""
        if not name:
            name = platform.processor() or "Unknown CPU"
        freq = psutil.cpu_freq()
        return {
            "model": " ".join(name.split()),
            "physical_cores": psutil.cpu_count(logical=False) or psutil.cpu_count() or 1,
            "logical_cores": psutil.cpu_count(logical=True) or 1,
            "max_freq_mhz": round(freq.max, 0) if freq and freq.max else None,
        }

    # ------------------------------------------------------------------ #
    # GPUs
    # ------------------------------------------------------------------ #
    def gpu_info(self) -> list[dict[str, Any]]:
        """Return GPU metadata, with live utilisation when nvidia-smi exists."""
        with self._lock:
            if self._cached_gpu is None:
                self._cached_gpu = self._detect_gpus()
            gpus = [dict(g) for g in self._cached_gpu]
        if self._nvidia:
            self._enrich_nvidia(gpus)
        return gpus

    def _detect_gpus(self) -> list[dict[str, Any]]:
        gpus: list[dict[str, Any]] = []
        try:
            if IS_WINDOWS:
                out = run_powershell(
                    "Get-CimInstance Win32_VideoController | "
                    "Select-Object Name,DriverVersion,AdapterRAM,VideoProcessor | ConvertTo-Json -Compress",
                    timeout=20,
                )
                if out.returncode == 0 and out.stdout.strip():
                    data = json.loads(out.stdout)
                    if isinstance(data, dict):
                        data = [data]
                    for item in data:
                        name = str(item.get("Name") or "GPU").strip()
                        if not name:
                            continue
                        if "microsoft basic display" in name.lower():
                            continue
                        gpus.append(
                            {
                                "name": name,
                                "vendor": self._gpu_vendor(name),
                                "driver": str(item.get("DriverVersion") or ""),
                                "memory_total": _bytes(item.get("AdapterRAM")),
                                "integrated": self._is_integrated(name),
                                "utilization": None,
                                "memory_used": None,
                                "temperature": None,
                            }
                        )
            elif IS_MACOS:
                out = run(["system_profiler", "SPDisplaysDataType"], timeout=20)
                for line in out.stdout.splitlines():
                    stripped = line.strip()
                    if stripped.lower().startswith("chipset model:"):
                        name = stripped.split(":", 1)[1].strip()
                        gpus.append(
                            {
                                "name": name,
                                "vendor": self._gpu_vendor(name),
                                "driver": "",
                                "memory_total": 0,
                                "integrated": self._is_integrated(name),
                                "utilization": None,
                                "memory_used": None,
                                "temperature": None,
                            }
                        )
            else:
                out = run(["lspci"], timeout=10)
                if out.returncode == 0:
                    for line in out.stdout.splitlines():
                        lowered = line.lower()
                        if "vga compatible controller" in lowered or "3d controller" in lowered:
                            name = line.split(":", 2)[-1].strip()
                            gpus.append(
                                {
                                    "name": name,
                                    "vendor": self._gpu_vendor(name),
                                    "driver": "",
                                    "memory_total": 0,
                                    "integrated": self._is_integrated(name),
                                    "utilization": None,
                                    "memory_used": None,
                                    "temperature": None,
                                }
                            )
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
        return gpus

    @staticmethod
    def _gpu_vendor(name: str) -> str:
        lowered = name.lower()
        if "nvidia" in lowered or "geforce" in lowered or "quadro" in lowered or "rtx" in lowered:
            return "NVIDIA"
        if "amd" in lowered or "radeon" in lowered or "ati " in lowered:
            return "AMD"
        if "intel" in lowered or "uhd graphics" in lowered or "iris" in lowered:
            return "Intel"
        if "apple" in lowered:
            return "Apple"
        return "Other"

    @staticmethod
    def _is_integrated(name: str) -> bool:
        lowered = name.lower()
        return any(
            token in lowered
            for token in ("intel", "iris", "uhd graphics", "vega 8", "radeon graphics", "apple m")
        )

    def _enrich_nvidia(self, gpus: list[dict[str, Any]]) -> None:
        """Merge live stats from nvidia-smi into the GPU list."""
        try:
            proc = run(
                [
                    self._nvidia,
                    "--query-gpu=name,utilization.gpu,memory.used,memory.total,"
                    "temperature.gpu,driver_version",
                    "--format=csv,noheader,nounits",
                ],
                timeout=6,
            )
            if proc.returncode != 0:
                return
            rows = [line for line in proc.stdout.splitlines() if line.strip()]
            parsed = []
            for row in rows:
                parts = [p.strip() for p in row.split(",")]
                if len(parts) < 6:
                    continue
                try:
                    parsed.append(
                        {
                            "name": parts[0],
                            "utilization": float(parts[1]),
                            "memory_used": int(float(parts[2])) * 1024 * 1024,
                            "memory_total": int(float(parts[3])) * 1024 * 1024,
                            "temperature": float(parts[4]),
                            "driver": parts[5],
                        }
                    )
                except ValueError:
                    continue
            if not parsed:
                return
            if not gpus:
                gpus.extend({**gpu, "vendor": "NVIDIA", "integrated": False} for gpu in parsed)
                return
            for gpu in gpus:
                if "nvidia" not in gpu["name"].lower() and gpu["vendor"] != "NVIDIA":
                    continue
                match = next(
                    (p for p in parsed if p["name"].lower()[:12] in gpu["name"].lower() or
                     gpu["name"].lower()[:12] in p["name"].lower()),
                    parsed[0],
                )
                gpu.update(
                    {
                        "utilization": match["utilization"],
                        "memory_used": match["memory_used"],
                        "memory_total": match["memory_total"] or gpu["memory_total"],
                        "temperature": match["temperature"],
                        "driver": match.get("driver") or gpu["driver"],
                    }
                )
        except (OSError, subprocess.SubprocessError):
            return

    # ------------------------------------------------------------------ #
    # Temperatures
    # ------------------------------------------------------------------ #
    def temperatures(self) -> dict[str, Any]:
        """Best-effort CPU/GPU temperatures (often unavailable on Windows)."""
        result: dict[str, Any] = {"cpu": None, "gpu": None, "other": []}
        gpus = self._cached_gpu or []
        for gpu in gpus:
            if gpu.get("temperature"):
                result["gpu"] = gpu["temperature"]
                break
        try:
            if hasattr(psutil, "sensors_temperatures"):
                temps = psutil.sensors_temperatures() or {}
                for sensor_name, entries in temps.items():
                    for entry in entries:
                        if entry.current is None:
                            continue
                        label = (entry.label or sensor_name).lower()
                        entry_payload = {
                            "label": entry.label or sensor_name,
                            "current": round(float(entry.current), 1),
                            "high": entry.high,
                            "critical": entry.critical,
                        }
                        if result["cpu"] is None and any(
                            key in label for key in ("cpu", "core", "package", "k10temp", "tctl")
                        ):
                            result["cpu"] = entry_payload["current"]
                        if result["gpu"] is None and "gpu" in label:
                            result["gpu"] = entry_payload["current"]
                        if len(result["other"]) < 8:
                            result["other"].append(entry_payload)
        except (OSError, AttributeError, ValueError):
            pass

        if result["cpu"] is None and IS_WINDOWS:
            result["cpu"] = self._windows_cpu_temp()
        return result

    def _windows_cpu_temp(self) -> float | None:
        """Read WMI thermal zones without blocking the sampling loop.

        PowerShell startup costs several hundred milliseconds, so the value is
        cached for 30s and refreshed on a background thread.
        """
        now = time.time()
        with self._lock:
            cached = self._win_temp
            fresh = (now - self._win_temp_at) < 30
            busy = self._win_temp_busy
            if fresh or busy:
                return cached
            self._win_temp_busy = True

        def worker() -> None:
            value: float | None = None
            try:
                proc = run_powershell(
                    "(Get-CimInstance -Namespace root/WMI -ClassName MSAcpi_ThermalZoneTemperature "
                    "-ErrorAction SilentlyContinue | "
                    "Select-Object -ExpandProperty CurrentTemperature) -join ','",
                    timeout=8,
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    values = [
                        round(float(v) / 10.0 - 273.15, 1)
                        for v in proc.stdout.strip().split(",")
                        if v.strip().isdigit() and int(v) > 2000
                    ]
                    values = [v for v in values if -10 < v < 130]
                    if values:
                        value = max(values)
            except (OSError, ValueError, subprocess.SubprocessError):
                value = None
            finally:
                with self._lock:
                    self._win_temp = value
                    self._win_temp_at = time.time()
                    self._win_temp_busy = False

        threading.Thread(target=worker, daemon=True, name="cpu-temp").start()
        return cached

    # ------------------------------------------------------------------ #
    # Network
    # ------------------------------------------------------------------ #
    def _primary_ip(self) -> str | None:
        """Find the LAN-facing IP without sending any packets."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
        except OSError:
            return None
        finally:
            sock.close()

    def _interface_addresses(self) -> list[dict[str, Any]]:
        addresses: list[dict[str, Any]] = []
        try:
            stats = psutil.net_if_stats()
            addrs = psutil.net_if_addrs()
        except (OSError, AttributeError):
            return addresses
        for iface, entries in addrs.items():
            ipv4, mac = None, None
            for entry in entries:
                if entry.family == socket.AF_INET and ipv4 is None:
                    ipv4 = entry.address
                elif getattr(psutil, "AF_LINK", object()) == entry.family:
                    mac = entry.address
            if not ipv4 and not mac:
                continue
            stat = stats.get(iface)
            addresses.append(
                {
                    "name": iface,
                    "ipv4": ipv4,
                    "mac": mac,
                    "up": bool(stat.isup) if stat else False,
                    "speed_mbps": int(stat.speed) if stat and stat.speed else None,
                }
            )
        addresses.sort(key=lambda a: (not a["up"], a["name"]))
        return addresses

    def _wifi(self) -> dict[str, Any] | None:
        """Cached Wi-Fi details (``netsh`` is slow, so refresh every 15s)."""
        with self._lock:
            if self._wifi_cache is not None and (time.time() - self._wifi_at) < 15:
                return dict(self._wifi_cache)
        result = self._wifi_lookup()
        with self._lock:
            self._wifi_cache = result
            self._wifi_at = time.time()
        return dict(result) if result else None

    def _wifi_lookup(self) -> dict[str, Any] | None:
        if IS_WINDOWS:
            try:
                proc = run(["netsh", "wlan", "show", "interfaces"], timeout=8)
                if proc.returncode != 0:
                    return None
                data: dict[str, Any] = {}
                for line in proc.stdout.splitlines():
                    if ":" not in line:
                        continue
                    key, _, value = line.partition(":")
                    key = key.strip().lower()
                    value = value.strip()
                    if key in ("ssid", "name"):
                        if "ssid" in key and "bssid" not in key:
                            data.setdefault("ssid", value)
                    elif key == "signal":
                        data["signal"] = value
                    elif key == "state":
                        data["state"] = value
                    elif "radio type" in key:
                        data["radio"] = value
                    elif "authentication" in key:
                        data["security"] = value
                if data.get("ssid"):
                    signal = data.get("signal", "")
                    percent = None
                    if "%" in signal:
                        try:
                            percent = int(signal.replace("%", "").strip())
                        except ValueError:
                            percent = None
                    return {"ssid": data["ssid"], "signal": percent, "radio": data.get("radio")}
            except (OSError, subprocess.SubprocessError):
                return None
        elif IS_MACOS:
            try:
                proc = run(["/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/"
                            "Resources/airport", "-I"], timeout=8)
                for line in proc.stdout.splitlines():
                    if " SSID:" in line and "BSSID" not in line:
                        return {"ssid": line.split(":", 1)[1].strip(), "signal": None, "radio": None}
            except (OSError, subprocess.SubprocessError):
                return None
        else:
            proc = which("nmcli")
            if proc:
                try:
                    out = run([proc, "-t", "-f", "active,ssid", "dev", "wifi"], timeout=8)
                    for line in out.stdout.splitlines():
                        if line.startswith("yes:"):
                            return {"ssid": line.split(":", 1)[1], "signal": None, "radio": None}
                except (OSError, subprocess.SubprocessError):
                    return None
        return None

    def _network(self, now: float) -> dict[str, Any]:
        try:
            counters = psutil.net_io_counters()
        except (OSError, AttributeError, ValueError):
            counters = None
        up_speed = down_speed = 0.0
        total_sent = total_recv = 0
        if counters is not None:
            total_sent, total_recv = counters.bytes_sent, counters.bytes_recv
            if self._last_net is not None:
                last_ts, last_sent, last_recv = self._last_net
                elapsed = max(now - last_ts, 0.001)
                up_speed = max((total_sent - last_sent) / elapsed, 0)
                down_speed = max((total_recv - last_recv) / elapsed, 0)
            self._last_net = (now, total_sent, total_recv)

        primary = self._primary_ip()
        interfaces = self._interface_addresses()
        if primary is None:
            primary = next((i["ipv4"] for i in interfaces if i["ipv4"] and i["up"]), None)
        return {
            "download_bps": round(down_speed, 1),
            "upload_bps": round(up_speed, 1),
            "total_received": total_recv,
            "total_sent": total_sent,
            "primary_ip": primary,
            "interfaces": interfaces,
            "wifi": self._wifi(),
        }

    # ------------------------------------------------------------------ #
    # Disks
    # ------------------------------------------------------------------ #
    def _disks(self) -> list[dict[str, Any]]:
        disks: list[dict[str, Any]] = []
        skip_fs = {"", "squashfs", "tmpfs", "devtmpfs", "overlay", "proc", "sysfs", "cgroup",
                   "cgroup2", "ramfs", "devfs", "autofs", "iso9660", "udf"}
        try:
            partitions = psutil.disk_partitions(all=False)
        except (OSError, AttributeError):
            partitions = []
        seen: set[str] = set()
        for part in partitions:
            if part.fstype.lower() in skip_fs or part.fstype.lower() == "cdrom":
                continue
            if part.device in seen:
                continue
            seen.add(part.device)
            try:
                usage = psutil.disk_usage(part.mountpoint)
            except (OSError, PermissionError):
                continue
            disks.append(
                {
                    "device": part.device,
                    "mount": part.mountpoint,
                    "fstype": part.fstype,
                    "total": usage.total,
                    "used": usage.used,
                    "free": usage.free,
                    "percent": round(usage.percent, 1),
                    "removable": "removable" in (part.opts or "").lower(),
                }
            )
        if not disks:
            try:
                usage = psutil.disk_usage(os.path.abspath(os.sep))
                disks.append(
                    {
                        "device": os.path.abspath(os.sep),
                        "mount": os.path.abspath(os.sep),
                        "fstype": "",
                        "total": usage.total,
                        "used": usage.used,
                        "free": usage.free,
                        "percent": round(usage.percent, 1),
                        "removable": False,
                    }
                )
            except OSError:
                pass
        return disks

    def _disk_io(self, now: float) -> dict[str, float]:
        try:
            io = psutil.disk_io_counters()
        except (OSError, AttributeError, ValueError):
            return {"read_bps": 0.0, "write_bps": 0.0}
        if io is None:
            return {"read_bps": 0.0, "write_bps": 0.0}
        read_bps = write_bps = 0.0
        if self._last_disk_io is not None:
            last_ts, last_read, last_write = self._last_disk_io
            elapsed = max(now - last_ts, 0.001)
            read_bps = max((io.read_bytes - last_read) / elapsed, 0)
            write_bps = max((io.write_bytes - last_write) / elapsed, 0)
        self._last_disk_io = (now, io.read_bytes, io.write_bytes)
        return {"read_bps": round(read_bps, 1), "write_bps": round(write_bps, 1)}

    # ------------------------------------------------------------------ #
    # Battery / load
    # ------------------------------------------------------------------ #
    def _battery(self) -> dict[str, Any] | None:
        try:
            battery = psutil.sensors_battery()
        except (AttributeError, OSError, NotImplementedError):
            return None
        if battery is None:
            return None
        seconds = battery.secsleft
        if seconds in (psutil.POWER_TIME_UNLIMITED, psutil.POWER_TIME_UNKNOWN) or seconds < 0:
            seconds = None
        return {
            "percent": round(float(battery.percent), 1),
            "plugged": bool(battery.power_plugged),
            "seconds_left": seconds,
        }

    def _cpu_stats(self) -> dict[str, Any]:
        per_core = psutil.cpu_percent(interval=None, percpu=True)
        times = psutil.cpu_times()
        try:
            load = psutil.getloadavg()
        except (AttributeError, OSError):
            load = (0.0, 0.0, 0.0)
        try:
            freq = psutil.cpu_freq()
            freq_mhz = round(freq.current, 0) if freq else None
        except (OSError, AttributeError, NotImplementedError):
            freq_mhz = None
        return {
            "percent": round(sum(per_core) / len(per_core), 1) if per_core else 0.0,
            "per_core": [round(v, 1) for v in per_core],
            "freq_mhz": freq_mhz,
            "load_avg": [round(v, 2) for v in load],
            "times": {
                "user": round(times.user, 1),
                "system": round(times.system, 1),
                "idle": round(times.idle, 1),
            },
        }

    # ------------------------------------------------------------------ #
    # Snapshot
    # ------------------------------------------------------------------ #
    def snapshot(self) -> dict[str, Any]:
        now = time.time()
        with self._lock:
            memory = psutil.virtual_memory()
            swap = psutil.swap_memory()
            cpu = self._cpu_stats()
            net = self._network(now)
            disks = self._disks()
            io = self._disk_io(now)
            gpus = self.gpu_info()
            temps = self.temperatures()
            battery = self._battery()
            try:
                proc_count = len(psutil.pids())
            except (psutil.Error, OSError):
                proc_count = 0
            uptime = max(now - psutil.boot_time(), 0)

            mem_used = memory.total - memory.available
            payload: dict[str, Any] = {
                "ts": now,
                "cpu": cpu,
                "memory": {
                    "total": memory.total,
                    "used": mem_used,
                    "available": memory.available,
                    "percent": round(memory.percent, 1),
                },
                "swap": {
                    "total": swap.total,
                    "used": swap.used,
                    "percent": round(swap.percent, 1) if swap.total else 0.0,
                },
                "disks": disks,
                "disk_io": io,
                "network": net,
                "gpus": gpus,
                "temperatures": temps,
                "battery": battery,
                "uptime": round(uptime, 0),
                "process_count": proc_count,
            }
            self._append_history(payload)
            return payload

    def _append_history(self, payload: dict[str, Any]) -> None:
        primary_disk = max(payload["disks"], key=lambda d: d["total"], default=None)
        self._history.append(
            {
                "ts": round(payload["ts"], 0),
                "cpu": payload["cpu"]["percent"],
                "mem": payload["memory"]["percent"],
                "disk": primary_disk["percent"] if primary_disk else 0,
                "down": payload["network"]["download_bps"],
                "up": payload["network"]["upload_bps"],
                "gpu": next((g.get("utilization") for g in payload["gpus"] if g.get("utilization")), None),
            }
        )

    def history(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._history)

    def summary(self) -> dict[str, Any]:
        """Lightweight snapshot used by the websocket tick."""
        snap = self.snapshot()
        snap["history"] = self.history()
        snap["static"] = self.static_info()
        return snap


# Shared monitor instance.
monitor = Monitor()


def quick_facts() -> dict[str, Any]:
    """Cheap facts used by the header (no subprocess calls)."""
    return {
        "hostname": socket.gethostname(),
        "platform": platform.system(),
        "boot_time": psutil.boot_time(),
        "uptime": round(time.time() - psutil.boot_time(), 0),
        "user": os.environ.get("USERNAME") or os.environ.get("USER") or "user",
    }


def disk_free(path: str) -> float:
    """Free space in GB for a path (used before destructive operations)."""
    try:
        return round(psutil.disk_usage(path).free / (1024 ** 3), 2)
    except OSError:
        return 0.0

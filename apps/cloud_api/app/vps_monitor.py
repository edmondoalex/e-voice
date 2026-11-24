"""Read-only VPS telemetry used by the authenticated e-Voice dashboard."""

# ruff: noqa: E501

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import platform
import re
import shutil
import socket
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

DATA_ROOT = Path(os.getenv("EKONEX_VPS_MONITOR_DATA_PATH", "/var/lib/ekonex-voice/vps-monitor"))
SNAPSHOT_PATH = DATA_ROOT / "host-snapshot.json"
HISTORY_PATH = DATA_ROOT / "history.jsonl"
INCIDENTS_PATH = DATA_ROOT / "incidents.jsonl"
GEO_PATH = DATA_ROOT / "geo-cache.json"
SECURITY_PATH = DATA_ROOT / "security-events.json"
MAX_HISTORY_DAYS = 31
IP_PATTERN = re.compile(r"(?<![\w:])(?P<ip>(?:\d{1,3}\.){3}\d{1,3})(?![\w:])")
NGINX_PATTERN = re.compile(r'^(?P<ip>\S+) .* \[(?P<at>[^]]+)] "[^"]*" (?P<status>\d{3}) ')


def _run(command: list[str], timeout: int = 4) -> tuple[int | None, str]:
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, check=False
        )
        return result.returncode, (result.stdout or result.stderr or "").strip()
    except (OSError, subprocess.SubprocessError):
        return None, ""


def _percent(used: int, total: int) -> float:
    return round((used / total * 100) if total else 0.0, 1)


def _memory() -> dict[str, int | float]:
    values: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, value = line.split(":", 1)
            values[key] = int(value.strip().split()[0]) * 1024
    except (OSError, ValueError):
        pass
    total = values.get("MemTotal", 0)
    available = values.get("MemAvailable", 0)
    used = max(0, total - available)
    swap_total = values.get("SwapTotal", 0)
    swap_used = max(0, swap_total - values.get("SwapFree", 0))
    return {
        "total_bytes": total,
        "used_bytes": used,
        "percent": _percent(used, total),
        "swap_total_bytes": swap_total,
        "swap_used_bytes": swap_used,
        "swap_percent": _percent(swap_used, swap_total),
    }


def _cpu() -> dict[str, Any]:
    try:
        loads = [round(value, 2) for value in os.getloadavg()]
    except OSError:
        loads = [0.0, 0.0, 0.0]
    cores = os.cpu_count() or 1
    return {
        "percent": round(min(100.0, loads[0] / cores * 100), 1),
        "cores": cores,
        "load_average": loads,
    }


def _network() -> dict[str, int]:
    sent = received = 0
    try:
        for line in Path("/proc/net/dev").read_text().splitlines()[2:]:
            name, values = line.split(":", 1)
            if name.strip() == "lo":
                continue
            fields = values.split()
            received += int(fields[0])
            sent += int(fields[8])
    except (OSError, ValueError, IndexError):
        pass
    return {"sent_bytes": sent, "received_bytes": received}


def _uptime() -> int:
    try:
        return int(float(Path("/proc/uptime").read_text().split()[0]))
    except (OSError, ValueError, IndexError):
        return 0


def _service(unit: str, label: str) -> dict[str, Any]:
    code, state = _run(["systemctl", "is-active", unit])
    if code is None:
        state = "unknown"
    code, properties = _run(
        [
            "systemctl",
            "show",
            unit,
            "--property=MainPID,MemoryCurrent,ActiveEnterTimestampMonotonic,NRestarts",
            "--value",
        ]
    )
    lines = properties.splitlines() if code == 0 else []
    return {
        "id": unit,
        "name": label,
        "state": state if state else "unknown",
        "pid": int(lines[0]) if lines and lines[0].isdigit() else None,
        "memory_bytes": int(lines[1]) if len(lines) > 1 and lines[1].isdigit() else None,
        "restarts": int(lines[3]) if len(lines) > 3 and lines[3].isdigit() else None,
    }


def _compose_services() -> list[dict[str, Any]]:
    compose_file = os.getenv(
        "EKONEX_VPS_MONITOR_COMPOSE_FILE", "/opt/ekonex/e-voice/docker-compose.yml"
    )
    code, output = _run(
        ["docker", "compose", "-f", compose_file, "ps", "--format", "json"], timeout=8
    )
    labels = {
        "api": "e-Voice API",
        "maintenance": "Manutenzione",
        "postgres": "PostgreSQL",
        "redis": "Redis",
    }
    services: list[dict[str, Any]] = []
    if code == 0:
        try:
            parsed = json.loads(output)
            rows = parsed if isinstance(parsed, list) else [parsed]
        except json.JSONDecodeError:
            rows = []
            for line in output.splitlines():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        for row in rows:
            service = str(row.get("Service") or row.get("service") or "")
            if service not in labels:
                continue
            state = str(row.get("State") or row.get("state") or "unknown").lower()
            services.append(
                {
                    "id": service,
                    "name": labels[service],
                    "state": "active" if state == "running" else state,
                    "pid": None,
                    "memory_bytes": None,
                    "restarts": None,
                }
            )
    if services:
        return services
    return [
        _service("nginx.service", "Nginx"),
        {
            "id": "compose",
            "name": "Container e-Voice",
            "state": "unknown",
            "pid": None,
            "memory_bytes": None,
            "restarts": None,
        },
    ]


def _endpoint(url: str) -> dict[str, Any]:
    started = time.monotonic()
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "EkonexVoiceMonitor/1.0"})
        with urllib.request.urlopen(request, timeout=5) as response:
            status = response.status
        ok = status < 500
    except urllib.error.HTTPError as error:
        status, ok = error.code, error.code < 500
    except (OSError, urllib.error.URLError, TimeoutError):
        status, ok = None, False
    return {
        "url": url,
        "status": status,
        "ok": ok,
        "latency_ms": round((time.monotonic() - started) * 1000),
    }


def _certificate(domain: str) -> dict[str, Any]:
    try:
        context = ssl.create_default_context()
        with socket.create_connection((domain, 443), timeout=5) as raw:
            with context.wrap_socket(raw, server_hostname=domain) as secured:
                certificate = secured.getpeercert()
                if certificate is None:
                    raise ValueError("certificate missing")
                expires_raw = certificate.get("notAfter")
                if not isinstance(expires_raw, str):
                    raise ValueError("certificate expiration missing")
        expires = datetime.strptime(expires_raw, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=UTC)
        return {
            "domain": domain,
            "expires_at": expires.isoformat(),
            "days_remaining": int((expires - datetime.now(UTC)).total_seconds() / 86400),
        }
    except (OSError, ssl.SSLError, KeyError, ValueError):
        return {"domain": domain, "expires_at": None, "days_remaining": None}


def _security_summary() -> dict[str, Any]:
    _, ufw = _run(["ufw", "status"])
    _, fail2ban = _run(["systemctl", "is-active", "fail2ban"])
    _, failed = _run(
        ["journalctl", "--since", "24 hours ago", "-u", "ssh", "--no-pager", "-o", "cat"], timeout=8
    )
    failures = sum(
        "failed" in line.lower() or "invalid user" in line.lower() for line in failed.splitlines()
    )
    return {
        "ufw_active": "Status: active" in ufw,
        "fail2ban": fail2ban or "unknown",
        "ssh_failures_24h": failures,
    }


def _latest_backup() -> dict[str, Any]:
    root = Path(os.getenv("EKONEX_VPS_MONITOR_BACKUP_PATH", "/opt/ekonex/backups/e-voice"))
    try:
        files = [item for item in root.rglob("*") if item.is_file()]
        latest = max(files, key=lambda item: item.stat().st_mtime)
        modified = datetime.fromtimestamp(latest.stat().st_mtime, tz=UTC)
        return {
            "available": True,
            "last_at": modified.isoformat(),
            "age_hours": round((datetime.now(UTC) - modified).total_seconds() / 3600, 1),
            "files": len(files),
            "size_bytes": sum(item.stat().st_size for item in files),
        }
    except (OSError, ValueError):
        return {
            "available": False,
            "last_at": None,
            "age_hours": None,
            "files": 0,
            "size_bytes": 0,
        }


def _live_snapshot() -> dict[str, Any]:
    now = datetime.now(UTC)
    disk = shutil.disk_usage("/")
    cpu, memory = _cpu(), _memory()
    disk_value = {
        "total_bytes": disk.total,
        "used_bytes": disk.used,
        "percent": _percent(disk.used, disk.total),
    }
    services = _compose_services()
    services.append(_service("nginx.service", "Nginx"))
    alerts: list[dict[str, str]] = []
    for label, value, threshold in (
        ("CPU", float(cpu["percent"]), 85),
        ("Memoria", float(memory["percent"]), 90),
        ("Disco", float(disk_value["percent"]), 90),
    ):
        if value >= threshold:
            alerts.append(
                {
                    "level": "critical",
                    "title": f"{label} elevata",
                    "detail": f"Utilizzo {value}%",
                    "action": "Verificare carico e processi della VPS.",
                }
            )
    for service in services:
        if service["state"] not in {"active", "unknown"}:
            alerts.append(
                {
                    "level": "critical",
                    "title": f"{service['name']} non attivo",
                    "detail": service["state"],
                    "action": "Controllare il servizio e i log.",
                }
            )
    return {
        "generated_at": now.isoformat(),
        "health": "critical" if alerts else "healthy",
        "host": {
            "name": platform.node(),
            "os": platform.system(),
            "release": platform.release(),
            "uptime_seconds": _uptime(),
        },
        "resources": {"cpu": cpu, "memory": memory, "disk": disk_value, "network": _network()},
        "services": services,
        "alerts": alerts,
        "endpoints": [_endpoint("https://voice.e-control.tech/health")],
        "certificates": [_certificate("voice.e-control.tech")],
        "backup": _latest_backup(),
        "security": _security_summary(),
        "inventory": {
            "python": platform.python_version(),
            "kernel": platform.release(),
            "architecture": platform.machine(),
        },
    }


def snapshot() -> dict[str, Any]:
    """Read the sanitized host snapshot, with a development fallback."""
    try:
        value = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        if isinstance(value, dict) and value.get("generated_at"):
            return value
    except (OSError, json.JSONDecodeError):
        pass
    return _live_snapshot()


def _append(path: Path, row: dict[str, Any]) -> None:
    try:
        DATA_ROOT.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    except OSError:
        return


def record_sample() -> dict[str, Any]:
    data = _live_snapshot()
    resources = data["resources"]
    row = {
        "at": data["generated_at"],
        "cpu": resources["cpu"]["percent"],
        "memory": resources["memory"]["percent"],
        "disk": resources["disk"]["percent"],
    }
    _append(HISTORY_PATH, row)
    if data["alerts"]:
        _append(
            INCIDENTS_PATH,
            {"at": data["generated_at"], "health": data["health"], "alerts": data["alerts"]},
        )
    try:
        DATA_ROOT.mkdir(parents=True, exist_ok=True)
        temporary = SNAPSHOT_PATH.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        temporary.replace(SNAPSHOT_PATH)
    except OSError:
        pass
    try:
        stale_security = time.time() - SECURITY_PATH.stat().st_mtime >= 300
    except OSError:
        stale_security = True
    if stale_security:
        security = _collect_security_events(24 * 30)
        try:
            SECURITY_PATH.write_text(json.dumps(security, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
    return data


def _read_rows(path: Path, since: datetime, limit: int = 10000) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines()[-limit:]:
            row = json.loads(line)
            if datetime.fromisoformat(row["at"]) >= since:
                rows.append(row)
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        pass
    return rows


def history(hours: int) -> list[dict[str, Any]]:
    return _read_rows(HISTORY_PATH, datetime.now(UTC) - timedelta(hours=hours))


def incidents(limit: int = 12) -> list[dict[str, Any]]:
    return _read_rows(INCIDENTS_PATH, datetime.now(UTC) - timedelta(days=MAX_HISTORY_DAYS))[-limit:]


def _collect_security_events(hours: int) -> dict[str, Any]:
    since = f"{hours} hours ago"
    _, output = _run(
        ["journalctl", "--since", since, "-u", "ssh", "--no-pager", "-o", "short-iso"], timeout=12
    )
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for line in output.splitlines():
        match = IP_PATTERN.search(line)
        if not match:
            continue
        lowered = line.lower()
        event = (
            "autorizzato"
            if "accepted" in lowered
            else "fallito"
            if ("failed" in lowered or "invalid user" in lowered)
            else "osservato"
        )
        key = (match.group("ip"), event)
        timestamp = line.split(maxsplit=1)[0]
        row = grouped.setdefault(
            key,
            {
                "ip": key[0],
                "event": event,
                "status": event,
                "attempts": 0,
                "last_seen": timestamp,
                "city": None,
                "region": None,
                "country": None,
                "provider": None,
                "latitude": None,
                "longitude": None,
            },
        )
        row["attempts"] += 1
        row["last_seen"] = timestamp
    cutoff = datetime.now(UTC) - timedelta(hours=hours)
    nginx_root = Path(os.getenv("EKONEX_VPS_MONITOR_NGINX_LOG_PATH", "/var/log/nginx"))
    try:
        nginx_logs = [
            item
            for item in nginx_root.glob("access.log*")
            if item.is_file() and item.suffix != ".gz"
        ]
    except OSError:
        nginx_logs = []
    for log_path in nginx_logs:
        try:
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-20000:]
        except OSError:
            continue
        for line in lines:
            match = NGINX_PATTERN.match(line)
            if not match:
                continue
            try:
                observed_at = datetime.strptime(match.group("at"), "%d/%b/%Y:%H:%M:%S %z")
            except ValueError:
                continue
            if observed_at < cutoff:
                continue
            status_code = int(match.group("status"))
            event = "HTTP riuscito" if status_code < 400 else "HTTP rifiutato"
            connection_status = "autorizzato" if status_code < 400 else "rifiutato"
            key = (match.group("ip"), event)
            row = grouped.setdefault(
                key,
                {
                    "ip": key[0],
                    "event": event,
                    "status": connection_status,
                    "attempts": 0,
                    "last_seen": observed_at.isoformat(),
                    "city": None,
                    "region": None,
                    "country": None,
                    "provider": None,
                    "latitude": None,
                    "longitude": None,
                },
            )
            row["attempts"] += 1
            if observed_at.isoformat() > row["last_seen"]:
                row["last_seen"] = observed_at.isoformat()
    rows = sorted(grouped.values(), key=lambda value: value["attempts"], reverse=True)[:250]
    geo_cache: dict[str, dict[str, Any]] = {}
    try:
        loaded = json.loads(GEO_PATH.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            geo_cache = loaded
    except (OSError, json.JSONDecodeError):
        pass
    missing: list[str] = []
    for address in {row["ip"] for row in rows}:
        try:
            public = ipaddress.ip_address(address).is_global
        except ValueError:
            public = False
        if address not in geo_cache and public:
            missing.append(address)

    def lookup(address: str) -> tuple[str, dict[str, Any]]:
        try:
            with urllib.request.urlopen(f"https://ipwho.is/{address}", timeout=3) as response:
                payload = json.loads(response.read(65536))
            location = {
                "city": payload.get("city"),
                "region": payload.get("region"),
                "country": payload.get("country"),
                "provider": (payload.get("connection") or {}).get("isp"),
                "latitude": payload.get("latitude"),
                "longitude": payload.get("longitude"),
            }
        except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError):
            location = {}
        return address, location

    changed = bool(missing)
    if missing:
        with ThreadPoolExecutor(max_workers=8) as executor:
            for address, location in executor.map(lookup, missing):
                geo_cache[address] = location
    for row in rows:
        cached_location = geo_cache.get(row["ip"])
        if cached_location:
            row.update(cached_location)
    if changed:
        try:
            DATA_ROOT.mkdir(parents=True, exist_ok=True)
            GEO_PATH.write_text(json.dumps(geo_cache, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
    return {
        "sources": rows,
        "total_attempts": sum(row["attempts"] for row in rows),
        "status_counts": dict(Counter(row["status"] for row in rows)),
    }


def security_events(hours: int) -> dict[str, Any]:
    try:
        value = json.loads(SECURITY_PATH.read_text(encoding="utf-8"))
        if isinstance(value, dict) and isinstance(value.get("sources"), list):
            cutoff = datetime.now(UTC) - timedelta(hours=hours)
            sources = []
            for row in value["sources"]:
                try:
                    seen = datetime.fromisoformat(str(row["last_seen"]).replace(" ", "T"))
                    if seen.tzinfo is None:
                        seen = seen.replace(tzinfo=UTC)
                except (ValueError, KeyError):
                    seen = datetime.now(UTC)
                if seen >= cutoff:
                    sources.append(row)
            return {
                "sources": sources,
                "total_attempts": sum(row["attempts"] for row in sources),
                "status_counts": dict(Counter(row["status"] for row in sources)),
            }
    except (OSError, json.JSONDecodeError):
        pass
    return _collect_security_events(hours)


async def run_history_collector() -> None:
    while True:
        await asyncio.to_thread(record_sample)
        await asyncio.sleep(60)


if __name__ == "__main__":
    record_sample()

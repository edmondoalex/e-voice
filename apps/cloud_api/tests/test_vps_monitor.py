from __future__ import annotations

import json
from datetime import UTC, datetime

from apps.cloud_api.app import vps_monitor


def test_snapshot_prefers_sanitized_host_file(tmp_path, monkeypatch) -> None:
    snapshot_path = tmp_path / "host-snapshot.json"
    snapshot_path.write_text(
        json.dumps({"generated_at": datetime.now(UTC).isoformat(), "host": {"name": "vps"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(vps_monitor, "SNAPSHOT_PATH", snapshot_path)
    assert vps_monitor.snapshot()["host"]["name"] == "vps"


def test_history_is_limited_to_requested_window(tmp_path, monkeypatch) -> None:
    history_path = tmp_path / "history.jsonl"
    history_path.write_text(
        json.dumps({"at": datetime.now(UTC).isoformat(), "cpu": 1, "memory": 2, "disk": 3}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(vps_monitor, "HISTORY_PATH", history_path)
    assert len(vps_monitor.history(1)) == 1


def test_nginx_connections_are_classified_as_successful_or_rejected(tmp_path, monkeypatch) -> None:
    nginx = tmp_path / "nginx"
    nginx.mkdir()
    now = datetime.now(UTC).strftime("%d/%b/%Y:%H:%M:%S %z")
    (nginx / "access.log").write_text(
        f'127.0.0.1 - - [{now}] "GET /health HTTP/1.1" 200 12 "-" "test"\n'
        f'127.0.0.2 - - [{now}] "POST /login HTTP/1.1" 403 12 "-" "test"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("EKONEX_VPS_MONITOR_NGINX_LOG_PATH", str(nginx))
    monkeypatch.setattr(vps_monitor, "_run", lambda *_args, **_kwargs: (0, ""))
    monkeypatch.setattr(vps_monitor, "GEO_PATH", tmp_path / "geo-cache.json")
    monkeypatch.setattr(vps_monitor, "DATA_ROOT", tmp_path)

    result = vps_monitor._collect_security_events(1)

    statuses = {row["status"] for row in result["sources"]}
    assert statuses == {"autorizzato", "rifiutato"}

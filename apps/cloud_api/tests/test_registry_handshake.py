import asyncio
import hashlib
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from apps.cloud_api.app import evcp
from apps.cloud_api.app.domain.enums import InstallationStatus
from apps.cloud_api.app.domain.models import ConnectorCredential, Entity, Installation
from apps.cloud_api.tests.test_evcp import CONNECTOR_CAPABILITIES, FakeConnectorWebSocket, message


@pytest.mark.parametrize("flag,advertised", [(False, True), (True, False), (True, True)])
async def test_negotiation_session_gate_and_old_peer(
    session, seeded_domain, monkeypatch, flag, advertised
):
    iid = seeded_domain.installation_a_id
    inst = await session.get(Installation, iid)
    inst.status = InstallationStatus.ACTIVE
    secret = "evc_registry_synthetic_secret"
    session.add(
        ConnectorCredential(
            installation_id=iid, secret_hash=hashlib.sha256(secret.encode()).hexdigest()
        )
    )
    await session.commit()
    monkeypatch.setattr(
        evcp, "get_settings", lambda: SimpleNamespace(voice_selection_v1_enabled=True)
    )
    monkeypatch.setattr(evcp, "REGISTRY_ONLY_NEGOTIATION_ENABLED", flag)
    ws = FakeConnectorWebSocket(secret)
    if iid in evcp.sessions._sessions:
        await evcp.sessions.remove(iid, evcp.sessions._sessions[iid].session_id)
    task = asyncio.create_task(evcp.connector_websocket(ws, session))
    try:
        caps = {**CONNECTOR_CAPABILITIES, "supports_voice_selection_v1": True}
        if advertised:
            caps["supports_voice_registry_only_v1"] = True
        await ws.inbound.put(
            message(
                "hello",
                {
                    "installation_id": str(iid),
                    "connector_version": "0.1.8-beta.5",
                    "ha_version": "2026.8.0",
                    "protocol_versions": [1],
                    "capabilities": caps,
                },
            )
        )
        ack = await asyncio.wait_for(ws.outbound.get(), 2)
        on = flag and advertised
        assert (ack["payload"].get("voice_registry_only_v1") is True) == on
        sid = ack["payload"]["session_id"]
        payload = {
            "registry_only": True,
            "registry_id": "no-state-ws",
            "entity_id": "sensor.no_state_ws",
            "domain": "sensor",
            "available": False,
            "control_exposed": False,
        }
        await ws.inbound.put(
            message(
                "inventory_full",
                {
                    "session_id": sid,
                    "revision": 1,
                    "batch_index": 0,
                    "batch_count": 1,
                    "entities": [payload],
                },
            )
        )
        if on:
            await ws.inbound.put(message("heartbeat", {"session_id": sid}))
            assert (await asyncio.wait_for(ws.outbound.get(), 2))["type"] == "heartbeat_ack"
            row = await session.scalar(
                select(Entity).where(
                    Entity.installation_id == iid, Entity.ha_registry_id == "no-state-ws"
                )
            )
            assert row is not None and row.available is False and row.control_exposed is False
        else:
            await asyncio.wait_for(task, 2)
            assert ws.close_code == 4002
            row = await session.scalar(
                select(Entity).where(
                    Entity.installation_id == iid, Entity.ha_registry_id == "no-state-ws"
                )
            )
            assert row is None
    finally:
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task


async def test_real_loopback_websocket_negotiates_and_saves(session, seeded_domain, monkeypatch):
    import json
    import socket

    import uvicorn
    import websockets
    from fastapi import FastAPI

    from apps.cloud_api.app.database import get_database_session

    iid = seeded_domain.installation_a_id
    inst = await session.get(Installation, iid)
    inst.status = InstallationStatus.ACTIVE
    secret = "evc_registry_loopback_synthetic"
    session.add(
        ConnectorCredential(
            installation_id=iid, secret_hash=hashlib.sha256(secret.encode()).hexdigest()
        )
    )
    await session.commit()
    monkeypatch.setattr(
        evcp, "get_settings", lambda: SimpleNamespace(voice_selection_v1_enabled=True)
    )
    monkeypatch.setattr(evcp, "REGISTRY_ONLY_NEGOTIATION_ENABLED", True)
    app = FastAPI()
    app.include_router(evcp.router)

    async def override():
        yield session

    app.dependency_overrides[get_database_session] = override
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(32)
    sock.setblocking(False)
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="critical", lifespan="off"))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.02)
        assert server.started
        async with websockets.connect(
            f"ws://127.0.0.1:{port}/connector/v1/ws",
            additional_headers={"Authorization": "Bearer " + secret},
        ) as ws:
            caps = {
                **CONNECTOR_CAPABILITIES,
                "supports_voice_selection_v1": True,
                "supports_voice_registry_only_v1": True,
            }
            await ws.send(
                message(
                    "hello",
                    {
                        "installation_id": str(iid),
                        "connector_version": "0.1.8-beta.5",
                        "ha_version": "2026.8.0",
                        "protocol_versions": [1],
                        "capabilities": caps,
                    },
                )
            )
            ack = json.loads(await asyncio.wait_for(ws.recv(), 3))
            assert ack["payload"]["voice_registry_only_v1"] is True
            sid = ack["payload"]["session_id"]
            payload = {
                "registry_only": True,
                "registry_id": "real-loopback-id",
                "entity_id": "sensor.loopback_synthetic",
                "domain": "sensor",
                "available": False,
                "control_exposed": False,
            }
            await ws.send(
                message(
                    "inventory_full",
                    {
                        "session_id": sid,
                        "revision": 1,
                        "batch_index": 0,
                        "batch_count": 1,
                        "entities": [payload],
                    },
                )
            )
            await ws.send(message("heartbeat", {"session_id": sid}))
            assert json.loads(await asyncio.wait_for(ws.recv(), 3))["type"] == "heartbeat_ack"
            row = await session.scalar(
                select(Entity).where(
                    Entity.installation_id == iid, Entity.ha_registry_id == "real-loopback-id"
                )
            )
            assert row is not None and row.available is False and row.control_exposed is False
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 5)
        sock.close()


async def test_real_ws_existing_media_metadata_and_control_restored(
    session, seeded_domain, monkeypatch
):
    import json
    import socket
    from unittest.mock import AsyncMock

    import uvicorn
    import websockets
    from fastapi import FastAPI

    from apps.cloud_api.app.alexa import alexa_entity_eligible, capabilities, discovery_endpoint
    from apps.cloud_api.app.command_dispatch import CommandDispatchService, PowerCommand
    from apps.cloud_api.app.database import get_database_session

    iid = seeded_domain.installation_a_id
    inst = await session.get(Installation, iid)
    inst.status = InstallationStatus.ACTIVE
    entity = await session.get(Entity, seeded_domain.entity_a_id)
    entity.ha_registry_id = "ws-media"
    entity.ha_entity_id = "media_player.ws_media"
    entity.ha_domain = "media_player"
    entity.supported_features = 128
    entity.device_class = "speaker"
    entity.control_exposed = True
    entity.available = True
    entity.voice_name = "Nome personalizzato"
    entity.attributes_json = {"source_list": ["radio"]}
    secret = "evc_registry_loopback_existing"
    session.add(
        ConnectorCredential(
            installation_id=iid, secret_hash=hashlib.sha256(secret.encode()).hexdigest()
        )
    )
    await session.commit()
    before = (alexa_entity_eligible(entity), capabilities(entity), discovery_endpoint(entity))
    assert before[0]
    monkeypatch.setattr(
        evcp, "get_settings", lambda: SimpleNamespace(voice_selection_v1_enabled=True)
    )
    monkeypatch.setattr(evcp, "REGISTRY_ONLY_NEGOTIATION_ENABLED", True)
    app = FastAPI()
    app.include_router(evcp.router)

    async def override():
        yield session

    app.dependency_overrides[get_database_session] = override
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(32)
    sock.setblocking(False)
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="critical", lifespan="off"))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.02)
        assert server.started
        async with websockets.connect(
            f"ws://127.0.0.1:{port}/connector/v1/ws",
            additional_headers={"Authorization": "Bearer " + secret},
        ) as ws:
            caps = {
                **CONNECTOR_CAPABILITIES,
                "supports_voice_selection_v1": True,
                "supports_voice_registry_only_v1": True,
            }
            await ws.send(
                message(
                    "hello",
                    {
                        "installation_id": str(iid),
                        "connector_version": "0.1.8-beta.5",
                        "ha_version": "2026.8.0",
                        "protocol_versions": [1],
                        "capabilities": caps,
                    },
                )
            )
            ack = json.loads(await asyncio.wait_for(ws.recv(), 3))
            assert ack["payload"]["voice_registry_only_v1"] is True
            sid = ack["payload"]["session_id"]
            payload = {
                "registry_only": True,
                "registry_id": "ws-media",
                "entity_id": "media_player.ws_media",
                "domain": "media_player",
                "friendly_name": "Fallback",
                "available": False,
                "control_exposed": False,
            }

            async def send_full(revision, item):
                await ws.send(
                    message(
                        "inventory_full",
                        {
                            "session_id": sid,
                            "revision": revision,
                            "batch_index": 0,
                            "batch_count": 1,
                            "entities": [item],
                        },
                    )
                )
                await ws.send(message("heartbeat", {"session_id": sid}))
                assert json.loads(await asyncio.wait_for(ws.recv(), 3))["type"] == "heartbeat_ack"

            await send_full(1, payload)
            await session.refresh(entity)
            assert (
                entity.available is False
                and entity.supported_features == 128
                and entity.device_class == "speaker"
                and entity.voice_name == "Nome personalizzato"
            )
            assert (
                alexa_entity_eligible(entity),
                capabilities(entity),
                discovery_endpoint(entity),
            ) == before
            router = AsyncMock()
            out = await CommandDispatchService(session, router).dispatch(
                iid, "ws-media", PowerCommand(operation="power_on")
            )
            assert out.status == "unavailable"
            router.dispatch.assert_not_awaited()
            restored = {
                **payload,
                "registry_only": False,
                "available": True,
                "control_exposed": True,
                "supported_features": 128,
                "device_class": "speaker",
                "attributes": {"source_list": ["radio"]},
                "state": "off",
            }
            await send_full(2, restored)
            await session.refresh(entity)
            assert entity.available is True and entity.voice_name == "Nome personalizzato"
            assert (
                alexa_entity_eligible(entity),
                capabilities(entity),
                discovery_endpoint(entity),
            ) == before
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 5)
        sock.close()

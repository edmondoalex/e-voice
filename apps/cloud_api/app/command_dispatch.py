"""Typed, installation-scoped cloud command dispatch boundary for M6."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Literal, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .domain.models import AuditEvent, Entity, Installation
from .evcp import CommandResultPayload

COMMAND_TIMEOUT_SECONDS = 8.0


class StrictCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PowerCommand(StrictCommand):
    operation: Literal["power_on", "power_off"]


class BrightnessCommand(StrictCommand):
    operation: Literal["set_brightness"]
    brightness: int = Field(ge=0, le=255)


class ColorCommand(StrictCommand):
    operation: Literal["set_color"]
    rgb_color: tuple[
        Annotated[int, Field(ge=0, le=255)],
        Annotated[int, Field(ge=0, le=255)],
        Annotated[int, Field(ge=0, le=255)],
    ]


class ColorTemperatureCommand(StrictCommand):
    operation: Literal["set_color_temperature"]
    color_temp_kelvin: float = Field(gt=0, le=20_000)


class CoverCommand(StrictCommand):
    operation: Literal["open", "close", "stop"]


class PositionCommand(StrictCommand):
    operation: Literal["set_position"]
    position: int = Field(ge=0, le=100)


class TargetTemperatureCommand(StrictCommand):
    operation: Literal["set_target_temperature"]
    temperature: float = Field(ge=-100, le=100)


class HvacModeCommand(StrictCommand):
    operation: Literal["set_hvac_mode"]
    hvac_mode: str = Field(min_length=1, max_length=64)


class PercentageCommand(StrictCommand):
    operation: Literal["set_percentage"]
    percentage: int = Field(ge=0, le=100)


class ActivateCommand(StrictCommand):
    operation: Literal["activate"]


class PressCommand(StrictCommand):
    operation: Literal["press"]


class NumberCommand(StrictCommand):
    operation: Literal["set_value"]
    value: float


class SelectCommand(StrictCommand):
    operation: Literal["select_option"]
    option: str = Field(min_length=1, max_length=255)


type CommandSpec = Annotated[
    PowerCommand
    | BrightnessCommand
    | ColorCommand
    | ColorTemperatureCommand
    | CoverCommand
    | PositionCommand
    | TargetTemperatureCommand
    | HvacModeCommand
    | PercentageCommand
    | ActivateCommand
    | PressCommand
    | NumberCommand
    | SelectCommand,
    Field(discriminator="operation"),
]
command_adapter: TypeAdapter[CommandSpec] = TypeAdapter(CommandSpec)


class CommandRouter(Protocol):
    async def dispatch(
        self,
        installation_id: UUID,
        command_id: UUID,
        registry_id: str,
        command: dict[str, object],
        timeout_seconds: float,
        correlation_id: UUID | None = None,
    ) -> CommandResultPayload: ...


@dataclass(frozen=True, slots=True)
class DispatchOutcome:
    command_id: UUID
    status: str
    error_code: str | None


class CommandDispatchService:
    """Authorize an M5 entity before routing a typed abstract command."""

    def __init__(self, session: AsyncSession, router: CommandRouter) -> None:
        self._session, self._router = session, router

    async def dispatch(
        self,
        installation_id: UUID,
        registry_id: str,
        command: CommandSpec,
        *,
        command_id: UUID | None = None,
        correlation_id: UUID | None = None,
    ) -> DispatchOutcome:
        request_id = command_id or uuid4()
        installation = await self._session.get(Installation, installation_id)
        entity = await self._entity(installation_id, registry_id)
        if installation is None:
            return DispatchOutcome(request_id, "target_not_found", "ENTITY_NOT_FOUND")
        if entity is None:
            return await self._record(
                installation,
                registry_id,
                command.operation,
                DispatchOutcome(request_id, "target_not_found", "ENTITY_NOT_FOUND"),
            )
        if entity.deleted_at is not None:
            return await self._record(
                installation,
                registry_id,
                command.operation,
                DispatchOutcome(request_id, "target_not_exposed", "ENTITY_NOT_EXPOSED"),
            )
        if not entity.available:
            return await self._record(
                installation,
                registry_id,
                command.operation,
                DispatchOutcome(request_id, "unavailable", "ENTITY_UNAVAILABLE"),
            )
        diagnostic = {
            "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "correlation_id": str(correlation_id) if correlation_id else None,
            "command_id": str(request_id),
            "installation_id": str(installation.id),
            "entity_id": str(entity.id),
            "ha_entity_id": entity.ha_entity_id,
            "registry_id": registry_id,
            "operation": command.operation,
            "payload": command.model_dump(mode="json"),
        }
        self._session.add(
            AuditEvent(
                tenant_id=installation.tenant_id,
                installation_id=installation.id,
                source="alexa" if correlation_id else "evcp",
                event_type="dispatcher.command_created",
                request_id=str(request_id),
                payload_redacted_json=diagnostic,
                result="queued",
            )
        )
        await self._session.commit()
        dispatch_arguments = (
            installation_id,
            request_id,
            registry_id,
            command.model_dump(mode="json"),
            COMMAND_TIMEOUT_SECONDS,
        )
        result = (
            await self._router.dispatch(*dispatch_arguments, correlation_id)
            if correlation_id is not None
            else await self._router.dispatch(*dispatch_arguments)
        )
        session_reason = next(
            (
                item.get("reason")
                for item in result.diagnostics
                if item.get("event_type")
                in {"evcp.session_decision", "evcp.dispatch_session_selected"}
            ),
            None,
        )
        not_sent_reasons = {
            "no_registered_session",
            "session_transitioning",
            "heartbeat_expired",
            "websocket_client_not_connected",
            "websocket_application_not_connected",
            "websocket_send_failed",
        }
        command_sent = (
            result.error_code != "INSTALLATION_OFFLINE" and session_reason not in not_sent_reasons
        )
        self._session.add(
            AuditEvent(
                tenant_id=installation.tenant_id,
                installation_id=installation.id,
                source="alexa" if correlation_id else "evcp",
                event_type="evcp.command_sent",
                request_id=str(request_id),
                payload_redacted_json=diagnostic
                | {
                    "session_id": str(result.session_id),
                    "send_result": "sent" if command_sent else "not_sent",
                    "evcp_payload": {
                        "command_id": str(request_id),
                        "correlation_id": str(correlation_id) if correlation_id else None,
                        "registry_id": registry_id,
                        "command": command.model_dump(mode="json"),
                    },
                },
                result="sent" if command_sent else "not_sent",
            )
        )
        for item in result.diagnostics:
            event_type = str(item.get("event_type", "connector.diagnostic"))
            component_source = (
                "homeassistant" if event_type.startswith("homeassistant.") else "connector"
            )
            self._session.add(
                AuditEvent(
                    tenant_id=installation.tenant_id,
                    installation_id=installation.id,
                    source="alexa" if correlation_id else component_source,
                    event_type=event_type,
                    request_id=str(request_id),
                    payload_redacted_json=diagnostic
                    | {"component_source": component_source}
                    | item,
                    result=(
                        "success"
                        if item.get("success") is True
                        else "failure"
                        if item.get("success") is False
                        else "observed"
                    ),
                )
            )
        self._session.add(
            AuditEvent(
                tenant_id=installation.tenant_id,
                installation_id=installation.id,
                source="alexa" if correlation_id else "evcp",
                event_type="command.final_summary",
                request_id=str(request_id),
                payload_redacted_json=diagnostic
                | {
                    "final_status": result.status,
                    "error_code": result.error_code,
                    "connector_received": any(
                        item.get("event_type") == "connector.command_received"
                        for item in result.diagnostics
                    ),
                    "service_result": next(
                        (
                            item.get("success")
                            for item in result.diagnostics
                            if item.get("event_type") == "homeassistant.service_result"
                        ),
                        None,
                    ),
                },
                result=result.status,
            )
        )
        await self._session.commit()
        return await self._record(
            installation,
            registry_id,
            command.operation,
            DispatchOutcome(request_id, result.status, result.error_code),
        )

    async def _entity(self, installation_id: UUID, registry_id: str) -> Entity | None:
        result = await self._session.scalars(
            select(Entity).where(
                Entity.installation_id == installation_id,
                Entity.ha_registry_id == registry_id,
            )
        )
        return result.one_or_none()

    async def _record(
        self,
        installation: Installation,
        registry_id: str,
        operation: str,
        outcome: DispatchOutcome,
    ) -> DispatchOutcome:
        self._session.add(
            AuditEvent(
                tenant_id=installation.tenant_id,
                installation_id=installation.id,
                source="evcp",
                event_type="command_dispatch",
                request_id=str(outcome.command_id),
                payload_redacted_json={"registry_id": registry_id, "operation": operation},
                result=outcome.status,
            )
        )
        await self._session.commit()
        return outcome

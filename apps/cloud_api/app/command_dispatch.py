"""Typed, installation-scoped cloud command dispatch boundary for M6."""

from __future__ import annotations

from dataclasses import dataclass
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
        result = await self._router.dispatch(
            installation_id,
            request_id,
            registry_id,
            command.model_dump(mode="json"),
            COMMAND_TIMEOUT_SECONDS,
        )
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

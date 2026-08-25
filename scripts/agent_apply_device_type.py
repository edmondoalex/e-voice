from pathlib import Path


def once(path: str, old: str, new: str) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected 1 match, got {count}: {old[:80]!r}")
    file.write_text(text.replace(old, new, 1), encoding="utf-8")


once(
    "apps/cloud_api/app/domain/models.py",
    '    alexa_cover_mode: Mapped[str | None] = mapped_column(String(20))\n',
    '    alexa_cover_mode: Mapped[str | None] = mapped_column(String(20))\n'
    '    alexa_device_type: Mapped[str | None] = mapped_column(String(32))\n',
)

once(
    "apps/cloud_api/app/admin_console.py",
    'from .cover_modes import COVER_STOP, effective_cover_mode, validate_cover_mode\n',
    'from .alexa_device_types import allowed_alexa_device_types, validate_alexa_device_type\n'
    'from .cover_modes import COVER_STOP, effective_cover_mode, validate_cover_mode\n',
)
once(
    "apps/cloud_api/app/admin_console.py",
    '    cover_mode = ""\n    if entity.ha_domain == "cover":\n',
    '    selected_device_type = entity.alexa_device_type or "auto"\n'
    '    device_type_labels = {\n'
    '        "auto": "Automatico (in base al tipo Home Assistant)",\n'
    '        "switch": "Interruttore — accendi / spegni",\n'
    '        "light": "Luce — accendi / spegni",\n'
    '        "outlet": "Presa — accendi / spegni",\n'
    '        "gate": "Cancello — apri / chiudi",\n'
    '    }\n'
    '    allowed_types = ("auto", *allowed_alexa_device_types(entity))\n'
    '    device_type_options = "".join(\n'
    '        f\'<option value="{value}"{" selected" if value == selected_device_type else ""}>{device_type_labels[value]}</option>\'\n'
    '        for value in allowed_types\n'
    '    )\n'
    '    device_type = f"""<label class="field"><b>Tipo dispositivo Alexa</b><select name="alexa_device_type">{device_type_options}</select><span class="muted">Non cambia il tipo reale in Home Assistant. Determina categoria, capability e verbi vocali pubblicati ad Alexa.</span></label>"""\n'
    '    cover_mode = ""\n'
    '    if entity.ha_domain == "cover":\n',
)
once(
    "apps/cloud_api/app/admin_console.py",
    '{cover_mode}\n<p><b>Nome dashboard effettivo:</b>',
    '{device_type}\n{cover_mode}\n<p><b>Nome dashboard effettivo:</b>',
)
once(
    "apps/cloud_api/app/admin_console.py",
    '        entity.alexa_cover_mode,\n    )\n    try:\n',
    '        entity.alexa_cover_mode,\n        entity.alexa_device_type,\n    )\n    try:\n',
)
once(
    "apps/cloud_api/app/admin_console.py",
    '            entity.voice_aliases = clean_voice_aliases(\n'
    '                re.split(r"[\\r\\n,]+", values.get("voice_aliases", ""))\n'
    '            )\n'
    '            if entity.ha_domain == "cover":\n',
    '            entity.voice_aliases = clean_voice_aliases(\n'
    '                re.split(r"[\\r\\n,]+", values.get("voice_aliases", ""))\n'
    '            )\n'
    '            requested_device_type = values.get("alexa_device_type", "auto")\n'
    '            entity.alexa_device_type = (\n'
    '                None\n'
    '                if requested_device_type == "auto"\n'
    '                else validate_alexa_device_type(entity, requested_device_type)\n'
    '            )\n'
    '            if entity.ha_domain == "cover":\n',
)
admin = Path("apps/cloud_api/app/admin_console.py")
text = admin.read_text(encoding="utf-8")
old_rollback = (
    '            entity.voice_aliases,\n'
    '            entity.alexa_cover_mode,\n'
    '        ) = previous\n'
)
if text.count(old_rollback) != 2:
    raise SystemExit(f"admin rollback expected 2 matches, got {text.count(old_rollback)}")
text = text.replace(
    old_rollback,
    '            entity.voice_aliases,\n'
    '            entity.alexa_cover_mode,\n'
    '            entity.alexa_device_type,\n'
    '        ) = previous\n',
    2,
)
admin.write_text(text, encoding="utf-8")
once(
    "apps/cloud_api/app/admin_console.py",
    '        entity.alexa_cover_mode,\n    )\n    changed_fields = [\n',
    '        entity.alexa_cover_mode,\n        entity.alexa_device_type,\n    )\n    changed_fields = [\n',
)
once(
    "apps/cloud_api/app/admin_console.py",
    '("display_name", "voice_name", "voice_aliases", "alexa_cover_mode"),\n',
    '("display_name", "voice_name", "voice_aliases", "alexa_cover_mode", "alexa_device_type"),\n',
)
once(
    "apps/cloud_api/app/admin_console.py",
    '    return _names_page(installation, entity, context, _csrf(context), message="Nomi salvati.")\n',
    '    return _names_page(installation, entity, context, _csrf(context), message="Configurazione entità salvata.")\n',
)

once(
    "apps/cloud_api/app/alexa.py",
    'from .alexa_discovery_audit import record_discovery\n',
    'from .alexa_device_types import is_gate_override, overridden_display_category\n'
    'from .alexa_discovery_audit import record_discovery\n',
)
once(
    "apps/cloud_api/app/alexa.py",
    'def capabilities(entity: Entity) -> list[dict[str, Any]]:\n',
    'def _gate_mode_capability() -> dict[str, Any]:\n'
    '    return _capability("Alexa.ModeController", ["mode"]) | {\n'
    '        "instance": "Gate.Position",\n'
    '        "capabilityResources": {\n'
    '            "friendlyNames": [\n'
    '                {"@type": "asset", "value": {"assetId": "Alexa.Setting.Opening"}},\n'
    '                {"@type": "text", "value": {"text": "Cancello", "locale": "it-IT"}},\n'
    '            ]\n'
    '        },\n'
    '        "configuration": {\n'
    '            "ordered": False,\n'
    '            "supportedModes": [\n'
    '                {\n'
    '                    "value": "Position.Up",\n'
    '                    "modeResources": {"friendlyNames": [\n'
    '                        {"@type": "asset", "value": {"assetId": "Alexa.Value.Open"}},\n'
    '                        {"@type": "text", "value": {"text": "Aperto", "locale": "it-IT"}},\n'
    '                    ]},\n'
    '                },\n'
    '                {\n'
    '                    "value": "Position.Down",\n'
    '                    "modeResources": {"friendlyNames": [\n'
    '                        {"@type": "asset", "value": {"assetId": "Alexa.Value.Close"}},\n'
    '                        {"@type": "text", "value": {"text": "Chiuso", "locale": "it-IT"}},\n'
    '                    ]},\n'
    '                },\n'
    '            ],\n'
    '        },\n'
    '        "semantics": {\n'
    '            "actionMappings": [\n'
    '                {"@type": "ActionsToDirective", "actions": ["Alexa.Actions.Open"], "directive": {"name": "SetMode", "payload": {"mode": "Position.Up"}}},\n'
    '                {"@type": "ActionsToDirective", "actions": ["Alexa.Actions.Close"], "directive": {"name": "SetMode", "payload": {"mode": "Position.Down"}}},\n'
    '            ],\n'
    '            "stateMappings": [\n'
    '                {"@type": "StatesToValue", "states": ["Alexa.States.Open"], "value": "Position.Up"},\n'
    '                {"@type": "StatesToValue", "states": ["Alexa.States.Closed"], "value": "Position.Down"},\n'
    '            ],\n'
    '        },\n'
    '    }\n\n\n'
    'def capabilities(entity: Entity) -> list[dict[str, Any]]:\n',
)
once(
    "apps/cloud_api/app/alexa.py",
    '    if entity.ha_domain in {"light", "switch", "fan"}:\n'
    '        result.append(_capability("Alexa.PowerController", ["powerState"]))\n',
    '    if entity.ha_domain in {"light", "switch", "fan"} and not is_gate_override(entity):\n'
    '        result.append(_capability("Alexa.PowerController", ["powerState"]))\n'
    '    if is_gate_override(entity):\n'
    '        result.append(_gate_mode_capability())\n',
)
once(
    "apps/cloud_api/app/alexa.py",
    'def discovery_endpoint(entity: Entity) -> dict[str, Any]:\n    category = {\n',
    'def discovery_endpoint(entity: Entity) -> dict[str, Any]:\n    category = overridden_display_category(entity) or {\n',
)
once(
    "apps/cloud_api/app/alexa.py",
    '    if entity.ha_domain in {"light", "switch", "fan"}:\n        props.append(\n',
    '    if is_gate_override(entity):\n'
    '        props.append(\n'
    '            _property(\n'
    '                "Alexa.ModeController",\n'
    '                "mode",\n'
    '                "Position.Up" if entity.state == "on" else "Position.Down",\n'
    '                instance="Gate.Position",\n'
    '            )\n'
    '        )\n'
    '    elif entity.ha_domain in {"light", "switch", "fan"}:\n'
    '        props.append(\n',
)
once(
    "apps/cloud_api/app/alexa.py",
    '    if (namespace, name) in mapping:\n        if entity is not None and entity.ha_domain == "cover":\n',
    '    if (namespace, name) in mapping:\n'
    '        if entity is not None and is_gate_override(entity) and namespace == "Alexa.PowerController":\n'
    '            return None\n'
    '        if entity is not None and entity.ha_domain == "cover":\n',
)
once(
    "apps/cloud_api/app/alexa.py",
    '    if namespace == "Alexa.ModeController" and name == "SetMode":\n        if (\n',
    '    if namespace == "Alexa.ModeController" and name == "SetMode":\n'
    '        if entity is not None and is_gate_override(entity):\n'
    '            gate_modes = {"Position.Up": "power_on", "Position.Down": "power_off"}\n'
    '            requested_mode = payload.get("mode")\n'
    '            operation = gate_modes.get(requested_mode) if isinstance(requested_mode, str) else None\n'
    '            return {"operation": operation} if operation is not None else None\n'
    '        if (\n',
)
once(
    "apps/cloud_api/app/alexa.py",
    '    replacements: dict[tuple[str, str], Any] = {}\n    if operation == "set_target_temperature":\n',
    '    replacements: dict[tuple[str, str], Any] = {}\n'
    '    if is_gate_override(entity) and operation in {"power_on", "power_off"}:\n'
    '        properties = [\n'
    '            item\n'
    '            for item in properties\n'
    '            if (item["namespace"], item["name"]) != ("Alexa.ModeController", "mode")\n'
    '        ]\n'
    '        properties.append(\n'
    '            _property(\n'
    '                "Alexa.ModeController",\n'
    '                "mode",\n'
    '                "Position.Up" if operation == "power_on" else "Position.Down",\n'
    '                instance="Gate.Position",\n'
    '            )\n'
    '        )\n'
    '        return properties\n'
    '    if operation == "set_target_temperature":\n',
)

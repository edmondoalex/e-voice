"""Nucleo conversazionale deterministico e indipendente dal canale vocale.

Il modulo costituisce il confine di sicurezza tra un futuro modello linguistico e
le entità sincronizzate. Non esegue comandi e non accede direttamente a Home
Assistant: risolve soltanto un insieme già autorizzato di istantanee.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import StrEnum


class ReplyStatus(StrEnum):
    ANSWERED = "answered"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"
    UNAVAILABLE = "unavailable"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class EntitySnapshot:
    """Campi sicuri di un'entità già autorizzata per la conversazione."""

    entity_id: str
    name: str
    domain: str
    state: str | None
    unit: str | None = None
    device_class: str | None = None
    area: str | None = None
    aliases: tuple[str, ...] = ()
    available: bool = True
    observed_at: datetime | None = None
    category: str | None = None


@dataclass(frozen=True, slots=True)
class Evidence:
    entity_id: str
    name: str
    value: str | None
    unit: str | None
    observed_at: datetime | None


@dataclass(frozen=True, slots=True)
class ConversationReply:
    status: ReplyStatus
    speech: str
    intent: str | None = None
    evidence: tuple[Evidence, ...] = ()
    candidates: tuple[str, ...] = ()
    diagnostics: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class _Intent:
    name: str
    device_class: str | None
    subject_terms: tuple[str, ...]
    domain: str = "sensor"
    prefer_lowest: bool = False


_INTENTS = (
    _Intent(
        "alarm_status",
        None,
        ("allarme", "antifurto", "sistema di allarme", "stato allarme"),
    ),
    _Intent(
        "lock_summary",
        None,
        ("tutte le serrature", "tutti gli accessi", "riepilogo serrature"),
        domain="lock",
    ),
    _Intent(
        "lock_status",
        None,
        ("stato serratura", "serratura", "porta", "portoncino"),
        domain="lock",
    ),
    _Intent(
        "opening_summary",
        None,
        (
            "porte o portoni aperti",
            "aperture aperte",
            "porte aperte",
            "portoni aperti",
            "tutte le porte",
            "tutti i portoni",
            "tutte le aperture",
        ),
        domain="binary_sensor",
    ),
    _Intent(
        "opening_status",
        None,
        ("stato apertura", "porta", "portone", "garage"),
        domain="binary_sensor",
    ),
    _Intent(
        "exported_energy_today",
        "energy",
        ("energia esportata", "energia oggi esportata", "esportata", "immessa oggi"),
    ),
    _Intent(
        "imported_energy_today",
        "energy",
        ("energia importata", "energia oggi importata", "importata", "prelevata oggi"),
    ),
    _Intent(
        "produced_energy_today",
        "energy",
        (
            "energia prodotta oggi",
            "energia oggi prodotta",
            "produzione di oggi",
            "totale prodotto oggi",
            "totale prodotta oggi",
        ),
    ),
    _Intent(
        "consumed_energy_today",
        "energy",
        (
            "energia consumata oggi",
            "energia oggi consumata",
            "consumo di oggi",
            "totale consumato oggi",
            "totale consumata oggi",
        ),
    ),
    _Intent(
        "consumption_power",
        "power",
        ("consumo", "consumi", "consuma", "assorbimento"),
    ),
    _Intent(
        "photovoltaic_power",
        "power",
        ("fotovoltaico", "fotovoltaici", "produzione fotovoltaica", "pannelli solari", "pv"),
    ),
    _Intent(
        "grid_power",
        "power",
        ("potenza rete", "rete elettrica", "prelievo", "immissione", "import export"),
    ),
    _Intent(
        "acs_temperature",
        "temperature",
        ("acs", "acqua calda", "bollitore", "boiler"),
    ),
    _Intent(
        "battery_level",
        "battery",
        ("batteria", "batterie", "accumulo"),
    ),
    _Intent(
        "temperature",
        "temperature",
        ("temperatura", "temperature", "gradi", "caldo", "freddo"),
    ),
)

_COMMAND_TERMS = (
    "apri",
    "chiudi",
    "attiva",
    "disattiva",
    "inserisci",
    "disinserisci",
    "accendi",
    "spegni",
)


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    without_accents = "".join(char for char in decomposed if not unicodedata.combining(char))
    tokens = re.findall(r"[a-z0-9]+", without_accents)
    normalized: list[str] = []
    index = 0
    while index < len(tokens):
        end = index
        while end < len(tokens) and len(tokens[end]) == 1 and tokens[end].isalnum():
            end += 1
        if end - index >= 2:
            normalized.append("".join(tokens[index:end]))
            index = end
            continue
        normalized.append(tokens[index])
        index += 1
    return " ".join(normalized)


def _contains_phrase(text: str, phrase: str) -> bool:
    normalized_phrase = _normalize(phrase)
    return bool(re.search(rf"(?:^| )({re.escape(normalized_phrase)})(?: |$)", text))


def _format_value(entity: EntitySnapshot) -> str:
    value = entity.state or ""
    unit = entity.unit
    temperature_categories = {"temperature", "acs_temperature", "thermal_temperature"}
    if not unit:
        unit = {
            "battery": "%",
            "battery_level": "%",
            "energy": "kWh",
            "power": "W",
            "temperature": "°C",
        }.get(entity.device_class or "")
    if not unit and entity.category in temperature_categories:
        unit = "°C"
    normalized_unit = (unit or "").strip().casefold()
    if normalized_unit in {"kwh", "wh"}:
        try:
            energy = Decimal(value)
            if normalized_unit == "kwh" and abs(energy) < 1:
                watt_hours = (energy * 1000).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
                return f"{watt_hours} wattora"
            if normalized_unit == "wh":
                watt_hours = energy.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
                return f"{watt_hours} wattora"
            kilowatt_hours = energy.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            spoken = format(kilowatt_hours, "f").rstrip("0").rstrip(".").replace(".", ",")
            return f"{spoken} chilowattora"
        except InvalidOperation:
            pass
    if (
        unit in {"°C", "°F"}
        or entity.device_class == "temperature"
        or entity.category in temperature_categories
    ):
        try:
            precision = Decimal("0.1") if entity.domain == "climate" else Decimal("1")
            value = str(Decimal(value).quantize(precision, rounding=ROUND_HALF_UP))
            value = value.replace(".", ",")
        except InvalidOperation:
            pass
    if not unit:
        return value
    if unit in {"%", "°C", "°F"}:
        return f"{value}{unit}"
    return f"{value} {unit}".strip()


def _without_leading_word(value: str, word: str) -> str:
    normalized = _normalize(value)
    normalized_word = _normalize(word)
    if normalized == normalized_word:
        return ""
    if normalized.startswith(f"{normalized_word} "):
        return value[len(word) :].strip()
    return value


def _has_numeric_state(entity: EntitySnapshot) -> bool:
    if entity.state is None:
        return False
    try:
        float(entity.state)
    except ValueError:
        return False
    return True


def _format_time(value: datetime | None) -> str | None:
    if value is None:
        return None
    localized = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return localized.strftime("%H:%M")


class ConversationEngine:
    """Risolve le prime domande informative senza invocare un modello generativo."""

    def ask(
        self, utterance: str, entities: Iterable[EntitySnapshot], *, now: datetime | None = None
    ) -> ConversationReply:
        text = _normalize(utterance)
        if not text:
            return ConversationReply(ReplyStatus.UNSUPPORTED, "Non ho ricevuto una domanda.")
        if any(_contains_phrase(text, term) for term in _COMMAND_TERMS):
            return ConversationReply(
                ReplyStatus.UNSUPPORTED,
                "Il laboratorio è in sola lettura e non esegue comandi.",
            )

        snapshots = tuple(entities)
        intent = self._detect_intent(text)
        if intent is None:
            intent = self._detect_named_entity_intent(text, snapshots)
        if intent is None:
            return ConversationReply(
                ReplyStatus.UNSUPPORTED,
                "Per ora posso leggere fotovoltaico, consumi, potenza di rete, "
                "temperatura ACS, temperature e batterie.",
            )

        if intent.name == "lock_summary":
            return self._summarize_locks(snapshots)
        if intent.name == "opening_summary":
            return self._summarize_openings(snapshots)

        # Alcune formulazioni libere vengono instradate come stato singolo anche
        # quando chiedono tutte le serrature. Non devono passare dal riepilogo
        # numerico generico: locked/unlocked sono stati testuali validi.
        if intent.name == "lock_status" and self._requests_all(text):
            return self._summarize_locks(snapshots)

        multiple_photovoltaics = (
            all(_contains_phrase(text, site) for site in ("sas", "privato"))
            or _contains_phrase(text, "tutti i fotovoltaici")
            or _contains_phrase(text, "tutti gli impianti fotovoltaici")
            or _contains_phrase(text, "entrambi i fotovoltaici")
        )
        if intent.name == "photovoltaic_power" and multiple_photovoltaics:
            combined = self._summarize_site_values(text, intent, snapshots)
            if combined is not None:
                return combined

        if self._requests_all(text) or self._requests_both_sites(text):
            combined = self._summarize_all_values(text, intent, snapshots)
            if combined is not None:
                return combined

        candidates = self._rank(text, intent, snapshots)
        if not candidates:
            return ConversationReply(
                ReplyStatus.NOT_FOUND,
                "Non trovo un sensore autorizzato adatto a questa domanda.",
                intent=intent.name,
            )

        best_score = candidates[0][0]
        best = [entity for score, entity in candidates if score == best_score]
        if len(best) > 1:
            names = tuple(entity.name for entity in best[:4])
            return ConversationReply(
                ReplyStatus.AMBIGUOUS,
                f"Trovo più sensori: {', '.join(names)}. Quale vuoi usare?",
                intent=intent.name,
                candidates=names,
            )

        entity = best[0]
        evidence = Evidence(
            entity.entity_id, entity.name, entity.state, entity.unit, entity.observed_at
        )
        if not entity.available or entity.state in {None, "unknown", "unavailable"}:
            return ConversationReply(
                ReplyStatus.UNAVAILABLE,
                f"Il sensore {entity.name} non è disponibile.",
                intent=intent.name,
                evidence=(evidence,),
            )
        if intent.name not in {
            "alarm_status",
            "lock_status",
            "opening_status",
        } and not _has_numeric_state(entity):
            return ConversationReply(
                ReplyStatus.UNAVAILABLE,
                f"Il sensore {entity.name} non contiene un valore numerico valido.",
                intent=intent.name,
                evidence=(evidence,),
            )

        stale = self._is_stale(entity, now)
        speech = self._build_speech(intent, entity)
        if stale:
            observed = _format_time(entity.observed_at)
            speech += f" Il dato potrebbe non essere aggiornato; ultima lettura alle {observed}."
        return ConversationReply(
            ReplyStatus.ANSWERED,
            speech,
            intent=intent.name,
            evidence=(evidence,),
            diagnostics={"freshness": "stale" if stale else "current"},
        )

    @classmethod
    def _summarize_site_values(
        cls,
        text: str,
        intent: _Intent,
        entities: tuple[EntitySnapshot, ...],
    ) -> ConversationReply | None:
        """Risponde a una richiesta esplicita che nomina entrambi gli impianti."""
        ranked = cls._rank(text, intent, entities)
        selected: list[tuple[str, EntitySnapshot]] = []
        for site in ("SAS", "privato"):
            site_key = _normalize(site)
            match = next(
                (
                    entity
                    for _score, entity in ranked
                    if any(
                        _contains_phrase(_normalize(value), site_key)
                        for value in (entity.name, *entity.aliases)
                    )
                ),
                None,
            )
            if match is None:
                return None
            selected.append((site, match))

        evidence = tuple(
            Evidence(entity.entity_id, entity.name, entity.state, entity.unit, entity.observed_at)
            for _site, entity in selected
        )
        unavailable = [
            site
            for site, entity in selected
            if not entity.available
            or entity.state in {None, "unknown", "unavailable"}
            or not _has_numeric_state(entity)
        ]
        if unavailable:
            return ConversationReply(
                ReplyStatus.UNAVAILABLE,
                f"Il fotovoltaico {unavailable[0]} non è disponibile.",
                intent=intent.name,
                evidence=evidence,
            )

        sas = _format_value(selected[0][1])
        private = _format_value(selected[1][1])
        return ConversationReply(
            ReplyStatus.ANSWERED,
            (
                f"Il fotovoltaico SAS sta producendo {sas}, "
                f"mentre quello privato sta producendo {private}."
            ),
            intent=intent.name,
            evidence=evidence,
            diagnostics={"freshness": "current", "scope": "multiple_sites"},
        )

    @staticmethod
    def _requests_all(text: str) -> bool:
        return any(
            _contains_phrase(text, term)
            for term in ("tutti", "tutte", "entrambi", "entrambe", "totale", "complessivo")
        )

    @staticmethod
    def _requests_both_sites(text: str) -> bool:
        return all(_contains_phrase(text, site) for site in ("sas", "privato"))

    @classmethod
    def _summarize_all_values(
        cls,
        text: str,
        intent: _Intent,
        entities: tuple[EntitySnapshot, ...],
    ) -> ConversationReply | None:
        ranked = cls._rank(text, intent, entities)
        matching = tuple(
            entity
            for score, entity in ranked
            if entity.category is not None
            or (
                score > 1
                and any(
                    _contains_phrase(_normalize(" ".join((entity.name, *entity.aliases))), term)
                    for term in intent.subject_terms
                )
            )
        )
        if not matching:
            return None
        evidence = tuple(
            Evidence(entity.entity_id, entity.name, entity.state, entity.unit, entity.observed_at)
            for entity in matching
        )
        def summarized_value(entity: EntitySnapshot) -> str | None:
            if not entity.available or entity.state in {None, "unknown", "unavailable"}:
                return None
            if intent.name == "lock_status":
                return cls._lock_state(entity)
            if intent.name == "opening_status":
                return {
                    "on": "aperta",
                    "open": "aperta",
                    "off": "chiusa",
                    "closed": "chiusa",
                }.get(str(entity.state).casefold(), str(entity.state))
            if intent.name == "alarm_status":
                return str(entity.state)
            return _format_value(entity) if _has_numeric_state(entity) else None

        parts = []
        for entity in sorted(matching, key=lambda item: item.name.casefold()):
            value = summarized_value(entity)
            parts.append(
                f"{entity.name}: {value}" if value is not None else f"{entity.name}: non disponibile"
            )
        return ConversationReply(
            ReplyStatus.ANSWERED,
            "; ".join(parts) + ".",
            intent=intent.name,
            evidence=evidence,
            diagnostics={"freshness": "current", "scope": "multiple_entities"},
        )

    @staticmethod
    def _lock_state(entity: EntitySnapshot) -> str:
        masculine = _normalize(entity.name).startswith("portoncino")
        states = {
            "locked": "chiuso a chiave" if masculine else "chiusa a chiave",
            "unlocked": "aperto" if masculine else "aperta",
            "locking": "in chiusura",
            "unlocking": "in apertura",
            "jammed": "bloccato per un problema" if masculine else "bloccata per un problema",
        }
        return states.get(str(entity.state).casefold(), str(entity.state))

    @classmethod
    def _summarize_locks(cls, entities: tuple[EntitySnapshot, ...]) -> ConversationReply:
        locks = tuple(entity for entity in entities if entity.domain == "lock")
        if not locks:
            return ConversationReply(
                ReplyStatus.NOT_FOUND,
                "Non trovo serrature autorizzate.",
                intent="lock_summary",
            )
        parts = [
            f"{entity.name}: {cls._lock_state(entity)}"
            if entity.available and entity.state not in {None, "unknown", "unavailable"}
            else f"{entity.name}: non disponibile"
            for entity in sorted(locks, key=lambda item: item.name.casefold())
        ]
        evidence = tuple(
            Evidence(entity.entity_id, entity.name, entity.state, entity.unit, entity.observed_at)
            for entity in locks
        )
        return ConversationReply(
            ReplyStatus.ANSWERED,
            ". ".join(parts) + ".",
            intent="lock_summary",
            evidence=evidence,
        )

    @staticmethod
    def _summarize_openings(entities: tuple[EntitySnapshot, ...]) -> ConversationReply:
        openings = tuple(entity for entity in entities if entity.domain == "binary_sensor")
        if not openings:
            return ConversationReply(
                ReplyStatus.NOT_FOUND,
                "Non trovo sensori di apertura autorizzati.",
                intent="opening_summary",
            )
        active = tuple(
            entity
            for entity in openings
            if entity.available and str(entity.state).casefold() in {"on", "open"}
        )
        if not active:
            speech = "Tutte le porte e i portoni controllati risultano chiusi."
        else:
            speech = (
                "Risultano aperti: "
                + ", ".join(
                    entity.name for entity in sorted(active, key=lambda item: item.name.casefold())
                )
                + "."
            )
        evidence = tuple(
            Evidence(entity.entity_id, entity.name, entity.state, entity.unit, entity.observed_at)
            for entity in openings
        )
        return ConversationReply(
            ReplyStatus.ANSWERED,
            speech,
            intent="opening_summary",
            evidence=evidence,
        )

    @staticmethod
    def _detect_intent(text: str) -> _Intent | None:
        opening_summary = next(intent for intent in _INTENTS if intent.name == "opening_summary")
        if any(_contains_phrase(text, term) for term in opening_summary.subject_terms):
            return opening_summary
        if _contains_phrase(text, "stato apertura"):
            return next(intent for intent in _INTENTS if intent.name == "opening_status")
        for intent in _INTENTS:
            if any(_contains_phrase(text, term) for term in intent.subject_terms):
                return intent
        return None

    @staticmethod
    def _detect_named_entity_intent(
        text: str, entities: tuple[EntitySnapshot, ...]
    ) -> _Intent | None:
        matching_classes = {
            entity.device_class
            for entity in entities
            if any(_contains_phrase(text, value) for value in (entity.name, *entity.aliases))
        }
        if matching_classes == {"temperature"}:
            return next(intent for intent in _INTENTS if intent.name == "temperature")
        if matching_classes == {"battery"}:
            return next(intent for intent in _INTENTS if intent.name == "battery_level")
        if matching_classes == {"energy"}:
            matching_text = " ".join(
                value
                for entity in entities
                if any(_contains_phrase(text, value) for value in (entity.name, *entity.aliases))
                for value in (entity.name, *entity.aliases)
            )
            normalized_names = _normalize(matching_text)
            if "esportata" in normalized_names or "export" in normalized_names:
                return next(intent for intent in _INTENTS if intent.name == "exported_energy_today")
            if "importata" in normalized_names or "import" in normalized_names:
                return next(intent for intent in _INTENTS if intent.name == "imported_energy_today")
            if "prodotta" in normalized_names or "production" in normalized_names:
                return next(intent for intent in _INTENTS if intent.name == "produced_energy_today")
            if "consumata" in normalized_names or "consumption" in normalized_names:
                return next(intent for intent in _INTENTS if intent.name == "consumed_energy_today")
        if matching_classes == {"power"}:
            matching_text = " ".join(
                value
                for entity in entities
                if any(_contains_phrase(text, value) for value in (entity.name, *entity.aliases))
                for value in (entity.name, *entity.aliases)
            )
            if any(term in _normalize(matching_text) for term in ("consumo", "consumi")):
                return next(intent for intent in _INTENTS if intent.name == "consumption_power")
        return None

    @staticmethod
    def _rank(
        text: str, intent: _Intent, entities: tuple[EntitySnapshot, ...]
    ) -> list[tuple[int, EntitySnapshot]]:
        ranked: list[tuple[int, EntitySnapshot]] = []
        for entity in entities:
            compatible_categories = {
                "temperature": {"temperature", "thermal_temperature"},
            }.get(intent.name, {intent.name})
            if entity.category is not None and entity.category not in compatible_categories:
                continue
            if entity.category is None:
                thermostat_temperature = (
                    intent.device_class == "temperature" and entity.domain == "climate"
                )
                if entity.domain != intent.domain and not thermostat_temperature:
                    continue
                if intent.device_class is not None and entity.device_class != intent.device_class:
                    continue
            searchable = (entity.name, *entity.aliases)
            score = 1
            if entity.area and _contains_phrase(text, entity.area):
                score += 8
            if any(_contains_phrase(text, value) for value in searchable):
                score += 12
            if any(
                _contains_phrase(_normalize(" ".join(searchable)), term)
                for term in intent.subject_terms
            ):
                score += 4
            query_words = set(text.split())
            searchable_words = set(_normalize(" ".join(searchable)).split())
            score += 2 * len(query_words & searchable_words)
            ranked.append((score, entity))
        return sorted(ranked, key=lambda item: (-item[0], item[1].name.casefold()))

    @staticmethod
    def _is_stale(entity: EntitySnapshot, now: datetime | None) -> bool:
        if entity.observed_at is None:
            return False
        reference = now or datetime.now(UTC)
        observed = entity.observed_at
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=UTC)
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=UTC)
        return (reference - observed).total_seconds() > 15 * 60

    @staticmethod
    def _build_speech(intent: _Intent, entity: EntitySnapshot) -> str:
        value = _format_value(entity)
        if intent.name == "alarm_status":
            return f"Lo stato dell'allarme è {entity.state}."
        if intent.name == "lock_status":
            return f"{entity.name} è {ConversationEngine._lock_state(entity)}."
        if intent.name == "opening_status":
            states = {"on": "aperta", "open": "aperta", "off": "chiusa", "closed": "chiusa"}
            state = states.get(str(entity.state).casefold(), str(entity.state))
            return f"{entity.name} è {state}."
        if intent.name == "photovoltaic_power":
            return f"In questo momento il fotovoltaico sta producendo {value}."
        if intent.name == "consumption_power":
            qualifier = _without_leading_word(entity.name, "consumo istantaneo")
            qualifier = _without_leading_word(qualifier, "consumo")
            subject = f"Il consumo {qualifier}".strip()
            return f"{subject} in questo momento è {value}."
        if intent.name == "grid_power":
            qualifier = _without_leading_word(entity.name, "potenza rete")
            subject = f"La potenza di rete {qualifier}".strip()
            return f"{subject} in questo momento è {value}."
        if intent.name == "exported_energy_today":
            site = "SAS" if "sas" in _normalize(entity.name).split() else "privato"
            return f"Oggi l'impianto {site} ha esportato {value}."
        if intent.name == "imported_energy_today":
            site = "SAS" if "sas" in _normalize(entity.name).split() else "privato"
            return f"Oggi l'impianto {site} ha importato {value}."
        if intent.name == "produced_energy_today":
            site = "SAS" if "sas" in _normalize(entity.name).split() else "privato"
            return f"Oggi l'impianto {site} ha prodotto {value}."
        if intent.name == "consumed_energy_today":
            site = "SAS" if "sas" in _normalize(entity.name).split() else "privato"
            return f"Oggi l'impianto {site} ha consumato {value}."
        if intent.name == "acs_temperature":
            return f"La temperatura dell'acqua calda è {value}."
        if intent.name == "battery_level":
            qualifier = _without_leading_word(entity.name, "batteria")
            subject = f"La batteria {qualifier}".strip()
            return f"{subject} è al {value}."
        area = f" in {entity.area}" if entity.area else ""
        return f"La temperatura{area} è {value}."


class ConversationSession:
    """Mantiene soltanto il contesto necessario a risolvere un chiarimento."""

    def __init__(self, engine: ConversationEngine | None = None) -> None:
        self._engine = engine or ConversationEngine()
        self._pending_utterance: str | None = None

    def ask(
        self, utterance: str, entities: Iterable[EntitySnapshot], *, now: datetime | None = None
    ) -> ConversationReply:
        snapshots = tuple(entities)
        effective_utterance = utterance
        if self._pending_utterance is not None:
            effective_utterance = f"{self._pending_utterance} {utterance}"

        reply = self._engine.ask(effective_utterance, snapshots, now=now)
        if reply.status is ReplyStatus.AMBIGUOUS:
            self._pending_utterance = effective_utterance
        else:
            self._pending_utterance = None
        return reply

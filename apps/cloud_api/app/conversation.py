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
        "consumption_power",
        "power",
        ("consumo", "consumi", "consuma", "assorbimento"),
    ),
    _Intent(
        "photovoltaic_power",
        "power",
        ("fotovoltaico", "produzione fotovoltaica", "pannelli solari", "pv"),
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
        ("temperatura", "gradi", "caldo", "freddo"),
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
    if not unit:
        unit = {
            "battery": "%",
            "battery_level": "%",
            "energy": "kWh",
            "power": "W",
            "temperature": "°C",
        }.get(entity.device_class or "")
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
        if intent.name != "alarm_status" and not _has_numeric_state(entity):
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

    @staticmethod
    def _detect_intent(text: str) -> _Intent | None:
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
                return next(
                    intent for intent in _INTENTS if intent.name == "exported_energy_today"
                )
            if "importata" in normalized_names or "import" in normalized_names:
                return next(
                    intent for intent in _INTENTS if intent.name == "imported_energy_today"
                )
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
            if entity.domain != intent.domain:
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
            normalized = _normalize(entity.state or "")
            state = {
                "solo esterno": "inserito solo sul perimetro esterno",
                "totale": "inserito totalmente",
                "inserito": "inserito",
                "disinserito": "disinserito",
                "off": "disinserito",
                "on": "inserito",
            }.get(normalized, (entity.state or "non disponibile").lower())
            return f"L'allarme è {state}."
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

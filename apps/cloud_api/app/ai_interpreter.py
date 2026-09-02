"""Constrained OpenAI interpreter for read-only Ekonex questions."""

from __future__ import annotations

import json
import logging

import httpx

from .conversation import EntitySnapshot

logger = logging.getLogger(__name__)


class OpenAIQuestionInterpreter:
    def __init__(self, api_key: str, model: str, timeout: float) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout = timeout

    async def interpret(self, question: str, entities: tuple[EntitySnapshot, ...]) -> str | None:
        if not self._api_key:
            return None
        allowed = [
            {"name": entity.name, "aliases": list(entity.aliases), "domain": entity.domain}
            for entity in entities
        ]
        payload = {
            "model": self._model,
            "store": False,
            "max_output_tokens": 100,
            "instructions": (
                "Interpreta una domanda italiana sulla casa. Non rispondere alla domanda e non "
                "inventare dati. Restituisci esclusivamente una query canonica che il motore "
                "Ekonex possa risolvere. Sono ammessi: fotovoltaico, consumo, batteria, potenza "
                "rete, temperatura, energia importata o esportata oggi, stato allarme, stato di "
                "una serratura, tutte le serrature, stato di un'apertura, porte o portoni aperti. "
                "Usa soltanto nomi presenti nell'elenco. Interpreta sole, pannelli e produzione "
                "solare come produzione fotovoltaica istantanea; non come energia importata o "
                "esportata. Se manca il sito, non sceglierlo arbitrariamente: genera la query "
                "generica 'quanto produce il fotovoltaico', così Ekonex potrà chiedere quale "
                "sensore usare. Se la richiesta è un comando, "
                "non riguarda "
                "la casa o non è risolvibile, restituisci una stringa vuota."
            ),
            "input": json.dumps(
                {"question": question, "allowed_entities": allowed}, ensure_ascii=False
            ),
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "ekonex_question",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                        "additionalProperties": False,
                    },
                }
            },
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    "https://api.openai.com/v1/responses",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json=payload,
                )
                response.raise_for_status()
            body = response.json()
            for item in body.get("output", []):
                for content in item.get("content", []):
                    if content.get("type") == "output_text":
                        query = json.loads(content.get("text", "{}"))["query"].strip()
                        return query or None
        except (httpx.HTTPError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            logger.warning("OpenAI question interpretation failed: %s", type(error).__name__)
            return None
        return None

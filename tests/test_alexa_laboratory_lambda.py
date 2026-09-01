from aws_lambda.alexa_laboratory.lambda_function import lambda_handler

SKILL_ID = "amzn1.ask.skill.6f4ff736-deee-43b8-bf09-6399d0f0a4a2"


def event(application_id: str) -> dict[str, object]:
    return {
        "session": {"application": {"applicationId": application_id}},
        "request": {"type": "LaunchRequest"},
    }


def test_rejects_a_different_skill_id(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("EKONEX_LAB_SKILL_ID", SKILL_ID)
    reply = lambda_handler(event("amzn1.ask.skill.other"), None)
    assert reply["response"]["outputSpeech"]["text"] == "Questa richiesta non è autorizzata."


def test_requires_an_isolated_staging_backend(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("EKONEX_LAB_SKILL_ID", SKILL_ID)
    reply = lambda_handler(event(SKILL_ID), None)
    assert "non è ancora configurato" in reply["response"]["outputSpeech"]["text"]

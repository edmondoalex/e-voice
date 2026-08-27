# Alexa it-IT open/close investigation

Status: open investigation with Amazon Developer Support.

Amazon case: `21738300901182`

## Summary

Ekonex Voice exposes Home Assistant cover entities to Alexa Smart Home. For the test blind endpoint, Italian native open/close utterances are not routed to the Smart Home Lambda, while generic power utterances are.

Observed on the same endpoint:

- `Alexa, accendi tapparella pippo` -> Lambda invoked, `Alexa.PowerController / TurnOn`, command works.
- `Alexa, open tapparella pippo` -> Lambda invoked, routed as `Alexa.PowerController / TurnOn`, command works.
- `Alexa, apri tapparella pippo` -> Alexa replies that it does not know how to adjust the device to that setting; no Lambda invocation occurs.

Alexa Voice History confirms the failing utterance is transcribed correctly and classified as `domain: HomeAutomation`, `intent: SetValueIntent`. The utterance activity key was captured and supplied privately to Amazon support; it is intentionally not committed to this public repository.

## Current test endpoint

Home Assistant entity:

`cover.buspro_cover_porta_ufficio`

Alexa friendly name used during the final reproduction:

`tapparella pippo`

Alexa endpoint ID:

`ev1_fdef316ec2a04669b5746fbfeb4c222a`

Display category:

`EXTERIOR_BLIND`

The current A/B model, merged in PR #66, follows the Home Assistant positional cover model:

- `Alexa`
- `Alexa.EndpointHealth`
- `Alexa.PowerController`
- `Alexa.RangeController`
  - instance: `cover.position`
  - supported range: 0-100
  - precision: 1
  - unit: `Alexa.Unit.Percent`
  - capability resources: text `Position` (`en-US`) plus `Alexa.Setting.Opening`
  - `Alexa.Actions.Lower` + `Alexa.Actions.Close` -> `SetRangeValue 0`
  - `Alexa.Actions.Raise` + `Alexa.Actions.Open` -> `SetRangeValue 100`
  - `Alexa.States.Closed` -> 0
  - `Alexa.States.Open` -> range 1-100
- `Alexa.PlaybackController / Stop` when supported

Executor behavior for this test model:

- `SetRangeValue 0` -> `close`
- `SetRangeValue 100` -> `open`
- intermediate values -> `set_position`
- `PowerController TurnOn/TurnOff` -> `open/close`
- `PlaybackController Stop` -> stop

## Event Gateway evidence

Forced Alexa resynchronization emits `Alexa.Discovery / AddOrUpdateReport` through the Event Gateway.

For the final reproduction, diagnostics recorded:

- HTTP status: `202`
- response body: null
- error: null
- error type: null
- attempts: `1`

The transmitted payload contains the test endpoint with the `cover.position` RangeController model and the Open/Close/Raise/Lower semantic mappings above.

This demonstrates that Ekonex successfully sends the discovery representation to Amazon and the Event Gateway accepts it.

## Lambda / CloudWatch evidence

CloudWatch Live Tail was used on:

`/aws/lambda/ekonex-voice`

The following was reproduced on the same endpoint and Echo device:

### `accendi tapparella pippo`

Lambda invocation appears immediately.

Representative directive:

- namespace: `Alexa.PowerController`
- name: `TurnOn`
- endpoint ID: `ev1_fdef316ec2a04669b5746fbfeb4c222a`

### `open tapparella pippo`

Lambda invocation appears and Alexa routes it as `Alexa.PowerController / TurnOn`.

### `apri tapparella pippo`

No Lambda invocation appears at all.

Alexa nevertheless recognizes the speech correctly in Voice History and replies in Italian that it does not know how to regulate the device to that setting.

This isolates the failure before the Ekonex Lambda/backend path.

## Skill configuration checked

Alexa Developer Console was verified after the reproduction:

- Smart Home skill: `e-Voice by Ekonex`
- payload version: v3
- locale shown in Build: `Italian (IT)`
- language selector lists `Italian (IT)`
- Lambda default endpoint: `eu-west-1`
- geographical endpoint `Europe, India`: enabled and points to the same `eu-west-1` Lambda

No alternate language was visible in the locale selector during verification.

## Other experiments already performed

Do not repeat these unless new evidence requires it.

- ModeController discrete blind model (`Blinds.Position`)
- Amazon-style RangeController model using `Blind.Lift`
- explicit Italian friendly resources (`Posizione`, `Aperto`, `Chiuso`, `Su`, `Giù`)
- `INTERIOR_BLIND` and `EXTERIOR_BLIND`
- removing `PowerController`, `ModeController`, and `PlaybackController` to test capability conflicts
- fresh friendly names and fresh endpoint experiments
- forced `AddOrUpdateReport` resynchronization
- comparison with Matter WindowCovering behavior on the same Alexa account
- comparison with Home Assistant Alexa cover implementation

The Italian `apri/chiudi` routing problem remained.

## Reference behavior

Matter WindowCovering devices on the same Alexa account and Italian locale correctly respond to native Italian open/close commands. This indicates that speech recognition and the Echo device can handle the utterances; the failure appears specific to Smart Home capability/semantic routing for this skill/endpoint representation.

## Amazon support

Case opened with Amazon Developer Support:

`21738300901182`

The support request includes:

- skill and endpoint details
- Event Gateway acceptance (`HTTP 202`)
- CloudWatch comparison of `accendi`, `open`, and `apri`
- Voice History classification (`HomeAutomation / SetValueIntent`)
- the failing utterance activity key, supplied privately to Amazon
- request for confirmation of the expected it-IT blind/window-covering capability and semantic representation

## Current decision

Do not introduce more experimental Alexa cover mappings until Amazon responds or new evidence appears. Keep PR #66 behavior stable so Amazon can investigate the same reproducible configuration described in the case.

# Alexa cover STOP diagnostic A/B

This is an intentionally non-production experiment for Issue #48. Amazon does not document
`Position.Stopped` as a standard `Blinds.Position` mode. The flag is off by default and this branch
must not be merged or left enabled after the controlled test.

## Safety and prerequisites

- Test one cover that reports HA features Open, Close and Stop (`supported_features & 8 != 0`).
- Record the current VPS commit and download the current Lambda package before changing anything.
- Keep the Alexa endpoint ID, account linking and tenant unchanged between A and B.
- Do not paste directive bodies into tickets: they contain BearerTokens. Use only the structured
  `alexa_directive_received` and `alexa_cover_diagnostic` records introduced here.

## A: current main

On the VPS, replace `<repo-dir>` with the real checkout path:

```bash
cd <repo-dir>
git fetch origin
git switch main
git pull --ff-only origin main
git rev-parse HEAD | tee /tmp/ekonex-voice-pre-stop-diagnostic-head
docker compose build api
docker compose up -d --no-deps api
```

Leave `EKONEX_ALEXA_EXPERIMENTAL_COVER_STOP_MODE` absent or set it to `false`. Run a complete Alexa
device discovery, save the cover Discovery JSON from a controlled redacted capture, and test app
Open/Close plus voice `Alexa, apri <nome>` and `Alexa, chiudi <nome>`.

## Deploy B on the VPS

```bash
cd <repo-dir>
git fetch origin agent/alexa-cover-stop-diagnostic
git switch --force-create agent/alexa-cover-stop-diagnostic \
  origin/agent/alexa-cover-stop-diagnostic
cp .env /tmp/ekonex-voice-pre-stop-diagnostic.env
printf '\nEKONEX_ALEXA_EXPERIMENTAL_COVER_STOP_MODE=true\n' >> .env
docker compose config --quiet
docker compose build api
docker compose up -d --no-deps api
docker compose ps api
```

If `.env` already contains the variable, edit that line instead of appending a duplicate. Confirm
the effective container value without printing any other environment variables:

```bash
docker compose exec api python -c \
  "from apps.cloud_api.app.config import get_settings; print(get_settings().alexa_experimental_cover_stop_mode)"
```

The output must be `True`. No database migration is required. Trigger one complete device
discovery (or remove and rediscover only the test device) so Alexa replaces its cached capability
model.

## Deploy diagnostic Lambda logging

Back up the currently deployed package first:

```bash
LAMBDA_URL="$(aws lambda get-function --function-name ekonex-voice --query Code.Location --output text)"
curl --fail --location "$LAMBDA_URL" --output /tmp/ekonex-voice-lambda-before-stop-test.zip
rm -rf /tmp/ekonex-voice-stop-lambda
mkdir -p /tmp/ekonex-voice-stop-lambda
cp aws_lambda/alexa_smart_home/lambda_function.py /tmp/ekonex-voice-stop-lambda/
(cd /tmp/ekonex-voice-stop-lambda && zip -q ../ekonex-voice-stop-lambda.zip lambda_function.py)
aws lambda update-function-code --function-name ekonex-voice \
  --zip-file fileb:///tmp/ekonex-voice-stop-lambda.zip
aws lambda wait function-updated --function-name ekonex-voice
```

If the skill targets a Lambda alias, publish this diagnostic version and temporarily move the
existing alias to it. Record the prior alias version first; do not change the Developer Console ARN.

## B test matrix

Use the Alexa app first, waiting for each motion to be observable:

1. Open.
2. Close.
3. While opening, press Stop.
4. While closing, press Stop.

Then test voice independently:

1. `Alexa, apri <nome>`.
2. `Alexa, chiudi <nome>`.
3. While moving, `Alexa, ferma <nome>`.

For every case note the wall-clock time (including timezone), starting state/direction, Alexa UI or
spoken result, physical result and whether Lambda/backend log records exist. Absence of a Lambda
record proves Alexa did not invoke the skill. A Lambda record without a backend record identifies
the proxy/network boundary. A backend `before_dispatch` record proves the chosen typed operation;
the matching `after_dispatch` record gives its EVCP result. `state_after` is intentionally null
because directive handling has no synchronous post-command HA state; use subsequent inventory/state
logs to correlate the observed result.

Collect logs for the narrow test window:

```bash
aws logs tail /aws/lambda/ekonex-voice --since 15m --format short \
  | grep 'alexa_directive_received' > /tmp/alexa-stop-lambda.log
docker compose logs --since 15m --no-color api \
  | grep 'alexa_cover_diagnostic' > /tmp/alexa-stop-backend.log
```

The allowlisted logs contain no token. Still inspect the files locally before sharing them. Expected
backend mapping is `Position.Up -> open`, `Position.Down -> close`, and—only in B on a STOP-capable
cover—`Position.Stopped -> stop`. Any other mode is rejected and never dispatched.

## Immediate rollback

Restore the Lambda first:

```bash
aws lambda update-function-code --function-name ekonex-voice \
  --zip-file fileb:///tmp/ekonex-voice-lambda-before-stop-test.zip
aws lambda wait function-updated --function-name ekonex-voice
```

If an alias was moved, point it back to the recorded version. Then restore the VPS:

```bash
cd <repo-dir>
cp /tmp/ekonex-voice-pre-stop-diagnostic.env .env
git switch main
git pull --ff-only origin main
docker compose config --quiet
docker compose build api
docker compose up -d --no-deps api
docker compose ps api
```

Confirm the flag prints `False`, then run complete Alexa discovery again to restore the canonical
two-mode Up/Down representation. Preserve the redacted A/B logs separately; do not commit them.

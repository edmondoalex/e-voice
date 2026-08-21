# Alexa cover STOP direction diagnostic

This temporary branch starts exactly at commit
`138fab2cdbdf7736f74ec0d7ac90ae0386af46d7` (immediately after PR #43). It preserves the
three-mode `Blinds.Position` discovery and the existing `Position.Stopped -> stop` mapping. Do not
merge or use it as a production upgrade.

## Deploy temporarily

Record the current VPS and integration versions first. Then deploy this branch without changing the
existing database or Alexa account linking:

```bash
cd <repo-dir>
git rev-parse HEAD | tee /tmp/ekonex-voice-before-stop-direction-diagnostic
git fetch origin agent/alexa-cover-stop-direction-diagnostic
git switch --force-create agent/alexa-cover-stop-direction-diagnostic \
  origin/agent/alexa-cover-stop-direction-diagnostic
docker compose build api
docker compose up -d --no-deps api
docker compose ps api
```

Copy the updated `custom_components/ekonex_voice` directory to the test Home Assistant instance
using the normal integration deployment method, then restart Home Assistant. No migration or new
environment variable is required. Run Alexa discovery once so the historical three-mode capability
is active.

## Deploy the diagnostic Lambda

Back up the deployed package, build a zip containing only the historical adapter with its safe
diagnostic logging, and update the existing function:

```bash
LAMBDA_URL="$(aws lambda get-function --function-name ekonex-voice --query Code.Location --output text)"
curl --fail --location "$LAMBDA_URL" --output /tmp/ekonex-voice-lambda-before-stop-direction.zip
rm -rf /tmp/ekonex-voice-stop-direction-lambda
mkdir -p /tmp/ekonex-voice-stop-direction-lambda
cp aws_lambda/alexa_smart_home/lambda_function.py /tmp/ekonex-voice-stop-direction-lambda/
(cd /tmp/ekonex-voice-stop-direction-lambda && \
  zip -q ../ekonex-voice-stop-direction-lambda.zip lambda_function.py)
aws lambda update-function-code --function-name ekonex-voice \
  --zip-file fileb:///tmp/ekonex-voice-stop-direction-lambda.zip
aws lambda wait function-updated --function-name ekonex-voice
```

If the Alexa endpoint uses a version or alias rather than `$LATEST`, record its current version,
publish this diagnostic Lambda and move only that existing alias temporarily.

## Focused test

For both directions, record the exact wall-clock time and observable position:

1. Start opening, then say `Alexa, ferma <nome cover>`.
2. Start closing, then say `Alexa, ferma <nome cover>`.

Collect only the allowlisted diagnostic records:

```bash
aws logs tail /aws/lambda/ekonex-voice --since 15m --format short \
  | grep 'alexa_directive_received'
docker compose logs --since 15m --no-color api \
  | grep 'alexa_cover_stop_diagnostic'
```

In Home Assistant, filter the integration log for `ha_cover_stop_diagnostic`. Correlate:

- Lambda: `namespace`, `name`, `instance`, `payload_mode`, `endpoint_id`;
- backend: those fields plus `entity_id`, synchronized `state_before`, `evcp_operation` and
  `dispatch_outcome`;
- Home Assistant: live `state_before`, `evcp_operation`, selected `ha_service` and outcome.

The expected invariant is always `Position.Stopped -> stop -> cover.stop_cover`, independently of
`opening` or `closing`. The logs intentionally exclude tokens, authorization headers, complete
payloads and arbitrary attributes.

## Rollback

Restore the saved Lambda zip (and prior alias version, if applicable), restore the recorded VPS
commit, rebuild the API, and restore the previous Home Assistant integration files. Run discovery
again if the restored version publishes a different cover capability.


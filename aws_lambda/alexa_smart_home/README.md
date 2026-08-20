# Deploy the `ekonex-voice` Alexa Lambda

The Lambda is a dependency-free Python adapter. It forwards the complete Alexa Smart Home
v3 directive to Ekonex Cloud. The cloud validates the Ekonex BearerToken, resolves its tenant
and builds discovery from the existing opt-in `entities` inventory. The Lambda never stores
devices, tenant mappings or credentials and never writes access tokens to logs.

`Alexa.Authorization/AcceptGrant` is the only directive allowed through the Lambda precheck
without a regular `endpoint.scope` or `payload.scope`. Amazon places the linked-account token in
`payload.grantee` and its one-use LWA authorization code in `payload.grant`; the complete directive
is forwarded unchanged so the cloud can exchange and store the proactive-event credentials.
Discovery, control and every other directive still require the regular BearerToken scope.

## Required AWS settings

- Function name: `ekonex-voice`
- Runtime: Python 3.13
- Handler: `lambda_function.lambda_handler`
- Architecture: keep the architecture already used by the function (`x86_64` or `arm64`)
- Memory: 128 MB or more
- Timeout: 15 seconds
- Environment:
  - `EKONEX_VOICE_BACKEND_URL=https://voice.e-control.tech`
  - `EKONEX_VOICE_BACKEND_TIMEOUT_SECONDS=8`

The execution role only needs normal CloudWatch Logs permissions. The function needs outbound
HTTPS access to `voice.e-control.tech`; when attached to a VPC, configure a NAT path. Do not add
OAuth client secrets or Ekonex access tokens to Lambda environment variables.

## Update the existing function from PowerShell

Run these commands from the repository root:

```powershell
Compress-Archive `
  -LiteralPath .\aws_lambda\alexa_smart_home\lambda_function.py `
  -DestinationPath .\ekonex-voice-lambda.zip `
  -Force

aws lambda update-function-code `
  --function-name ekonex-voice `
  --zip-file fileb://ekonex-voice-lambda.zip

aws lambda update-function-configuration `
  --function-name ekonex-voice `
  --runtime python3.13 `
  --handler lambda_function.lambda_handler `
  --timeout 15 `
  --memory-size 128 `
  --environment "Variables={EKONEX_VOICE_BACKEND_URL=https://voice.e-control.tech,EKONEX_VOICE_BACKEND_TIMEOUT_SECONDS=8}"

aws lambda wait function-updated --function-name ekonex-voice
aws lambda get-function-configuration `
  --function-name ekonex-voice `
  --query "{State:State,LastUpdateStatus:LastUpdateStatus,Runtime:Runtime,Handler:Handler,Timeout:Timeout,Environment:Environment.Variables}"
```

If the Alexa skill endpoint targets a published Lambda version or alias, publish and move that
alias after verification:

```powershell
$version = aws lambda publish-version `
  --function-name ekonex-voice `
  --query Version `
  --output text

aws lambda update-alias `
  --function-name ekonex-voice `
  --name live `
  --function-version $version
```

Use the real alias name instead of `live`. If the Alexa Developer Console targets `$LATEST`, the
alias commands are not needed. Keep the configured Alexa endpoint ARN unchanged unless you
intentionally introduce or rename an alias.

## Production verification

1. In the Alexa app, disable and re-enable the development skill if account linking must be
   refreshed.
2. Complete e-Control login and select the tenant.
3. Ask Alexa to discover devices or use **Discover Devices** in the Alexa app.
4. Verify the Lambda reports success and the Alexa app receives only the selected tenant's
   currently exposed, non-tombstoned entities.
5. Inspect CloudWatch logs for namespace/name and HTTP status diagnostics only. Access tokens
   must never appear.

Invalid or revoked tokens return `INVALID_AUTHORIZATION_CREDENTIAL`; expired tokens return
`EXPIRED_AUTHORIZATION_CREDENTIAL`. Connectivity and unexpected backend failures return
`INTERNAL_ERROR`, and HTTP 429 maps to `RATE_LIMIT_EXCEEDED`.

# Alexa Smart Home certification

This checklist prepares the production Ekonex Voice skill for Amazon certification. It must be
used with a clean production baseline: no entity-specific endpoint IDs, capabilities, mappings, or
diagnostic Discovery profiles.

## Current Amazon finding

Amazon case `21738300901` traced the affected Italian blind utterance to request interpretation,
before a directive is emitted to the skill. Amazon withdrew its earlier statements about whether
the Discovery semantics reached or were read by its device registry. The Developer Advocate asked
us to remove the invalid `Alexa.PlaybackController` capability from blind endpoints, rediscover,
and test the exact utterance `Alexa, apri le tapparelle` without a device name. Record the UTC
timestamp and Alexa's response for the case. Certification must not be described as a code fix for
the still-open Amazon interpretation issue.

## Before submission

- Deploy the reviewed certification baseline to the production Lambda and cloud API.
- Confirm that Discovery contains no entity-specific diagnostic endpoint IDs or capabilities.
- Keep one dedicated Italian test installation online and discoverable throughout certification.
- Create a dedicated Ekonex test user without two-factor authentication.
- Verify account linking, Discovery, ReportState, and supported commands with that user.
- Run Alexa Developer Console validation and the applicable Smart Home tests.
- Verify the privacy policy, terms, support page, and customer guide on mobile and desktop.
- Select manual publication after certification so approval does not deploy automatically.

## Certification test instructions

Provide Amazon with:

- the dedicated test username and password (only in the Developer Console, never in Git);
- the installation and device names available to that account;
- the supported device types and exact Italian example utterances;
- explicit setup, account-linking, Discovery, and troubleshooting steps;
- the reference to Amazon case `21738300901` for the unresolved `it-IT` blind Open/Close routing;
- confirmation that the device and Connector will remain online continuously.

## Release gate

Do not submit until CI, type checking, formatting, Hassfest, account linking, and production health
checks are green. Do not merge diagnostic Alexa branches into the certification baseline.

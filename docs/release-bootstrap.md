# HACS release bootstrap

The HACS release workflow runs when the connector manifest version changes and when the release workflow itself changes. This lets the current manifest version be published automatically if its GitHub prerelease is missing, while future version bumps remain automatic.

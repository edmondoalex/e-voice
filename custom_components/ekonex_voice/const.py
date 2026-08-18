"""Constants for Ekonex Voice."""

from datetime import timedelta

DOMAIN = "ekonex_voice"
NAME = "Ekonex Voice"

CONF_CLOUD_URL = "cloud_url"
CONF_CONNECTOR_CREDENTIAL = "connector_credential"
CONF_INSTALLATION_ID = "installation_id"
CONF_INSTALLATION_NAME = "installation_name"
CONF_TENANT_NAME = "tenant_name"

# M3 keeps the production endpoint out of the UI. The path contract is isolated
# in client.py so deployment can change the origin without touching flow logic.
DEFAULT_CLOUD_URL = "https://api.ekonex.it"

PAIRING_POLL_INTERVAL = timedelta(seconds=2)
PAIRING_REQUEST_TIMEOUT = 10.0
PAIRING_TOTAL_TIMEOUT = 600.0
CONNECT_TIMEOUT = 10.0
HEARTBEAT_TIMEOUT = 75.0
BACKOFF_INITIAL = 1.0
BACKOFF_MAXIMUM = 60.0
BACKOFF_SCHEDULE = (1.0, 2.0, 5.0, 10.0, 30.0, 60.0)

REDACTED = "**REDACTED**"

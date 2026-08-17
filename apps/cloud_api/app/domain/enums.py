"""Constrained values used by the M1 domain model."""

from enum import StrEnum


class RecordStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class InstallationStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    REVOKED = "revoked"


class TenantRole(StrEnum):
    OWNER = "owner"
    DEALER_ADMIN = "dealer_admin"
    INSTALLER = "installer"
    CUSTOMER_ADMIN = "customer_admin"
    CUSTOMER_USER = "customer_user"
    SUPPORT_READONLY = "support_readonly"


class PairingStatus(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    EXPIRED = "expired"
    LOCKED = "locked"

from sqlalchemy import UniqueConstraint

from apps.cloud_api.app.database import Base


def test_domain_tables_are_registered() -> None:
    assert set(Base.metadata.tables) == {
        "alexa_publications",
        "audit_events",
        "connector_credentials",
        "dealers",
        "entities",
        "installations",
        "pairing_claim_attempts",
        "pairing_sessions",
        "tenant_memberships",
        "tenants",
        "users",
    }


def test_entity_identity_is_unique_per_installation() -> None:
    entity_table = Base.metadata.tables["entities"]
    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in entity_table.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert ("installation_id", "ha_entity_id") in unique_columns

"""Tests for schema context integrity and drift detection against SQLAlchemy models."""
from sqlalchemy import inspect
from src.db.models.business import Customer, Order, OrderItem, Product
from src.llm.schema_context import (
    BUSINESS_RULES,
    SCHEMA_DEFINITION,
    format_schema_context,
)


def test_schema_definition_matches_orm_models():
    """Verify that every table and column in SCHEMA_DEFINITION corresponds to an actual ORM model."""
    orm_model_map = {
        "analytics.customers": Customer,
        "analytics.products": Product,
        "analytics.orders": Order,
        "analytics.order_items": OrderItem,
    }

    # Verify all approved tables exist
    assert set(SCHEMA_DEFINITION.keys()) == set(orm_model_map.keys())

    for table_name, model_cls in orm_model_map.items():
        mapper = inspect(model_cls)
        orm_columns = {col.key for col in mapper.columns}
        defined_columns = set(SCHEMA_DEFINITION[table_name]["columns"].keys())

        # Ensure no drift in column names
        missing_in_definition = orm_columns - defined_columns
        missing_in_orm = defined_columns - orm_columns

        assert not missing_in_definition, f"Columns in {model_cls.__name__} missing from SCHEMA_DEFINITION: {missing_in_definition}"
        assert not missing_in_orm, f"Columns in SCHEMA_DEFINITION missing from {model_cls.__name__}: {missing_in_orm}"


def test_format_schema_context_structure():
    """Verify that format_schema_context produces a complete, markdown-formatted prompt."""
    context_str = format_schema_context()

    assert "# APPROVED DATABASE SCHEMA" in context_str
    assert "analytics.customers" in context_str
    assert "analytics.products" in context_str
    assert "analytics.orders" in context_str
    assert "analytics.order_items" in context_str
    assert "Revenue Calculation (Product Level)" in context_str
    assert "o.status IN ('completed', 'shipped')" in context_str
    assert len(BUSINESS_RULES) >= 5

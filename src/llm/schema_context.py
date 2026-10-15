"""Structured schema context defining the approved business analytics schema and business rules."""
from typing import Dict, List, Any


# Canonical structured definition of the analytics schema
# Used for LLM prompt grounding and verified against SQLAlchemy models for drift detection.
SCHEMA_DEFINITION: Dict[str, Dict[str, Any]] = {
    "analytics.customers": {
        "description": "Registered customers and demographic locations.",
        "columns": {
            "id": {"type": "INTEGER", "description": "Primary key, unique customer identifier"},
            "first_name": {"type": "VARCHAR(50)", "description": "Customer given name"},
            "last_name": {"type": "VARCHAR(50)", "description": "Customer family name"},
            "email": {"type": "VARCHAR(100)", "description": "Unique email address"},
            "country": {"type": "VARCHAR(50)", "description": "Customer country of residence (e.g., 'United States', 'Germany', 'India')"},
            "city": {"type": "VARCHAR(50)", "description": "Customer city of residence"},
            "created_at": {"type": "TIMESTAMPTZ", "description": "Timestamp when the customer profile was created"},
        },
        "primary_key": ["id"],
        "foreign_keys": [],
    },
    "analytics.products": {
        "description": "Product catalog containing item details, category, pricing, and inventory stock.",
        "columns": {
            "id": {"type": "INTEGER", "description": "Primary key, unique product identifier"},
            "name": {"type": "VARCHAR(100)", "description": "Full display title of the product"},
            "category": {"type": "VARCHAR(50)", "description": "Product category (e.g., 'Electronics', 'Furniture', 'Accessories', 'Audio', 'Wearables')"},
            "price": {"type": "NUMERIC(10, 2)", "description": "Retail selling price in USD"},
            "cost": {"type": "NUMERIC(10, 2)", "description": "Unit manufacturing/acquisition cost in USD"},
            "stock_quantity": {"type": "INTEGER", "description": "Current inventory stock on hand"},
            "is_active": {"type": "BOOLEAN", "description": "Flag indicating if the product is active and available for sale"},
            "created_at": {"type": "TIMESTAMPTZ", "description": "Timestamp when the product was added to the catalog"},
        },
        "primary_key": ["id"],
        "foreign_keys": [],
    },
    "analytics.orders": {
        "description": "E-commerce orders placed by customers.",
        "columns": {
            "id": {"type": "INTEGER", "description": "Primary key, unique order identifier"},
            "customer_id": {"type": "INTEGER", "description": "Foreign key referencing analytics.customers(id)"},
            "status": {"type": "VARCHAR(30)", "description": "Order status: 'completed', 'shipped', 'pending', or 'cancelled'"},
            "total_amount": {"type": "NUMERIC(12, 2)", "description": "Total order monetary value in USD"},
            "order_date": {"type": "TIMESTAMPTZ", "description": "Timestamp when the order was placed"},
        },
        "primary_key": ["id"],
        "foreign_keys": [
            {"column": "customer_id", "references": "analytics.customers.id"}
        ],
    },
    "analytics.order_items": {
        "description": "Individual line items comprising each order.",
        "columns": {
            "id": {"type": "INTEGER", "description": "Primary key, line item identifier"},
            "order_id": {"type": "INTEGER", "description": "Foreign key referencing analytics.orders(id)"},
            "product_id": {"type": "INTEGER", "description": "Foreign key referencing analytics.products(id)"},
            "quantity": {"type": "INTEGER", "description": "Number of units purchased (must be > 0)"},
            "unit_price": {"type": "NUMERIC(10, 2)", "description": "Price per unit at time of purchase in USD"},
        },
        "primary_key": ["id"],
        "foreign_keys": [
            {"column": "order_id", "references": "analytics.orders.id"},
            {"column": "product_id", "references": "analytics.products.id"},
        ],
    },
}

BUSINESS_RULES: List[str] = [
    "Revenue Calculation (Product Level): Revenue for a product is calculated as SUM(oi.quantity * oi.unit_price) from analytics.order_items.",
    "Realized Revenue Filter: When computing sales or revenue metrics, filter for realized orders: o.status IN ('completed', 'shipped'), unless the user explicitly asks for all orders or specific statuses.",
    "Profit Calculation: Profit = SUM(oi.quantity * (oi.unit_price - p.cost)) joining order_items oi to products p.",
    "Customer Lifetime Value (LTV): Total spend of a customer = SUM(o.total_amount) or SUM(oi.quantity * oi.unit_price) for completed/shipped orders.",
    "Table Relationships: Always use explicit INNER JOIN or LEFT JOIN with standard foreign key relationships.",
    "Schema Qualification: Always explicitly qualify table names with the schema 'analytics' (e.g. 'analytics.customers').",
    "PostgreSQL Dialect: Use standard PostgreSQL functions (e.g., DATE_TRUNC('month', o.order_date), EXTRACT(YEAR FROM o.order_date), COALESCE, ROUND).",
    "Customer Privacy & PII: Direct harvesting or dumping of customer personal identifiers (such as raw customer email address lists) is prohibited by data governance policy. Customer analytics should focus on customer names and behavioral metrics (e.g. order counts, total spend).",
]


def format_schema_context() -> str:
    """Format the schema definition and business rules into a structured markdown prompt segment."""
    lines: List[str] = ["# APPROVED DATABASE SCHEMA (analytics schema)\n"]

    for table_name, table_info in SCHEMA_DEFINITION.items():
        lines.append(f"## Table: `{table_name}`")
        lines.append(f"Description: {table_info['description']}")
        lines.append("Columns:")
        for col_name, col_info in table_info["columns"].items():
            lines.append(f"  - `{col_name}` ({col_info['type']}): {col_info['description']}")

        if table_info["foreign_keys"]:
            lines.append("Foreign Keys:")
            for fk in table_info["foreign_keys"]:
                lines.append(f"  - `{fk['column']}` -> `{fk['references']}`")
        lines.append("")

    lines.append("# APPROVED BUSINESS RULES & METRICS DEFINITIONS")
    for rule in BUSINESS_RULES:
        lines.append(f"- {rule}")

    return "\n".join(lines)

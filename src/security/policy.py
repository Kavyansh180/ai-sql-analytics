"""Column-Level Security Policy for AI Analytics Queries.

Defines the centralized catalog of approved analytical columns, strictly prohibited
sensitive/PII contact columns, and wildcard-restricted tables.
"""
from typing import Dict, Optional, Set

# Tables and their explicitly approved analytical columns
# Any column not present in this allowlist is treated as unapproved.
APPROVED_TABLE_COLUMNS: Dict[str, Set[str]] = {
    "customers": {
        "id",
        "first_name",
        "last_name",
        "country",
        "city",
        "created_at",
    },
    "products": {
        "id",
        "name",
        "category",
        "price",
        "cost",
        "stock_quantity",
        "is_active",
        "created_at",
    },
    "orders": {
        "id",
        "customer_id",
        "status",
        "total_amount",
        "order_date",
    },
    "order_items": {
        "id",
        "order_id",
        "product_id",
        "quantity",
        "unit_price",
    },
}

# Tables and their specifically prohibited columns
PROHIBITED_TABLE_COLUMNS: Dict[str, Set[str]] = {
    "customers": {
        "email",
    },
}

# Globally prohibited sensitive contact / PII / credential column identifiers
# These are strictly blocked across any table, CTE, or subquery
PROHIBITED_CONTACT_COLUMNS: Set[str] = {
    "email",
    "email_address",
    "phone",
    "phone_number",
    "mobile",
    "mobile_number",
    "ssn",
    "social_security_number",
    "password",
    "password_hash",
    "secret",
    "credit_card",
    "card_number",
}

# Tables containing prohibited columns where wildcard selection (SELECT * / SELECT c.*)
# is strictly forbidden to prevent unintended data exposure
WILDCARD_RESTRICTED_TABLES: Set[str] = {
    "customers",
    "analytics.customers",
}


def normalize_identifier(identifier: Optional[str]) -> str:
    """Normalize SQL identifiers for case-insensitive comparison."""
    if not identifier:
        return ""
    # Strip quotes, schema prefix if present, and lower-case
    clean = identifier.strip().strip('"').strip("'").lower()
    if "." in clean:
        clean = clean.split(".")[-1]
    return clean


def is_prohibited_column(column_name: str, table_name: Optional[str] = None) -> bool:
    """
    Check if a column is prohibited by the Column-Level Security Policy.
    
    Args:
        column_name: Column identifier from SQL AST.
        table_name: Optional table identifier or alias.
        
    Returns:
        True if the column is explicitly prohibited; False otherwise.
    """
    clean_col = normalize_identifier(column_name)
    if clean_col in PROHIBITED_CONTACT_COLUMNS:
        return True

    if table_name:
        clean_tbl = normalize_identifier(table_name)
        if clean_tbl in PROHIBITED_TABLE_COLUMNS:
            if clean_col in PROHIBITED_TABLE_COLUMNS[clean_tbl]:
                return True

    return False


def is_approved_column(table_name: str, column_name: str) -> bool:
    """
    Check if a column is in the approved analytical allowlist for a specific table.
    
    Args:
        table_name: Table name (e.g., 'customers' or 'analytics.customers').
        column_name: Column name (e.g., 'first_name').
        
    Returns:
        True if approved; False if unapproved or prohibited.
    """
    clean_tbl = normalize_identifier(table_name)
    clean_col = normalize_identifier(column_name)

    if is_prohibited_column(clean_col, clean_tbl):
        return False

    approved_set = APPROVED_TABLE_COLUMNS.get(clean_tbl)
    if approved_set is None:
        return False

    return clean_col in approved_set


def is_wildcard_restricted(table_name: str) -> bool:
    """Check if a table contains sensitive columns restricting bare SELECT *."""
    clean_tbl = normalize_identifier(table_name)
    return clean_tbl in WILDCARD_RESTRICTED_TABLES or f"analytics.{clean_tbl}" in WILDCARD_RESTRICTED_TABLES

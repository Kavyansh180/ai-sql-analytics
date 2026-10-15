"""SQL governance and query validation layer using SQLGlot AST analysis."""
from typing import List, Optional, Set
import sqlglot
from sqlglot import exp
from pydantic import BaseModel, Field

# Canonical business tables permitted for AI analytics queries
ALLOWED_TABLES: Set[str] = {
    "customers",
    "products",
    "orders",
    "order_items",
    "analytics.customers",
    "analytics.products",
    "analytics.orders",
    "analytics.order_items",
}

DISALLOWED_SCHEMAS: Set[str] = {
    "app_metadata",
    "information_schema",
    "pg_catalog",
    "public",
}

# Dangerous or administrative functions strictly blocked from query AST
DANGEROUS_FUNCTIONS: Set[str] = {
    "set_config",
    "pg_sleep",
    "pg_sleep_for",
    "pg_sleep_until",
    "pg_read_file",
    "pg_read_binary_file",
    "pg_write_file",
    "pg_write_binary_file",
    "pg_terminate_backend",
    "pg_cancel_backend",
    "pg_reload_conf",
    "pg_rotate_logfile",
    "query_to_xml",
    "table_to_xml",
    "cursor_to_xml",
    "dblink",
    "dblink_exec",
    "dblink_connect",
    "lo_export",
    "lo_import",
    "lo_unlink",
    "current_setting",
}


class SQLValidationResult(BaseModel):
    """Structured result of SQL security validation."""
    is_valid: bool
    error: Optional[str] = None
    sanitized_sql: Optional[str] = None
    tables_referenced: List[str] = Field(default_factory=list)


class SQLSecurityValidator:
    """Validator enforcing read-only, single-statement, schema-isolated query policies."""

    def __init__(
        self,
        allowed_tables: Optional[Set[str]] = None,
        disallowed_schemas: Optional[Set[str]] = None,
        dangerous_functions: Optional[Set[str]] = None,
        max_limit: int = 1000,
        dialect: str = "postgres",
    ):
        self.allowed_tables = allowed_tables or ALLOWED_TABLES
        self.disallowed_schemas = disallowed_schemas or DISALLOWED_SCHEMAS
        self.dangerous_functions = dangerous_functions or DANGEROUS_FUNCTIONS
        self.max_limit = max_limit
        self.dialect = dialect

    def validate_and_sanitize(self, sql_query: str) -> SQLValidationResult:
        """
        Parse and validate an arbitrary SQL query against strict security policies.
        
        Enforces:
        1. Parsing correctness via SQLGlot.
        2. Exactly one statement (no statement chaining or multiple SQL commands).
        3. Only read-only query shapes permitted (SELECT, UNION, INTERSECT, EXCEPT, CTEs).
        4. Rejection of DDL, DML, DCL, and table creation (SELECT INTO).
        5. Rejection of dangerous administrative/session functions (pg_sleep, set_config, etc.).
        6. Table allowlisting (only analytics tables or in-query defined CTEs allowed).
        7. Rejection of restricted schemas (app_metadata, pg_catalog, information_schema).
        8. Enforced LIMIT clause to prevent server memory exhaustion across all supported query shapes.
        """
        if not sql_query or not sql_query.strip():
            return SQLValidationResult(is_valid=False, error="SQL query cannot be empty.")

        try:
            parsed_statements = sqlglot.parse(sql_query, read=self.dialect)
        except Exception as parse_err:
            return SQLValidationResult(
                is_valid=False,
                error=f"SQL syntax error: {str(parse_err)}",
            )

        # 1. Statement Chaining Check
        valid_statements = [s for s in parsed_statements if s is not None]
        if len(valid_statements) == 0:
            return SQLValidationResult(is_valid=False, error="No valid SQL statement found.")
        if len(valid_statements) > 1:
            return SQLValidationResult(
                is_valid=False,
                error="Multiple SQL statements are strictly forbidden. Only a single query may be executed.",
            )

        statement = valid_statements[0]

        # 2. Permitted Root Expression Types
        allowed_root_types = (exp.Select, exp.Union, exp.Intersect, exp.Except)
        if not isinstance(statement, allowed_root_types):
            return SQLValidationResult(
                is_valid=False,
                error=f"Forbidden statement type: '{type(statement).__name__}'. Only read-only query expressions are permitted.",
            )

        # Check for disallowed operations inside the AST (subqueries, CTE bodies, etc.)
        disallowed_expr_types = (
            exp.Insert,
            exp.Update,
            exp.Delete,
            exp.Drop,
            exp.Create,
            exp.Alter,
            exp.TruncateTable,
            exp.Into,
            exp.Command,
        )
        for node in statement.walk():
            if isinstance(node, disallowed_expr_types):
                return SQLValidationResult(
                    is_valid=False,
                    error=f"Forbidden operation '{type(node).__name__}' detected within query.",
                )

            # 3. Check for Dangerous or Administrative Functions
            if isinstance(node, (exp.Func, exp.Anonymous)):
                func_name = ""
                if hasattr(node, "name") and node.name:
                    func_name = str(node.name).lower()
                elif hasattr(node, "key") and node.key:
                    func_name = str(node.key).lower()

                if func_name in self.dangerous_functions:
                    return SQLValidationResult(
                        is_valid=False,
                        error=f"Execution of dangerous or administrative function '{func_name}' is forbidden.",
                    )

        # 4. Extract CTE Aliases Defined in Query
        cte_names: Set[str] = set()
        for cte in statement.find_all(exp.CTE):
            alias = cte.alias_or_name
            if alias:
                cte_names.add(alias.lower())

        # 5. Table & Schema Policy Validation
        tables_found: List[str] = []
        for table_expr in statement.find_all(exp.Table):
            table_name = table_expr.name.lower()
            schema_name = table_expr.db.lower() if table_expr.db else None

            # Skip in-query CTE references (they have no schema and match defined CTE alias)
            if not schema_name and table_name in cte_names:
                continue

            # Block disallowed schemas
            if schema_name in self.disallowed_schemas:
                return SQLValidationResult(
                    is_valid=False,
                    error=f"Access to schema '{schema_name}' is forbidden.",
                )

            # Build canonical representation
            full_ref = f"{schema_name}.{table_name}" if schema_name else table_name
            tables_found.append(full_ref)

            # Check table allowlist
            is_allowed = (
                full_ref in self.allowed_tables
                or table_name in self.allowed_tables
                or (schema_name == "analytics" and table_name in self.allowed_tables)
            )
            if not is_allowed:
                return SQLValidationResult(
                    is_valid=False,
                    error=f"Table '{full_ref}' is not in the approved analytics allowlist.",
                )

        # 6. Column-Level Security Policy & Wildcard Inspection (Phase 3.3)
        cls_error = self._validate_column_security(statement, cte_names)
        if cls_error:
            return SQLValidationResult(
                is_valid=False,
                error=cls_error,
                tables_referenced=list(set(tables_found)),
            )

        # 7. Enforce or Clamp LIMIT clause across all supported query shapes
        if isinstance(statement, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
            limit_clause = statement.args.get("limit")
            if limit_clause is None:
                statement = statement.limit(self.max_limit)
            else:
                try:
                    current_limit = int(limit_clause.expression.this)
                    if current_limit > self.max_limit or current_limit < 1:
                        limit_clause.set("expression", exp.Literal.number(self.max_limit))
                except (ValueError, AttributeError):
                    limit_clause.set("expression", exp.Literal.number(self.max_limit))

        sanitized_sql = statement.sql(dialect=self.dialect)

        return SQLValidationResult(
            is_valid=True,
            sanitized_sql=sanitized_sql,
            tables_referenced=list(set(tables_found)),
        )

    def _validate_column_security(
        self,
        statement: exp.Expression,
        cte_names: Set[str],
    ) -> Optional[str]:
        """
        Enforce deterministic column-level access controls and wildcard restrictions.
        
        Guarantees:
        - Prohibited columns (e.g. customer email, contact details) cannot be selected
          directly, aliased, wrapped in expressions, filtered, sorted, or grouped.
        - Wildcard selection (SELECT * / SELECT c.*) on tables with restricted columns is blocked.
        - All referenced base table columns must belong to the approved column allowlist.
        - CTEs and subqueries cannot access prohibited columns to re-export them.
        """
        from src.security.policy import (
            APPROVED_TABLE_COLUMNS,
            is_approved_column,
            is_prohibited_column,
            is_wildcard_restricted,
            normalize_identifier,
        )

        # Map all base tables and their local aliases
        table_alias_map: dict[str, str] = {}
        for table_expr in statement.find_all(exp.Table):
            t_name = normalize_identifier(table_expr.name)
            if t_name in cte_names:
                continue
            t_alias = normalize_identifier(table_expr.alias) if table_expr.alias else t_name
            table_alias_map[t_alias] = t_name
            table_alias_map[t_name] = t_name

        # Collect defined expression aliases
        defined_aliases: Set[str] = set()
        for alias_node in statement.find_all(exp.Alias):
            if alias_node.alias:
                defined_aliases.add(normalize_identifier(alias_node.alias))

        # Collect CTE output column names
        cte_outputs: dict[str, Set[str]] = {}
        for cte in statement.find_all(exp.CTE):
            name = normalize_identifier(cte.alias_or_name)
            outputs: Set[str] = set()
            if hasattr(cte, "this") and hasattr(cte.this, "expressions"):
                for expr in cte.this.expressions:
                    if hasattr(expr, "alias") and expr.alias:
                        outputs.add(normalize_identifier(expr.alias))
                    elif isinstance(expr, exp.Column):
                        outputs.add(normalize_identifier(expr.name))
            cte_outputs[name] = outputs

        # A. Wildcard Validation across all Select scopes
        for select in statement.find_all(exp.Select):
            local_tables: dict[str, str] = {}
            from_clause = select.args.get("from_")
            if from_clause and isinstance(from_clause.this, exp.Table):
                fn = normalize_identifier(from_clause.this.name)
                fa = normalize_identifier(from_clause.this.alias) if from_clause.this.alias else fn
                local_tables[fa] = fn
                local_tables[fn] = fn
            for join in select.args.get("joins", []):
                if isinstance(join.this, exp.Table):
                    jn = normalize_identifier(join.this.name)
                    ja = normalize_identifier(join.this.alias) if join.this.alias else jn
                    local_tables[ja] = jn
                    local_tables[jn] = jn

            for expr in select.expressions:
                for star in expr.find_all(exp.Star):
                    # COUNT(*) or similar aggregation is safe (does not project or read column data)
                    if star.find_ancestor(exp.Count, exp.AggFunc):
                        continue

                    col_parent = star.find_ancestor(exp.Column)
                    if col_parent and col_parent.table:
                        ref_alias = normalize_identifier(col_parent.table)
                        resolved_tbl = local_tables.get(ref_alias, table_alias_map.get(ref_alias, ref_alias))
                        if is_wildcard_restricted(resolved_tbl):
                            return (
                                f"Column-level security violation: Wildcard selection ('{col_parent.table}.*') "
                                f"on table '{resolved_tbl}' containing restricted columns is forbidden. Explicitly select approved columns."
                            )
                    else:
                        for alias, tbl in local_tables.items():
                            if is_wildcard_restricted(tbl):
                                return (
                                    f"Column-level security violation: Wildcard selection ('*') on table '{tbl}' "
                                    "containing restricted columns is forbidden. Explicitly select approved columns."
                                )

        # B. Column Identifier Validation
        base_tables = {tbl for tbl in table_alias_map.values() if tbl in APPROVED_TABLE_COLUMNS}

        for col in statement.find_all(exp.Column):
            col_name = normalize_identifier(col.name)
            if col_name == "*":
                continue

            col_table = normalize_identifier(col.table) if col.table else None
            resolved_table = table_alias_map.get(col_table, col_table) if col_table else None

            # 1. Reject prohibited columns directly
            if is_prohibited_column(col_name, resolved_table):
                target_str = f" on table '{resolved_table}'" if resolved_table else ""
                return f"Column-level security violation: Access to prohibited column '{col.name}'{target_str} is forbidden."

            # 2. Reject unapproved columns on qualified base tables
            if resolved_table in APPROVED_TABLE_COLUMNS:
                if not is_approved_column(resolved_table, col_name):
                    return (
                        f"Column-level security violation: Column '{col.name}' is not in the approved column "
                        f"allowlist for table '{resolved_table}'."
                    )

            # 3. Unqualified column resolution & ambiguity guard (fail-closed)
            if not col_table and col_name not in defined_aliases:
                in_cte = any(col_name in outputs for outputs in cte_outputs.values())
                if not in_cte:
                    # Find enclosing Select scope tables
                    parent_select = col.find_ancestor(exp.Select)
                    if parent_select:
                        scope_tables: Set[str] = set()
                        from_clause = parent_select.args.get("from_")
                        if from_clause and isinstance(from_clause.this, exp.Table):
                            fn = normalize_identifier(from_clause.this.name)
                            if fn not in cte_names:
                                scope_tables.add(table_alias_map.get(fn, fn))
                        for join in parent_select.args.get("joins", []):
                            if isinstance(join.this, exp.Table):
                                jn = normalize_identifier(join.this.name)
                                if jn not in cte_names:
                                    scope_tables.add(table_alias_map.get(jn, jn))
                        active_tables = {t for t in scope_tables if t in APPROVED_TABLE_COLUMNS} or base_tables
                    else:
                        active_tables = base_tables

                    from src.security.policy import PROHIBITED_TABLE_COLUMNS
                    matching_tables = [
                        tbl for tbl in active_tables
                        if col_name in APPROVED_TABLE_COLUMNS.get(tbl, set())
                        or col_name in PROHIBITED_TABLE_COLUMNS.get(tbl, set())
                    ]

                    # Check if unified by USING clause in the local select
                    using_cols: Set[str] = set()
                    if parent_select:
                        for j in parent_select.args.get("joins", []):
                            for u in j.args.get("using") or []:
                                using_cols.add(normalize_identifier(u.name if hasattr(u, "name") else str(u)))

                    if col_name not in using_cols and len(matching_tables) > 1:
                        return (
                            f"Column-level security violation: Unqualified column '{col.name}' is ambiguous across "
                            f"multiple referenced tables ({', '.join(sorted(matching_tables))}). Must be table-qualified."
                        )

                    if len(matching_tables) == 1:
                        single_tbl = matching_tables[0]
                        if is_prohibited_column(col_name, single_tbl):
                            return f"Column-level security violation: Access to prohibited column '{col.name}' on table '{single_tbl}' is forbidden."
                    elif len(matching_tables) == 0 and active_tables:
                        return (
                            f"Column-level security violation: Unqualified column '{col.name}' does not match "
                            f"any approved column on referenced tables ({', '.join(sorted(active_tables))})."
                        )

        # C. Join USING Clause Validation
        for join in statement.find_all(exp.Join):
            for using_ident in join.args.get("using") or []:
                name = using_ident.name if hasattr(using_ident, "name") else str(using_ident)
                clean_id = normalize_identifier(name)
                if is_prohibited_column(clean_id):
                    return f"Column-level security violation: Join USING clause references prohibited column '{name}'."

        return None

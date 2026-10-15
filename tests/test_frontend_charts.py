"""Unit tests for automatic chart selection and Plotly visualization generation."""
import pytest
from src.frontend.charts import generate_chart


def test_line_chart_selected_for_temporal_metric():
    """Date/time column plus numeric metric with multiple points selects a line chart."""
    rows = [
        {"order_month": "2025-01-01", "total_revenue": 1500.0},
        {"order_month": "2025-02-01", "total_revenue": 2300.0},
        {"order_month": "2025-03-01", "total_revenue": 3100.0},
    ]
    result = generate_chart(rows)
    assert result.chart_type == "line"
    assert result.figure is not None
    assert "Revenue" in result.title


def test_bar_chart_selected_for_category_and_metric():
    """Categorical column plus numeric metric selects a bar chart."""
    rows = [
        {"product_name": "Ultra HD Monitor", "total_revenue": 1197.0},
        {"product_name": "Noise Cancelling Headphones", "total_revenue": 749.97},
        {"product_name": "Mechanical Keyboard", "total_revenue": 450.0},
    ]
    result = generate_chart(rows)
    assert result.chart_type == "bar"
    assert result.figure is not None


def test_pie_chart_selected_for_small_category_share():
    """Small category distribution (2-6 categories, non-negative, positive sum) selects pie chart."""
    rows = [
        {"category": "Electronics", "category_revenue": 5000.0},
        {"category": "Furniture", "category_revenue": 3200.0},
        {"category": "Accessories", "category_revenue": 1800.0},
    ]
    result = generate_chart(rows)
    assert result.chart_type == "pie"
    assert result.figure is not None


def test_empty_result_set_does_not_create_chart():
    """Empty result sets return no chart and a helpful explanation."""
    result = generate_chart([])
    assert result.chart_type is None
    assert result.figure is None
    assert "no data rows" in result.reason.lower()


def test_data_without_numeric_metrics_does_not_create_chart():
    """Data containing only text/categorical fields cannot form a chart."""
    rows = [
        {"first_name": "Alice", "city": "New York"},
        {"first_name": "Bob", "city": "London"},
    ]
    result = generate_chart(rows)
    assert result.chart_type is None
    assert result.figure is None
    assert "no numeric metric found" in result.reason.lower()


def test_null_values_handled_safely():
    """Null and NaN values do not cause chart generation to crash."""
    rows = [
        {"product_name": "Item A", "revenue": 100.0},
        {"product_name": "Item B", "revenue": None},
        {"product_name": None, "revenue": 50.0},
    ]
    result = generate_chart(rows)
    assert result.chart_type in ("bar", "pie")
    assert result.figure is not None


def test_numeric_strings_handled_appropriately():
    """Numeric values returned as strings (e.g. from JSON decimals) are parsed safely."""
    rows = [
        {"product_name": "Widget A", "revenue": "150.50"},
        {"product_name": "Widget B", "revenue": "275.25"},
    ]
    result = generate_chart(rows)
    assert result.chart_type == "bar"
    assert result.figure is not None


def test_negative_values_do_not_produce_pie_chart():
    """Negative values disqualify pie charts and fall back to bar charts."""
    rows = [
        {"category": "Electronics", "profit": 1500.0},
        {"category": "Clearance", "profit": -400.0},
        {"category": "Accessories", "profit": 800.0},
    ]
    result = generate_chart(rows)
    assert result.chart_type != "pie"
    assert result.chart_type == "bar"
    assert result.figure is not None


def test_large_category_counts_do_not_produce_pie_chart():
    """Large category counts (> 6 items) do not produce an unreadable pie chart."""
    rows = [{"category": f"Cat_{i}", "category_revenue": 100.0 * i} for i in range(1, 10)]
    result = generate_chart(rows)
    assert result.chart_type == "bar"
    assert result.figure is not None


def test_duplicate_labels_do_not_crash():
    """Datasets with duplicate label values render safely without crashing."""
    rows = [
        {"category": "Electronics", "revenue": 100.0},
        {"category": "Electronics", "revenue": 200.0},
    ]
    result = generate_chart(rows)
    assert result.figure is not None


def test_unexpected_data_structures_handled_safely():
    """Malformed or unexpected row items return no chart safely."""
    rows = [{}, {}]
    result = generate_chart(rows)
    assert result.chart_type is None
    assert result.figure is None


def test_single_scalar_value_does_not_create_chart():
    """Single scalar metrics without any categorical or temporal dimension do not create a chart."""
    rows = [{"total_orders": 42}]
    result = generate_chart(rows)
    assert result.chart_type is None
    assert result.figure is None
    assert "single aggregate value" in result.reason.lower()


def test_id_columns_not_picked_as_primary_metric():
    """Surrogate keys and ID columns are avoided when a meaningful analytical metric is present."""
    rows = [
        {"customer_id": 101, "customer_name": "Alice", "total_spend": 550.0},
        {"customer_id": 102, "customer_name": "Bob", "total_spend": 320.0},
    ]
    result = generate_chart(rows)
    assert result.chart_type == "bar"
    assert "Spend" in result.title


def test_single_temporal_point_explains_lack_of_trend():
    """A dataset with only 1 calendar month explains that trend lines require >= 2 periods."""
    rows = [{"order_month": "2025-03-01", "total_revenue": 3294.95}]
    result = generate_chart(rows)
    assert result.chart_type is None
    assert result.figure is None
    assert "at least two time periods" in result.reason.lower()


def test_email_not_selected_as_chart_label_when_name_exists():
    """Categorical column selector prefers human names over email addresses."""
    rows = [
        {"first_name": "Alice", "email": "alice@example.com", "total_orders": 5},
        {"first_name": "Bob", "email": "bob@example.com", "total_orders": 3},
    ]
    result = generate_chart(rows)
    assert result.chart_type == "bar"
    assert "First Name" in result.title
    assert "Email" not in result.title


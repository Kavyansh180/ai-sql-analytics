"""Automatic chart selection and visualization module using Plotly."""
from dataclasses import dataclass
from datetime import date, datetime
import re
from typing import Any, Dict, List, Optional, Tuple
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go


@dataclass
class ChartResult:
    """Encapsulates the selected chart type, generated Plotly figure, and explanatory reason."""
    chart_type: Optional[str]  # "line", "bar", "pie", or None
    figure: Optional[go.Figure]
    title: str
    reason: Optional[str] = None


# Known identifier suffixes and names to avoid treating as primary analytical metrics
ID_PATTERNS = {"id", "pk", "uuid", "guid", "key"}


def is_id_column(col_name: str) -> bool:
    """Check if a column name indicates an internal surrogate key or ID."""
    clean = col_name.strip().lower()
    if clean in ID_PATTERNS:
        return True
    return clean.endswith(("_id", "_pk", "_uuid", "_key"))


def is_temporal_column(col_name: str, series: pd.Series) -> bool:
    """Check if a column represents temporal/date/time values."""
    clean = col_name.strip().lower()
    temporal_keywords = {"date", "time", "month", "year", "day", "timestamp", "period", "order_date", "order_month", "created_at"}
    if any(k in clean for k in temporal_keywords):
        return True

    # Check if dtype is already datetime
    if pd.api.types.is_datetime64_any_dtype(series):
        return True

    # Try parsing sample values as ISO dates
    non_null = series.dropna()
    if len(non_null) > 0:
        sample = str(non_null.iloc[0]).strip()
        # Common ISO date pattern YYYY-MM-DD or YYYY-MM
        if re.match(r"^\d{4}-\d{2}(-\d{2})?", sample):
            try:
                pd.to_datetime(sample)
                return True
            except Exception:
                pass
    return False


def clean_column_title(col_name: str) -> str:
    """Convert snake_case column names into readable Title Case labels."""
    return col_name.replace("_", " ").title()


def format_chart_layout(fig: go.Figure, title: str) -> go.Figure:
    """Apply consistent, professional corporate dark-mode styling to Plotly figures."""
    fig.update_layout(
        title={
            "text": title,
            "y": 0.95,
            "x": 0.03,
            "xanchor": "left",
            "yanchor": "top",
            "font": {
                "size": 15,
                "family": "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
                "color": "#f1f5f9",
            },
        },
        template="plotly_dark",
        paper_bgcolor="#0d1527",
        plot_bgcolor="#0d1527",
        margin={"l": 35, "r": 25, "t": 55, "b": 35},
        font={
            "family": "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
            "size": 12,
            "color": "#94a3b8",
        },
        hoverlabel={
            "bgcolor": "#1e293b",
            "bordercolor": "#334155",
            "font_size": 12,
            "font_family": "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
            "font_color": "#f8fafc",
        },
        legend={
            "font": {"color": "#cbd5e1", "size": 11},
            "bgcolor": "rgba(15,23,42,0.6)",
            "bordercolor": "#334155",
            "borderwidth": 1,
        },
        autosize=True,
    )
    return fig


def analyze_dataset_structure(
    df: pd.DataFrame,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Examine DataFrame columns and identify the best temporal, categorical, and numeric columns.
    
    Returns:
        (temporal_column, categorical_column, numeric_metric_column)
    """
    temporal_col: Optional[str] = None
    categorical_col: Optional[str] = None
    metric_col: Optional[str] = None

    candidate_metrics: List[str] = []
    candidate_id_metrics: List[str] = []
    candidate_categoricals: List[str] = []

    # First pass: identify temporal columns and candidate numeric metrics
    for col in df.columns:
        series = df[col]
        # Check temporal
        if temporal_col is None and is_temporal_column(str(col), series):
            temporal_col = str(col)
            continue

        # Check numeric
        numeric_series = pd.to_numeric(series, errors="coerce")
        if numeric_series.notna().sum() > 0 and (numeric_series.notna().sum() / max(len(series), 1)) >= 0.5:
            if is_id_column(str(col)):
                candidate_id_metrics.append(str(col))
            else:
                candidate_metrics.append(str(col))
        else:
            # Candidate categorical column
            if not is_id_column(str(col)):
                candidate_categoricals.append(str(col))

    # Prefer descriptive metric columns over IDs
    if candidate_metrics:
        # Prioritize revenue, amount, count, quantity, total
        priority_keywords = ["revenue", "amount", "total", "count", "orders", "quantity", "price", "profit"]
        chosen = candidate_metrics[0]
        for c in candidate_metrics:
            if any(k in c.lower() for k in priority_keywords):
                chosen = c
                break
        metric_col = chosen
    elif candidate_id_metrics:
        metric_col = candidate_id_metrics[0]

    # Pick best categorical column: prioritize names and categories over emails or surrogate codes
    if candidate_categoricals:
        priority_cat = ["name", "product", "customer", "category", "status", "country", "city"]
        chosen_cat = candidate_categoricals[0]
        for c in candidate_categoricals:
            if any(k in c.lower() for k in priority_cat) and "email" not in c.lower():
                chosen_cat = c
                break
        categorical_col = chosen_cat

    return temporal_col, categorical_col, metric_col


def generate_chart(
    rows: List[Dict[str, Any]],
    columns: Optional[List[str]] = None,
    preferred_title: Optional[str] = None,
) -> ChartResult:
    """
    Examine the returned analytical dataset and automatically select/render a Plotly figure.
    
    Decision Rules:
    1. Empty results or missing data -> No chart.
    2. Date/time column + numeric metric with >= 2 points -> Line chart.
    3. Exactly 1 categorical column + 1 numeric metric with 2 to 6 non-negative items summing > 0
       and category/status distribution -> Pie/Donut chart.
    4. Categorical column + numeric metric -> Bar chart (horizontal if long labels or > 8 items).
    5. Scalar or single-value result with no comparison axis -> No chart.
    """
    if not rows or len(rows) == 0:
        return ChartResult(
            chart_type=None,
            figure=None,
            title="",
            reason="A chart is not appropriate for this result set (no data rows).",
        )

    # Safe conversion to DataFrame without mutating input rows
    try:
        df = pd.DataFrame(rows)
    except Exception as err:
        return ChartResult(
            chart_type=None,
            figure=None,
            title="",
            reason=f"A chart is not appropriate for this result set (unable to parse rows: {type(err).__name__}).",
        )

    if df.empty or len(df.columns) == 0:
        return ChartResult(
            chart_type=None,
            figure=None,
            title="",
            reason="A chart is not appropriate for this result set (empty dataset).",
        )

    temporal_col, cat_col, metric_col = analyze_dataset_structure(df)

    # If no numeric metric exists, we cannot visualize quantities
    if metric_col is None:
        return ChartResult(
            chart_type=None,
            figure=None,
            title="",
            reason="A chart is not appropriate for this result set (no numeric metric found).",
        )

    # Convert metric series safely to numeric, dropping NaNs for visualization
    plot_df = df.copy()
    plot_df[metric_col] = pd.to_numeric(plot_df[metric_col], errors="coerce")
    plot_df = plot_df.dropna(subset=[metric_col])

    if plot_df.empty:
        return ChartResult(
            chart_type=None,
            figure=None,
            title="",
            reason="A chart is not appropriate for this result set (numeric column contains no valid values).",
        )

    metric_title = clean_column_title(metric_col)

    # --------------------------------------------------------------------------
    # 1. Line Chart: Temporal trend
    # --------------------------------------------------------------------------
    if temporal_col is not None:
        if len(plot_df) >= 2:
            try:
                plot_df["_sort_time"] = pd.to_datetime(plot_df[temporal_col], errors="coerce")
                plot_df = plot_df.sort_values(by="_sort_time", ascending=True)
            except Exception:
                # Fall back to index/natural sorting if date parsing fails
                plot_df = plot_df.sort_index()

            title = preferred_title or f"{metric_title} over {clean_column_title(temporal_col)}"
            fig = go.Figure()
            fig.add_trace(
                go.Scatter(
                    x=plot_df[temporal_col],
                    y=plot_df[metric_col],
                    mode="lines+markers",
                    line={"color": "#6366f1", "width": 3},
                    marker={"size": 7, "color": "#818cf8", "line": {"width": 1, "color": "#4338ca"}},
                    hovertemplate=f"<b>%{{x}}</b><br>{metric_title}: %{{y:,.2f}}<extra></extra>",
                )
            )
            fig.update_xaxes(
                title_text=clean_column_title(temporal_col),
                showgrid=True,
                gridcolor="#1e293b",
                zeroline=False,
                linecolor="#334155",
                tickfont={"color": "#94a3b8", "size": 11},
                title_font={"color": "#94a3b8", "size": 12},
            )
            fig.update_yaxes(
                title_text=metric_title,
                showgrid=True,
                gridcolor="#1e293b",
                zeroline=False,
                linecolor="#334155",
                tickfont={"color": "#94a3b8", "size": 11},
                title_font={"color": "#94a3b8", "size": 12},
            )
            format_chart_layout(fig, title)
            return ChartResult(chart_type="line", figure=fig, title=title)
        elif cat_col is None:
            # Single temporal point without any secondary comparison dimension
            return ChartResult(
                chart_type=None,
                figure=None,
                title="",
                reason="A trend line chart requires at least two time periods. The current dataset contains only one calendar month of orders.",
            )

    # --------------------------------------------------------------------------
    # 2. Pie Chart: Parts-of-a-whole composition
    # Criteria: Categorical column present, between 2 and 6 categories,
    # all non-negative values, total sum > 0, and not a time series.
    # --------------------------------------------------------------------------
    if cat_col is not None:
        category_count = len(plot_df[cat_col].unique())
        metric_values = plot_df[metric_col]
        has_negative = (metric_values < 0).any()
        total_sum = metric_values.sum()

        is_composition_candidate = (
            2 <= category_count <= 6
            and not has_negative
            and total_sum > 0
            and temporal_col is None
            and any(k in cat_col.lower() for k in ["category", "status", "type", "country", "share"])
        )

        if is_composition_candidate:
            title = preferred_title or f"{metric_title} by {clean_column_title(cat_col)}"
            fig = px.pie(
                plot_df,
                names=cat_col,
                values=metric_col,
                hole=0.45,
                color_discrete_sequence=[
                    "#6366f1",
                    "#06b6d4",
                    "#10b981",
                    "#f59e0b",
                    "#8b5cf6",
                    "#ec4899",
                ],
            )
            fig.update_traces(
                textposition="inside",
                textinfo="percent+label",
                textfont={"color": "#ffffff", "size": 11},
                marker={"line": {"color": "#0f172a", "width": 2}},
                hovertemplate=f"<b>%{{label}}</b><br>{metric_title}: %{{value:,.2f}}<br>Share: %{{percent}}<extra></extra>",
            )
            format_chart_layout(fig, title)
            return ChartResult(chart_type="pie", figure=fig, title=title)

    # --------------------------------------------------------------------------
    # 3. Bar Chart: Categorical comparison or ranking
    # --------------------------------------------------------------------------
    if cat_col is not None:
        title = preferred_title or f"{metric_title} by {clean_column_title(cat_col)}"
        plot_df[cat_col] = plot_df[cat_col].astype(str)

        max_label_len = plot_df[cat_col].str.len().max() if len(plot_df) > 0 else 0
        use_horizontal = len(plot_df) > 8 or max_label_len > 15

        fig = go.Figure()
        if use_horizontal:
            # Sort ascending so highest appears at the top of horizontal bar
            sorted_df = plot_df.sort_values(by=metric_col, ascending=True)
            fig.add_trace(
                go.Bar(
                    x=sorted_df[metric_col],
                    y=sorted_df[cat_col],
                    orientation="h",
                    marker={
                        "color": "#6366f1",
                        "line": {"color": "#818cf8", "width": 1},
                        "opacity": 0.92,
                    },
                    hovertemplate=f"<b>%{{y}}</b><br>{metric_title}: %{{x:,.2f}}<extra></extra>",
                )
            )
            fig.update_xaxes(
                title_text=metric_title,
                showgrid=True,
                gridcolor="#1e293b",
                zeroline=False,
                linecolor="#334155",
                tickfont={"color": "#94a3b8", "size": 11},
                title_font={"color": "#94a3b8", "size": 12},
            )
            fig.update_yaxes(
                title_text=clean_column_title(cat_col),
                showgrid=False,
                linecolor="#334155",
                tickfont={"color": "#94a3b8", "size": 11},
                title_font={"color": "#94a3b8", "size": 12},
            )
        else:
            # Vertical bar chart
            sorted_df = plot_df.sort_values(by=metric_col, ascending=False)
            fig.add_trace(
                go.Bar(
                    x=sorted_df[cat_col],
                    y=sorted_df[metric_col],
                    marker={
                        "color": "#6366f1",
                        "line": {"color": "#818cf8", "width": 1},
                        "opacity": 0.92,
                    },
                    hovertemplate=f"<b>%{{x}}</b><br>{metric_title}: %{{y:,.2f}}<extra></extra>",
                )
            )
            fig.update_xaxes(
                title_text=clean_column_title(cat_col),
                showgrid=False,
                linecolor="#334155",
                tickfont={"color": "#94a3b8", "size": 11},
                title_font={"color": "#94a3b8", "size": 12},
            )
            fig.update_yaxes(
                title_text=metric_title,
                showgrid=True,
                gridcolor="#1e293b",
                zeroline=False,
                linecolor="#334155",
                tickfont={"color": "#94a3b8", "size": 11},
                title_font={"color": "#94a3b8", "size": 12},
            )

        format_chart_layout(fig, title)
        return ChartResult(chart_type="bar", figure=fig, title=title)

    # --------------------------------------------------------------------------
    # 4. Single Scalar or No Categorical Axis
    # --------------------------------------------------------------------------
    return ChartResult(
        chart_type=None,
        figure=None,
        title="",
        reason="A chart is not appropriate for this result set (single aggregate value with no comparison dimensions).",
    )

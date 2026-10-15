"""Streamlit frontend application for AI SQL Analytics Copilot.

A modern, responsive analytics interface with split-workspace design:
- Left: Analytics results, KPI summary cards, interactive Plotly charts, data tables, and SQL metadata.
- Right: Dedicated AI Copilot chat panel with conversation history, suggested queries, and persistent composer.
"""
from datetime import datetime
import html
import os
import sys
from typing import Any, Dict, List, Optional
import pandas as pd
import streamlit as st

# Ensure repository root is on Python sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.frontend.charts import generate_chart
from src.frontend.client import AnalyticsAPIError, AnalyticsClient, get_api_base_url

# Page configuration
st.set_page_config(
    page_title="AI SQL Analytics Copilot",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Suggested business questions
EXAMPLE_QUESTIONS = [
    "Which product generated the highest revenue?",
    "Show monthly revenue trends.",
    "Which customers placed the most orders?",
    "Compare revenue across products.",
]


def inject_custom_css():
    """Inject cohesive, premium dark-mode styling for the analytics workspace."""
    st.markdown(
        """
        <style>
        /* Base typography and background */
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
        
        html, body, [class*="css"] {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        }

        .stApp {
            background-color: #090d16 !important;
            color: #f1f5f9 !important;
        }

        /* ==========================================================================
           Streamlit Top Header & Toolbar (Fixing bright white top bar & washed out text)
           ========================================================================== */
        header[data-testid="stHeader"] {
            background-color: #090d16 !important;
            border-bottom: 1px solid #1e293b !important;
            color: #cbd5e1 !important;
        }

        header[data-testid="stHeader"] * {
            color: #cbd5e1 !important;
        }

        /* Top rainbow gradient line */
        div[data-testid="stDecoration"] {
            display: none !important;
        }

        /* Streamlit toolbar & action icons */
        [data-testid="stToolbar"] {
            color: #cbd5e1 !important;
        }

        [data-testid="stToolbar"] button,
        [data-testid="stToolbar"] a,
        [data-testid="stToolbar"] svg {
            color: #cbd5e1 !important;
            fill: #cbd5e1 !important;
        }

        [data-testid="stToolbar"] button:hover,
        [data-testid="stToolbar"] a:hover,
        [data-testid="stToolbar"] button:hover svg {
            color: #ffffff !important;
            fill: #ffffff !important;
        }

        /* Deploy button styling - high contrast dark pill */
        .stDeployButton,
        [data-testid="stDeployButton"] {
            display: inline-flex !important;
        }

        .stDeployButton > button,
        [data-testid="stDeployButton"] > button {
            background-color: #1e293b !important;
            border: 1px solid #334155 !important;
            color: #ffffff !important;
            border-radius: 6px !important;
            font-size: 0.8rem !important;
            font-weight: 500 !important;
        }

        .stDeployButton > button:hover,
        [data-testid="stDeployButton"] > button:hover {
            background-color: #334155 !important;
            border-color: #6366f1 !important;
            color: #ffffff !important;
        }

        /* App status indicator (running spinner / stop) */
        [data-testid="stStatusWidget"] {
            background-color: #0f172a !important;
            border: 1px solid #1e293b !important;
            border-radius: 6px !important;
        }

        [data-testid="stStatusWidget"] * {
            color: #cbd5e1 !important;
        }

        /* Global headings & text contrast */
        h1, h2, h3, h4, h5, h6,
        [data-testid="stMarkdownContainer"] h1,
        [data-testid="stMarkdownContainer"] h2,
        [data-testid="stMarkdownContainer"] h3 {
            color: #ffffff !important;
            font-weight: 700 !important;
        }

        p, [data-testid="stMarkdownContainer"] p {
            color: #cbd5e1 !important;
            line-height: 1.6 !important;
        }

        li, [data-testid="stMarkdownContainer"] li {
            color: #cbd5e1 !important;
            line-height: 1.6 !important;
        }

        strong, b, [data-testid="stMarkdownContainer"] strong {
            color: #ffffff !important;
            font-weight: 600 !important;
        }

        .stCaption, [data-testid="stCaptionContainer"] p, [data-testid="stCaptionContainer"] {
            color: #94a3b8 !important;
            font-size: 0.8rem !important;
        }

        /* ==========================================================================
           Sidebar High Contrast & Readability (Fixing blending grey text)
           ========================================================================== */
        [data-testid="stSidebar"] {
            background-color: #0b1120 !important;
            border-right: 1px solid #1e293b !important;
        }

        [data-testid="stSidebar"] .stMarkdown,
        [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] {
            color: #cbd5e1 !important;
        }

        [data-testid="stSidebar"] h1,
        [data-testid="stSidebar"] h2,
        [data-testid="stSidebar"] h3,
        [data-testid="stSidebar"] h4,
        [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] h1,
        [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] h2,
        [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] h3 {
            color: #ffffff !important;
            font-weight: 700 !important;
            letter-spacing: -0.01em !important;
        }

        [data-testid="stSidebar"] p,
        [data-testid="stSidebar"] span,
        [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p,
        [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] span {
            color: #cbd5e1 !important;
            font-size: 0.88rem !important;
            line-height: 1.6 !important;
        }

        [data-testid="stSidebar"] li,
        [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] li {
            color: #cbd5e1 !important;
            font-size: 0.88rem !important;
            line-height: 1.6 !important;
            margin-bottom: 0.35rem !important;
        }

        [data-testid="stSidebar"] strong,
        [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] strong {
            color: #ffffff !important;
            font-weight: 600 !important;
        }

        [data-testid="stSidebar"] hr {
            border-color: #1e293b !important;
            margin: 1.25rem 0 !important;
        }

        /* Inline code chips - replaces white box with sleek indigo badge */
        code,
        [data-testid="stSidebar"] code,
        [data-testid="stMarkdownContainer"] code {
            background-color: rgba(99, 102, 241, 0.18) !important;
            color: #c7d2fe !important;
            border: 1px solid rgba(99, 102, 241, 0.35) !important;
            padding: 0.15rem 0.45rem !important;
            border-radius: 5px !important;
            font-size: 0.82rem !important;
            font-weight: 500 !important;
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace !important;
        }

        /* ==========================================================================
           Input Fields & Textarea (Fixing invisible input text contrast)
           ========================================================================== */
        div[data-baseweb="textarea"],
        div[data-baseweb="textarea"] > div,
        div[data-baseweb="base-input"],
        div[data-baseweb="input"],
        div[data-baseweb="input"] > div {
            background-color: #0f172a !important;
            border-color: #334155 !important;
            border-radius: 8px !important;
        }

        textarea,
        textarea[data-testid="stTextArea"],
        div[data-baseweb="textarea"] textarea,
        .stTextArea textarea,
        input[type="text"],
        div[data-baseweb="input"] input {
            background-color: #0f172a !important;
            color: #ffffff !important;
            -webkit-text-fill-color: #ffffff !important;
            caret-color: #38bdf8 !important;
            border: 1px solid #334155 !important;
            border-radius: 8px !important;
            font-size: 0.95rem !important;
            font-weight: 500 !important;
            line-height: 1.5 !important;
        }

        div[data-baseweb="textarea"]:focus-within,
        .stTextArea textarea:focus,
        div[data-baseweb="input"]:focus-within {
            border-color: #6366f1 !important;
            box-shadow: 0 0 0 2px rgba(99, 102, 241, 0.35) !important;
        }

        .stTextArea textarea::placeholder,
        input::placeholder {
            color: #64748b !important;
            -webkit-text-fill-color: #64748b !important;
            opacity: 1 !important;
        }

        div[data-testid="stWidgetLabel"] label,
        div[data-testid="stWidgetLabel"] p,
        .stTextArea label {
            color: #f8fafc !important;
            font-size: 0.92rem !important;
            font-weight: 600 !important;
            margin-bottom: 0.35rem !important;
        }

        .stTextArea small,
        div[data-testid="stForm"] small,
        div[data-testid="InputInstructions"] > span,
        div[data-baseweb="textarea"] + div {
            color: #94a3b8 !important;
            font-size: 0.75rem !important;
        }

        /* Form container */
        div[data-testid="stForm"] {
            border: 1px solid #1e293b !important;
            border-radius: 10px !important;
            padding: 1rem !important;
            background-color: #0d1527 !important;
        }

        /* Buttons & Actions */
        button[data-testid="baseButton-primary"],
        button[kind="primary"],
        button[kind="primaryFormSubmit"],
        div[data-testid="stFormSubmitButton"] button,
        .stButton button[kind="primary"] {
            background-color: #4f46e5 !important;
            border: 1px solid #6366f1 !important;
            color: #ffffff !important;
            font-weight: 600 !important;
            font-size: 0.95rem !important;
            border-radius: 8px !important;
            box-shadow: 0 2px 4px rgba(79, 70, 229, 0.25) !important;
            transition: all 0.15s ease-in-out !important;
        }

        button[data-testid="baseButton-primary"]:hover,
        button[kind="primary"]:hover,
        button[kind="primaryFormSubmit"]:hover,
        div[data-testid="stFormSubmitButton"] button:hover,
        .stButton button[kind="primary"]:hover {
            background-color: #4338ca !important;
            border-color: #818cf8 !important;
            box-shadow: 0 4px 12px rgba(79, 70, 229, 0.4) !important;
        }

        button[data-testid="baseButton-secondary"],
        .stButton button {
            background-color: #1e293b !important;
            border: 1px solid #334155 !important;
            color: #f1f5f9 !important;
            border-radius: 8px !important;
            font-weight: 500 !important;
            transition: all 0.15s ease-in-out !important;
        }

        button[data-testid="baseButton-secondary"]:hover,
        .stButton button:hover {
            background-color: #334155 !important;
            border-color: #6366f1 !important;
            color: #ffffff !important;
        }

        /* ==========================================================================
           Native KPI Metrics (Fixing ugly raw HTML in image 1)
           ========================================================================== */
        div[data-testid="stMetric"] {
            background: #0d1527 !important;
            border: 1px solid #1e293b !important;
            border-radius: 10px !important;
            padding: 0.85rem 1rem !important;
            box-shadow: 0 1px 3px rgba(0, 0, 0, 0.25) !important;
            transition: border-color 0.15s ease, transform 0.15s ease !important;
        }

        div[data-testid="stMetric"]:hover {
            border-color: #334155 !important;
            transform: translateY(-1px) !important;
        }

        div[data-testid="stMetricLabel"] p,
        div[data-testid="stMetricLabel"] span,
        div[data-testid="stMetricLabel"] {
            color: #94a3b8 !important;
            font-size: 0.72rem !important;
            font-weight: 600 !important;
            text-transform: uppercase !important;
            letter-spacing: 0.05em !important;
        }

        div[data-testid="stMetricValue"] div,
        div[data-testid="stMetricValue"] span,
        div[data-testid="stMetricValue"] {
            color: #ffffff !important;
            font-size: 1.35rem !important;
            font-weight: 700 !important;
        }

        div[data-testid="stMetricDelta"] {
            color: #34d399 !important;
            font-size: 0.75rem !important;
        }

        /* Status Badges */
        .badge {
            display: inline-flex;
            align-items: center;
            gap: 0.35rem;
            padding: 0.2rem 0.55rem;
            border-radius: 9999px;
            font-size: 0.72rem;
            font-weight: 600;
            line-height: 1;
        }

        .badge-ready {
            background-color: rgba(16, 185, 129, 0.15);
            color: #34d399;
            border: 1px solid rgba(16, 185, 129, 0.3);
        }

        .badge-info {
            background-color: rgba(99, 102, 241, 0.15);
            color: #818cf8;
            border: 1px solid rgba(99, 102, 241, 0.3);
        }

        .badge-warn {
            background-color: rgba(245, 158, 11, 0.15);
            color: #fbbf24;
            border: 1px solid rgba(245, 158, 11, 0.3);
        }

        .badge-danger {
            background-color: rgba(244, 63, 94, 0.15);
            color: #fb7185;
            border: 1px solid rgba(244, 63, 94, 0.3);
        }

        /* Alert / Insight callouts */
        div[data-testid="stAlert"] {
            background-color: rgba(99, 102, 241, 0.08) !important;
            border: 1px solid rgba(99, 102, 241, 0.25) !important;
            border-left: 4px solid #6366f1 !important;
            border-radius: 8px !important;
            color: #cbd5e1 !important;
        }

        div[data-testid="stAlert"] * {
            color: #cbd5e1 !important;
        }

        div[data-testid="stAlert"] strong {
            color: #ffffff !important;
        }

        /* Chat messages */
        .chat-bubble-user {
            background: linear-gradient(135deg, rgba(79, 70, 229, 0.18) 0%, rgba(30, 27, 75, 0.4) 100%);
            border: 1px solid rgba(99, 102, 241, 0.35);
            border-radius: 10px;
            padding: 0.85rem 1rem;
            margin-bottom: 0.75rem;
            color: #ffffff;
        }

        .chat-bubble-assistant {
            background: #0d1527;
            border: 1px solid #1e293b;
            border-radius: 10px;
            padding: 0.85rem 1rem;
            margin-bottom: 1rem;
            color: #cbd5e1;
            font-size: 0.9rem;
            line-height: 1.5;
        }

        .chat-meta {
            display: flex;
            align-items: center;
            justify-content: space-between;
            font-size: 0.72rem;
            color: #94a3b8;
            margin-bottom: 0.35rem;
        }

        .chat-author-user {
            color: #818cf8;
            font-weight: 600;
        }

        .chat-author-copilot {
            color: #38bdf8;
            font-weight: 600;
        }

        /* Empty state cards */
        .empty-state-card {
            border: 1px dashed #334155;
            background: rgba(15, 23, 42, 0.5);
            border-radius: 12px;
            padding: 2.5rem 1.5rem;
            text-align: center;
            margin: 1.5rem 0;
        }

        .empty-icon {
            font-size: 2.5rem;
            margin-bottom: 0.75rem;
            opacity: 0.9;
        }

        .starter-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 0.85rem;
            margin-top: 1.5rem;
            text-align: left;
        }

        .starter-card {
            background: #0f172a;
            border: 1px solid #1e293b;
            border-radius: 8px;
            padding: 0.85rem 1rem;
        }

        .starter-card-title {
            font-size: 0.82rem;
            font-weight: 600;
            color: #f1f5f9;
            margin-bottom: 0.25rem;
        }

        .starter-card-desc {
            font-size: 0.75rem;
            color: #94a3b8;
            line-height: 1.35;
        }

        /* ==========================================================================
           Expander Styling (Dark, Seamless, High Contrast)
           ========================================================================== */
        div[data-testid="stExpander"],
        div[data-testid="stExpander"] details,
        div[data-testid="stExpander"] summary,
        details[data-testid="stExpanderDetails"] {
            background-color: #0d1527 !important;
            border: 1px solid #1e293b !important;
            border-radius: 8px !important;
            color: #ffffff !important;
        }

        div[data-testid="stExpander"] summary:hover {
            background-color: #131d33 !important;
            border-color: #334155 !important;
        }

        div[data-testid="stExpander"] summary,
        div[data-testid="stExpander"] summary p,
        div[data-testid="stExpander"] summary span,
        div[data-testid="stExpander"] summary svg {
            color: #ffffff !important;
            fill: #ffffff !important;
            font-weight: 600 !important;
            font-size: 0.92rem !important;
        }

        div[data-testid="stExpander"] details[open] {
            background-color: #0d1527 !important;
            border: 1px solid #1e293b !important;
        }

        div[data-testid="stExpander"] details > div,
        div[data-testid="stExpanderDetails"] > div {
            background-color: #0d1527 !important;
            color: #cbd5e1 !important;
            padding-top: 0.5rem !important;
        }

        /* ==========================================================================
           Code Block (st.code) Styling - Crisp Dark Theme & High Contrast
           ========================================================================== */
        div[data-testid="stCode"],
        div[data-testid="stCodeBlock"],
        div[data-testid="stCode"] pre,
        div[data-testid="stCodeBlock"] pre,
        div[data-testid="stCode"] pre code,
        div[data-testid="stCodeBlock"] pre code {
            background-color: #080d1a !important;
            color: #f1f5f9 !important;
            border: 1px solid #1e293b !important;
            border-radius: 8px !important;
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace !important;
            font-size: 0.88rem !important;
            line-height: 1.55 !important;
        }

        /* Syntax highlighting tokens in code block */
        div[data-testid="stCode"] span,
        div[data-testid="stCodeBlock"] span {
            color: #93c5fd !important;
        }

        div[data-testid="stCode"] span.token.keyword,
        div[data-testid="stCodeBlock"] span.token.keyword {
            color: #a5b4fc !important;
            font-weight: 600 !important;
        }

        div[data-testid="stCode"] span.token.string,
        div[data-testid="stCodeBlock"] span.token.string {
            color: #34d399 !important;
        }

        div[data-testid="stCode"] span.token.number,
        div[data-testid="stCodeBlock"] span.token.number {
            color: #fbbf24 !important;
        }

        div[data-testid="stCode"] span.token.function,
        div[data-testid="stCodeBlock"] span.token.function {
            color: #38bdf8 !important;
        }

        /* Copy button on code block */
        div[data-testid="stCode"] button,
        div[data-testid="stCodeBlock"] button,
        button[data-testid="stCopyButton"] {
            background-color: #1e293b !important;
            border: 1px solid #334155 !important;
            color: #cbd5e1 !important;
            border-radius: 6px !important;
        }

        div[data-testid="stCode"] button:hover,
        div[data-testid="stCodeBlock"] button:hover,
        button[data-testid="stCopyButton"]:hover {
            background-color: #334155 !important;
            color: #ffffff !important;
        }

        div[data-testid="stCode"] button svg,
        div[data-testid="stCodeBlock"] button svg {
            fill: #cbd5e1 !important;
        }

        /* Streamlit components refinement */

        .stTabs [data-baseweb="tab-list"] {
            gap: 1.5rem;
            background-color: transparent;
            border-bottom: 1px solid #1e293b;
        }

        .stTabs [data-baseweb="tab"] {
            padding: 0.5rem 0.25rem;
            color: #94a3b8 !important;
            font-size: 0.88rem;
            font-weight: 500;
        }

        .stTabs [aria-selected="true"] {
            color: #818cf8 !important;
            border-bottom-color: #6366f1 !important;
        }

        .stTabs [data-baseweb="tab-panel"],
        div[data-testid="stTabContent"],
        div[data-testid="stPlotlyChart"],
        .js-plotly-plot {
            background-color: #0d1527 !important;
            border-radius: 8px !important;
        }

        .suggestion-prompt {
            font-size: 0.78rem;
            font-weight: 600;
            color: #94a3b8;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin: 0.5rem 0 0.35rem 0;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def init_session_state():
    """Initialize session state variables for input, query results, and chat history."""
    if "user_question" not in st.session_state:
        st.session_state["user_question"] = ""
    if "query_result" not in st.session_state:
        st.session_state["query_result"] = None
    if "last_error" not in st.session_state:
        st.session_state["last_error"] = None
    if "last_submitted_question" not in st.session_state:
        st.session_state["last_submitted_question"] = ""
    if "chat_history" not in st.session_state:
        st.session_state["chat_history"] = []
    if "copilot_collapsed" not in st.session_state:
        st.session_state["copilot_collapsed"] = False


def compute_kpis_from_rows(
    rows: List[Dict[str, Any]], execution_time_ms: Optional[float]
) -> Dict[str, Any]:
    """Derive summary KPIs safely and strictly from real query response rows."""
    row_count = len(rows)
    exec_str = f"{execution_time_ms:.1f} ms" if execution_time_ms is not None else "N/A"

    if not rows:
        return {
            "row_count": 0,
            "execution_time": exec_str,
            "metric_title": None,
            "metric_value": None,
        }

    # Inspect first row to discover numeric candidates
    first_row = rows[0]
    metric_totals: Dict[str, float] = {}

    for col_name, val in first_row.items():
        clean_col = col_name.strip().lower()
        if clean_col in {"id", "customer_id", "product_id", "order_id"}:
            continue

        # Try converting entire column to numeric
        values = []
        for r in rows:
            v = r.get(col_name)
            if v is not None:
                try:
                    values.append(float(v))
                except (ValueError, TypeError):
                    pass

        if len(values) == len(rows) and len(values) > 0:
            metric_totals[col_name] = sum(values)

    chosen_col = None
    if metric_totals:
        # Prioritize revenue, amount, total, spend
        priority_keywords = ["revenue", "amount", "total", "spend", "orders", "quantity", "price"]
        for kw in priority_keywords:
            for c in metric_totals:
                if kw in c.lower():
                    chosen_col = c
                    break
            if chosen_col:
                break

        if not chosen_col:
            chosen_col = list(metric_totals.keys())[0]

    metric_val_str = None
    metric_title_str = None

    if chosen_col:
        total_val = metric_totals[chosen_col]
        clean_title = chosen_col.replace("_", " ").title()

        # Format intelligently based on metric type
        if any(k in chosen_col.lower() for k in ["revenue", "amount", "spend", "price"]):
            metric_title_str = f"Total {clean_title}" if row_count > 1 else clean_title
            metric_val_str = f"${total_val:,.2f}"
        elif any(k in chosen_col.lower() for k in ["count", "orders", "quantity", "units"]):
            metric_title_str = f"Total {clean_title}" if row_count > 1 else clean_title
            metric_val_str = f"{int(total_val):,}" if total_val.is_integer() else f"{total_val:,.1f}"
        else:
            metric_title_str = f"Sum ({clean_title})" if row_count > 1 else clean_title
            metric_val_str = f"{total_val:,.2f}"

    return {
        "row_count": row_count,
        "execution_time": exec_str,
        "metric_title": metric_title_str,
        "metric_value": metric_val_str,
    }


def render_sidebar(client: AnalyticsClient):
    """Render backend connection health, analytics schema scope, and session actions."""
    with st.sidebar:
        st.markdown("### ⚙️ System Status")

        health = client.check_health()
        status = health.get("status")
        is_cloud_backend = (
            "lambda-url" in client.base_url
            or "amazonaws.com" in client.base_url
            or client.base_url.startswith("https://")
        )

        if status == "healthy":
            version_str = f" (v{health.get('version')})" if health.get("version") else ""
            st.success(f"🟢 **Backend Online{version_str}**\n\n`{client.base_url}`")
        elif status == "warming":
            st.warning(
                "🟡 **Backend Warming Up...**\n\n"
                "AWS Lambda is completing a cold start. Initialization usually completes in 10–15 seconds."
            )
            if st.button("🔄 Refresh Status", key="btn_refresh_warming", use_container_width=True):
                st.rerun()
        else:
            st.error(f"🔴 **Backend Offline**\n\n`{client.base_url}`")
            if is_cloud_backend:
                st.caption(
                    "Cloud endpoint did not respond. If the serverless container was sleeping, click Retry to warm it."
                )
                if st.button("🔄 Retry Connection", key="btn_retry_conn", use_container_width=True):
                    st.rerun()
            else:
                st.caption(
                    "Start backend with: `.\\.venv\\Scripts\\python.exe -m uvicorn src.api.main:app --port 8000`"
                )

        st.markdown("---")
        st.markdown("### 📊 Analytics Schema")
        st.markdown(
            "The copilot queries the **`analytics`** schema:\n\n"
            "- **`customers`**: Demographics and accounts\n"
            "- **`products`**: Catalog, categories, prices\n"
            "- **`orders`**: Order dates, statuses, amounts\n"
            "- **`order_items`**: Quantities, unit prices\n"
        )

        st.markdown("---")
        st.markdown("### 🛡️ Security Policy")
        st.markdown(
            "- **Read-Only** transactions (`app_user`)\n"
            "- **3,000ms** statement timeout\n"
            "- Automated column masking on PII\n"
        )

        st.markdown("---")
        if st.button("🧹 Clear Conversation", use_container_width=True):
            st.session_state["user_question"] = ""
            st.session_state["query_result"] = None
            st.session_state["last_error"] = None
            st.session_state["last_submitted_question"] = ""
            st.session_state["chat_history"] = []
            st.rerun()


def render_analytics_header(result: Optional[Dict[str, Any]], error: Optional[str]):
    """Render top header bar of the analytics workspace."""
    h_col1, h_col2 = st.columns([0.7, 0.3])
    with h_col1:
        st.markdown(
            "<div style='font-size: 1.15rem; font-weight: 700; color: #ffffff;'>📊 Analytics Workspace</div>",
            unsafe_allow_html=True,
        )
        if result and result.get("status") == "ready":
            st.markdown(
                f"<div style='font-size: 0.8rem; color: #94a3b8;'>Active Query: <span style='color: #ffffff; font-weight: 500;'>{html.escape(result.get('question', ''))}</span></div>",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                "<div style='font-size: 0.8rem; color: #94a3b8;'>Live query results, visualizations, and data explorer</div>",
                unsafe_allow_html=True,
            )
    with h_col2:
        if result:
            status = result.get("status", "")
            if status == "ready":
                st.markdown(
                    "<div style='text-align: right;'><span class='badge badge-ready'>● Ready</span></div>",
                    unsafe_allow_html=True,
                )
            elif status == "clarification_needed":
                st.markdown(
                    "<div style='text-align: right;'><span class='badge badge-warn'>🤔 Clarification</span></div>",
                    unsafe_allow_html=True,
                )
            elif status == "unsupported":
                st.markdown(
                    "<div style='text-align: right;'><span class='badge badge-danger'>🚫 Unsupported</span></div>",
                    unsafe_allow_html=True,
                )
        elif error:
            st.markdown(
                "<div style='text-align: right;'><span class='badge badge-danger'>⚠️ Error</span></div>",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                "<div style='text-align: right;'><span class='badge badge-info'>⚡ Standby</span></div>",
                unsafe_allow_html=True,
            )


def render_analytics_empty_state():
    """Render an empty state prior to executing any queries."""
    st.markdown(
        '<div class="empty-state-card">'
        '<div class="empty-icon">⚡</div>'
        '<div style="font-size: 1.15rem; font-weight: 700; color: #ffffff; margin-bottom: 0.35rem;">AI SQL Analytics Workspace</div>'
        '<div style="font-size: 0.88rem; color: #cbd5e1; max-width: 460px; margin: 0 auto; line-height: 1.5;">'
        'Ask a natural language business question in the AI Copilot on the right to generate secure SQL, KPIs, and interactive charts.'
        '</div>'
        '<div class="starter-grid">'
        '<div class="starter-card">'
        '<div class="starter-card-title">📈 Automated Charts</div>'
        '<div class="starter-card-desc">Automatically selects line, bar, or pie visualizations based on result distribution.</div>'
        '</div>'
        '<div class="starter-card">'
        '<div class="starter-card-title">🛡️ Secure SQL</div>'
        '<div class="starter-card-desc">Read-only role enforcement, 3,000ms timeouts, and schema isolation.</div>'
        '</div>'
        '<div class="starter-card">'
        '<div class="starter-card-title">💡 Plain-English Insights</div>'
        '<div class="starter-card-desc">Every query is accompanied by plain-English explanations of findings.</div>'
        '</div>'
        '</div>'
        '</div>',
        unsafe_allow_html=True,
    )


def render_analytics_results():
    """Render analytics results: KPI cards, explanation, charts, data table, and SQL drawer."""
    error = st.session_state.get("last_error")
    result = st.session_state.get("query_result")

    render_analytics_header(result, error)
    st.markdown("<div style='height: 0.5rem;'></div>", unsafe_allow_html=True)

    if error:
        st.markdown(
            f"""
            <div style="background: rgba(244, 63, 94, 0.1); border: 1px solid rgba(244, 63, 94, 0.3); border-radius: 8px; padding: 1rem; margin: 1rem 0;">
                <div style="color: #fb7185; font-weight: 600; font-size: 0.9rem; margin-bottom: 0.25rem;">⚠️ Request Failed</div>
                <div style="color: #f1f5f9; font-size: 0.85rem; line-height: 1.4;">{html.escape(error)}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        return

    if not result:
        render_analytics_empty_state()
        return

    status = result.get("status")
    explanation = result.get("explanation", "")
    sql = result.get("sql")
    rows = result.get("rows", [])
    execution_time_ms = result.get("execution_time_ms")

    if status == "ready":
        # 1. Native Streamlit KPI Cards (Clean, high-contrast, immune to HTML code-block rendering)
        kpi_data = compute_kpis_from_rows(rows, execution_time_ms)
        has_metric = bool(kpi_data["metric_title"] and kpi_data["metric_value"])
        kpi_cols = st.columns(4 if has_metric else 3)
        with kpi_cols[0]:
            st.metric(label="Rows Returned", value=kpi_data["row_count"])
        with kpi_cols[1]:
            st.metric(label="Execution Time", value=kpi_data["execution_time"])
        if has_metric:
            with kpi_cols[2]:
                st.metric(label=kpi_data["metric_title"], value=kpi_data["metric_value"])
            with kpi_cols[3]:
                st.metric(label="Security Scope", value="analytics", help="Read-Only Enforced")
        else:
            with kpi_cols[2]:
                st.metric(label="Security Scope", value="analytics", help="Read-Only Enforced")

        # 2. Plain-English Business Explanation using styled native alert
        if explanation:
            st.info(f"💡 **Analysis & Business Insights**\n\n{explanation}")

        # 3. Chart and Table Navigation
        if len(rows) > 0:
            df = pd.DataFrame(rows)
            chart_res = generate_chart(rows, columns=result.get("columns"))

            if chart_res.figure is not None:
                tab_chart, tab_data = st.tabs(["📊 Interactive Chart", "📋 Data Table"])
                with tab_chart:
                    st.plotly_chart(
                        chart_res.figure,
                        use_container_width=True,
                        theme=None,
                        config={"displayModeBar": "hover", "displaylogo": False},
                    )
                    st.caption(
                        f"Visualizer: **{chart_res.chart_type.title()} Chart** (Automatically selected from dataset properties)"
                    )
                with tab_data:
                    st.dataframe(df, use_container_width=True, hide_index=True)
                    st.download_button(
                        label="📥 Download Results as CSV",
                        data=df.to_csv(index=False),
                        file_name="analytics_result.csv",
                        mime="text/csv",
                        key="btn_download_csv_tab",
                    )
            else:
                st.dataframe(df, use_container_width=True, hide_index=True)
                if chart_res.reason:
                    st.caption(f"ℹ️ {chart_res.reason}")
                st.download_button(
                    label="📥 Download Results as CSV",
                    data=df.to_csv(index=False),
                    file_name="analytics_result.csv",
                    mime="text/csv",
                    key="btn_download_csv_solo",
                )
        else:
            st.warning("ℹ️ The query executed successfully, but returned 0 rows for the specified criteria.")

        # 4. Expandable SQL and Query Metadata
        if sql:
            with st.expander("🔍 View Generated PostgreSQL Query & Execution Metadata", expanded=False):
                st.code(sql, language="sql")
                m_col1, m_col2, m_col3 = st.columns(3)
                with m_col1:
                    st.markdown("<span style='color: #94a3b8; font-size: 0.82rem;'>Target Schema: </span>`analytics`", unsafe_allow_html=True)
                with m_col2:
                    st.markdown("<span style='color: #94a3b8; font-size: 0.82rem;'>Role: </span>`app_user` <span style='color: #94a3b8; font-size: 0.78rem;'>(Read-Only)</span>", unsafe_allow_html=True)
                with m_col3:
                    st.markdown("<span style='color: #94a3b8; font-size: 0.82rem;'>Statement Timeout: </span>`3,000 ms`", unsafe_allow_html=True)

    elif status == "clarification_needed":
        clarification_q = result.get("clarification_question", explanation)
        st.warning(
            f"🤔 **Clarification Needed**\n\n"
            f"**{clarification_q}**\n\n"
            f"*{explanation}*\n\n"
            "Please refine your question in the Copilot panel on the right with additional details."
        )

    elif status == "unsupported":
        st.error(
            f"🚫 **Request Unsupported**\n\n"
            f"{explanation}"
        )


def render_copilot_panel(client: AnalyticsClient):
    """Render dedicated AI Copilot panel: conversation history, suggested queries, and persistent composer."""
    # Copilot Header with status and collapse action
    c_head1, c_head2 = st.columns([0.7, 0.3])
    with c_head1:
        st.markdown(
            "<div style='font-size: 1.15rem; font-weight: 700; color: #ffffff;'>⚡ AI Copilot</div>",
            unsafe_allow_html=True,
        )
        st.markdown(
            "<div style='font-size: 0.78rem; color: #94a3b8;'>Natural Language to Secure SQL</div>",
            unsafe_allow_html=True,
        )
    with c_head2:
        is_collapsed = st.session_state.get("copilot_collapsed", False)
        toggle_label = "Expand ◨" if is_collapsed else "Collapse ‹"
        if st.button(toggle_label, key="btn_toggle_copilot", help="Toggle split/full layout"):
            st.session_state["copilot_collapsed"] = not is_collapsed
            st.rerun()

    st.markdown("<div style='height: 0.5rem;'></div>", unsafe_allow_html=True)

    # Conversation History
    history: List[Dict[str, Any]] = st.session_state.get("chat_history", [])

    if not history:
        # Initial greeting & Suggested questions
        st.markdown(
            '<div style="background: #0d1527; border: 1px solid #1e293b; border-radius: 10px; padding: 1rem; margin-bottom: 0.85rem;">'
            '<div style="color: #818cf8; font-weight: 600; font-size: 0.88rem; margin-bottom: 0.25rem;">👋 Welcome to SQL Analytics Copilot</div>'
            '<div style="color: #cbd5e1; font-size: 0.82rem; line-height: 1.45;">'
            'Ask questions about revenue, customer orders, or product sales in plain English.'
            '</div>'
            '</div>',
            unsafe_allow_html=True,
        )

        st.markdown("<div class='suggestion-prompt'>💡 Suggested Questions</div>", unsafe_allow_html=True)
        for idx, ex_text in enumerate(EXAMPLE_QUESTIONS):
            if st.button(ex_text, key=f"btn_ex_{idx}", use_container_width=True):
                st.session_state["user_question"] = ex_text
                st.session_state["auto_submit"] = True
                st.rerun()
    else:
        # Render readable conversation history
        for msg in history:
            role = msg.get("role")
            content = msg.get("content", "")
            ts = msg.get("timestamp", "")

            if role == "user":
                st.markdown(
                    f'<div class="chat-bubble-user">'
                    f'<div class="chat-meta">'
                    f'<span class="chat-author-user">👤 You</span>'
                    f'<span style="color: #94a3b8;">{ts}</span>'
                    f'</div>'
                    f'<div style="font-weight: 500; color: #ffffff; font-size: 0.95rem;">{html.escape(content)}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            else:
                status = msg.get("status", "ready")
                status_pill = ""
                if status == "ready":
                    status_pill = f'<span class="badge badge-ready">Ready • {msg.get("row_count", 0)} rows</span>'
                elif status == "clarification_needed":
                    status_pill = '<span class="badge badge-warn">Clarification</span>'
                elif status == "unsupported":
                    status_pill = '<span class="badge badge-danger">Unsupported</span>'
                elif status == "error":
                    status_pill = '<span class="badge badge-danger">Error</span>'

                st.markdown(
                    f'<div class="chat-bubble-assistant">'
                    f'<div class="chat-meta">'
                    f'<span class="chat-author-copilot">⚡ Copilot</span>'
                    f'<div>{status_pill} <span style="margin-left: 4px; color: #94a3b8;">{ts}</span></div>'
                    f'</div>'
                    f'<div style="color: #e2e8f0; font-size: 0.9rem; line-height: 1.5;">{html.escape(content)}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

        # Quick suggestion chips accessible even when history exists
        with st.expander("💡 Suggested Questions", expanded=False):
            for idx, ex_text in enumerate(EXAMPLE_QUESTIONS):
                if st.button(ex_text, key=f"btn_ex_hist_{idx}", use_container_width=True):
                    st.session_state["user_question"] = ex_text
                    st.session_state["auto_submit"] = True
                    st.rerun()

    # Persistent Question Composer
    st.markdown("<div style='height: 0.5rem;'></div>", unsafe_allow_html=True)
    auto_submit = st.session_state.pop("auto_submit", False)

    with st.form(key="copilot_form", clear_on_submit=False):
        question_input = st.text_area(
            label="Ask a business question:",
            value=st.session_state.get("user_question", ""),
            height=85,
            placeholder="e.g. Which product generated the highest revenue?",
            help="Type your analytics query in plain English and click Send.",
        )
        submit_clicked = st.form_submit_button(
            "🚀 Ask Copilot",
            type="primary",
            use_container_width=True,
        )

    # Execution logic: honor both form submit and instant suggestion clicks
    cleaned_question = question_input.strip() if submit_clicked else (
        st.session_state.get("user_question", "").strip() if auto_submit else ""
    )
    should_run = bool(cleaned_question)

    if should_run:
        cleaned_question = cleaned_question.strip()
        st.session_state["user_question"] = cleaned_question

        # Avoid redundant duplicate execution
        if (
            not submit_clicked
            and cleaned_question == st.session_state["last_submitted_question"]
            and st.session_state["query_result"] is not None
        ):
            pass
        else:
            with st.spinner("Analyzing question, generating secure SQL, and querying database... (May take ~10–15s if serverless backend is warming)"):
                now_str = datetime.now().strftime("%H:%M")
                try:
                    result = client.query(cleaned_question, force=True)
                    st.session_state["query_result"] = result
                    st.session_state["last_error"] = None
                    st.session_state["last_submitted_question"] = cleaned_question

                    # Append to conversation history
                    st.session_state["chat_history"].append(
                        {
                            "role": "user",
                            "content": cleaned_question,
                            "timestamp": now_str,
                        }
                    )
                    st.session_state["chat_history"].append(
                        {
                            "role": "assistant",
                            "content": result.get("explanation", ""),
                            "status": result.get("status", "ready"),
                            "row_count": result.get("row_count", 0),
                            "execution_time_ms": result.get("execution_time_ms"),
                            "timestamp": now_str,
                        }
                    )
                    st.rerun()

                except AnalyticsAPIError as api_err:
                    err_msg = str(api_err)
                    st.session_state["last_error"] = err_msg
                    st.session_state["query_result"] = None
                    st.session_state["last_submitted_question"] = cleaned_question
                    st.session_state["chat_history"].append(
                        {
                            "role": "user",
                            "content": cleaned_question,
                            "timestamp": now_str,
                        }
                    )
                    st.session_state["chat_history"].append(
                        {
                            "role": "assistant",
                            "content": err_msg,
                            "status": "error",
                            "timestamp": now_str,
                        }
                    )
                    st.rerun()

                except Exception as unk_err:
                    err_msg = f"An unexpected error occurred: {type(unk_err).__name__}"
                    st.session_state["last_error"] = err_msg
                    st.session_state["query_result"] = None
                    st.session_state["last_submitted_question"] = cleaned_question
                    st.session_state["chat_history"].append(
                        {
                            "role": "user",
                            "content": cleaned_question,
                            "timestamp": now_str,
                        }
                    )
                    st.session_state["chat_history"].append(
                        {
                            "role": "assistant",
                            "content": err_msg,
                            "status": "error",
                            "timestamp": now_str,
                        }
                    )
                    st.rerun()

    elif submit_clicked and not question_input.strip():
        st.warning("Please enter a question before submitting.")


def main():
    """Main application layout and workflow."""
    init_session_state()
    inject_custom_css()
    client = AnalyticsClient()

    render_sidebar(client)

    # Top App Header
    top_col1, top_col2 = st.columns([0.8, 0.2])
    with top_col1:
        st.markdown(
            '<div style="display: flex; align-items: center; gap: 0.65rem; margin-bottom: 0.25rem;">'
            '<span style="font-size: 1.5rem;">⚡</span>'
            '<span style="font-size: 1.4rem; font-weight: 700; color: #ffffff; letter-spacing: -0.02em;">'
            'AI SQL Analytics Copilot'
            '</span>'
            '<span class="badge badge-info" style="margin-left: 0.5rem;">Neon PostgreSQL</span>'
            '</div>'
            '<div style="font-size: 0.82rem; color: #94a3b8; margin-bottom: 1rem;">'
            'Enterprise natural language analytics powered by Gemini and automated Plotly intelligence'
            '</div>',
            unsafe_allow_html=True,
        )

    with top_col2:
        if st.session_state.get("copilot_collapsed", False):
            if st.button("💬 Open AI Copilot", key="btn_open_copilot", use_container_width=True):
                st.session_state["copilot_collapsed"] = False
                st.rerun()

    # Split Workspace Layout (~62% Left Analytics, ~38% Right Copilot)
    copilot_collapsed = st.session_state.get("copilot_collapsed", False)

    if copilot_collapsed:
        with st.container():
            render_analytics_results()
    else:
        col_analytics, col_copilot = st.columns([1.65, 1.0], gap="large")
        with col_analytics:
            render_analytics_results()
        with col_copilot:
            render_copilot_panel(client)


if __name__ == "__main__":
    main()

import streamlit as st
import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def render():
    st.title("🛡️ AWS-RESCUE — Overview")

    from dashboard.components.metrics import render_metric_cards
    from dashboard.components.charts import render_replication_timeline, render_drift_chart

    render_metric_cards()

    st.divider()

    col1, col2 = st.columns(2)
    with col1:
        render_replication_timeline()
    with col2:
        render_drift_chart()

    # Auto-refresh toggle
    st.divider()
    auto_refresh = st.toggle("Auto-refresh every 30 seconds", value=False)
    if auto_refresh:
        import time
        time.sleep(30)
        st.rerun()

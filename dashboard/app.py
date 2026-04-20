import os
import sys
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

st.set_page_config(
    page_title="AWS-RESCUE",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)

from infra.config import config

# Sidebar
with st.sidebar:
    st.title("🛡️ AWS-RESCUE")
    st.caption("Cross-region S3 replication monitor")
    st.divider()
    st.markdown("**Configuration**")
    st.markdown(f"- **Project ID:** `{config.project_id}`")
    st.markdown(f"- **Primary:** `{config.primary_bucket}`")
    st.markdown(f"  `{config.active_primary_region}`")
    st.markdown(f"- **Backup:** `{config.backup_bucket}`")
    st.markdown(f"  `{config.active_backup_region}`")
    st.markdown(f"- **DynamoDB:** `{config.dynamo_table}`")
    failover_state = "⚠️ FAILED OVER" if not config._primary_is_original else "✅ Normal"
    st.markdown(f"- **Failover State:** {failover_state}")
    st.divider()
    if st.button("🔄 Refresh Now"):
        st.rerun()

# Navigation via pages directory (Streamlit multi-page)
# Main landing page just shows a welcome + quick stats redirect
from dashboard.pages import _overview as ov

ov.render()

import os
import streamlit as st
import tempfile
import time
import threading
import pandas as pd
import gc
from scraper_engine import ScraperEngine

st.set_page_config(page_title="Literature Extractor", page_icon="📝", layout="wide")

# Authentication Check
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    st.title("🔒 Login Required")
    st.markdown("Please enter your credentials to access the Literature Extractor.")
    
    with st.form("login_form"):
        user_id = st.text_input("User ID")
        password = st.text_input("Password", type="password")
        submit_btn = st.form_submit_button("Log In")
        
        if submit_btn:
            if user_id == "ZS_HEOR" and password == "ZS_HEOR":
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("Invalid User ID or Password.")
    st.stop()

# Main Application starts here
st.title("Literature Extractor")

# User Instructions
with st.expander("ℹ️ User Instructions & Formats", expanded=False):
    st.markdown("""
    **What this tool does:**
    This tool automates the process of extracting full-text academic articles using SeleniumBase. 
    
    **Required Columns:**
    * **`DOI`**: The direct Document Object Identifier link.
    * **`Article Name`**: The full title of the paper.
    * **`Format Name`**: The desired filename for the downloaded PDF output.
    * **`Bing Link`**: (Optional) Custom search URL.
    """)

class AppState:
    def __init__(self):
        self.logs = []
        self.progress = 0.0
        self.is_scraping = False
        self.engine = None
        self.output_dir = None
        self.scraping_finished = False
        self.latest_screenshot = None

if "app_state" not in st.session_state:
    st.session_state.app_state = AppState()

state = st.session_state.app_state

def log_callback(msg_type, data):
    curr_time = time.strftime('%H:%M:%S')
    if msg_type == "log":
        state.logs.append(f"{curr_time} - {data}")
    elif msg_type == "error":
        state.logs.append(f"{curr_time} - ❌ ERROR: {data}")
    elif msg_type == "done":
        state.logs.append(f"{curr_time} - ✅ Scraping completed!")
        state.is_scraping = False
        state.scraping_finished = True
    elif msg_type == "screenshot":
        state.latest_screenshot = data

def progress_callback(progress):
    state.progress = progress

col1, col2 = st.columns([1, 2])

with col1:
    st.subheader("Configuration")
    max_workers = st.slider("Concurrent Browsers", min_value=1, max_value=5, value=1, help="Set to 1 if using HITL to keep window focus.")
    paywall_wait = st.slider("Paywall Wait (s)", min_value=5, max_value=60, value=15)
    
    # Updated Tabs
    tab1, tab2 = st.tabs(["Upload Excel", "Paste Data Grid"])
    
    tmp_path = None
    
    with tab1:
        uploaded_file = st.file_uploader("Upload Excel File", type=["xlsx", "xls"])
        if uploaded_file and not state.is_scraping:
            if st.button("Start Scraping", use_container_width=True, type="primary", key="btn_excel"):
                state.is_scraping = True
                with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
                    tmp.write(uploaded_file.getvalue())
                    tmp_path = tmp.name

    with tab2:
        st.markdown("**Click the top-left cell and press Ctrl+V to paste your data from Excel.**")
        
        # Initialize an empty dataframe with 15 rows for easy pasting
        if "paste_df" not in st.session_state:
            st.session_state.paste_df = pd.DataFrame(
                [["", "", "", ""] for _ in range(15)], 
                columns=["DOI", "Article Name", "Format Name", "Bing Link"]
            )
        
        # Interactive Data Editor
        edited_df = st.data_editor(
            st.session_state.paste_df,
            num_rows="dynamic", # Allows adding/deleting rows
            use_container_width=True,
            key="data_grid"
        )
        
        if not state.is_scraping:
            if st.button("Start Scraping Grid Data", use_container_width=True, type="primary", key="btn_grid"):
                # Clean up the dataframe (remove completely empty rows)
                # Replace empty strings with NaN to drop them properly
                clean_df = edited_df.replace(r'^\s*$', pd.NA, regex=True)
                clean_df = clean_df.dropna(subset=["Article Name", "Format Name"], how="all")
                
                if clean_df.empty:
                    st.error("Please paste or type at least one valid row with an Article Name and Format Name!")
                else:
                    state.is_scraping = True
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
                        clean_df.to_excel(tmp.name, index=False)
                        tmp_path = tmp.name

    # Trigger scraper engine
    if tmp_path and state.is_scraping:
        state.logs = []
        state.progress = 0.0
        state.scraping_finished = False
        state.latest_screenshot = None
        
        engine = ScraperEngine(tmp_path, log_callback, progress_callback, max_workers, paywall_wait)
        state.engine = engine
        state.output_dir = engine.output_dir
        
        thread = threading.Thread(target=engine.run)
        thread.start()
        st.rerun()
            
    if state.is_scraping:
        st.progress(state.progress)
        if st.button("Stop Scraping", use_container_width=True):
            if state.engine:
                state.engine.stop()
                st.warning("Stopping the scraper after current tasks finish...")
                
    if state.scraping_finished and state.output_dir and os.path.exists(state.output_dir):
        import shutil
        st.success("✅ Extraction complete!")
        zip_path = state.output_dir + ".zip"
        shutil.make_archive(zip_path.replace('.zip', ''), 'zip', state.output_dir)
        with open(zip_path, "rb") as f:
            st.download_button(
                label="⬇️ Download Extracted PDFs",
                data=f,
                file_name="Extracted_PDFs.zip",
                mime="application/zip",
                type="primary",
                use_container_width=True
            )

with col2:
    st.subheader("Logs")
    log_container = st.empty()
    if hasattr(state, 'latest_screenshot') and state.latest_screenshot:
        import base64
        try:
            st.image(base64.b64decode(state.latest_screenshot), caption="Live Browser Telemetry", use_container_width=True)
        except: pass

    if state.logs:
        log_container.code("\n".join(state.logs[-30:]), language="text")
    elif not state.is_scraping:
        st.info("Logs will appear here once scraping starts.")

# Polling loop for active extraction
if state.is_scraping:
    time.sleep(1)
    st.rerun()

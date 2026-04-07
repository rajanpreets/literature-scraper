import os
import streamlit as st
import tempfile
import time
import threading
import pandas as pd
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
    st.stop()  # Stop execution of the rest of the app if not authenticated

# Main Application starts here
st.title("Literature Extractor")

# User Instructions
with st.expander("ℹ️ User Instructions & Formats", expanded=False):
    st.markdown("""
    **What this tool does:**
    This tool automates the process of extracting full-text academic articles using SeleniumBase. It uses a pool of concurrent headless browsers to navigate directly to DOIs, hunt for PDF metadata, or search Bing for fallback links, dramatically speeding up bulk literature retrieval.
    
    **Required Excel Format:**
    Please uphold the following column names exactly in your uploaded `.xlsx` file:
    *   **`DOI`**: The direct Document Object Identifier link (e.g., `https://doi.org/10.1016/...`).
    *   **`Article Name`**: The full title of the paper. Used for fallback searches and conference abstract matching.
    *   **`Format Name`**: The desired filename for the downloaded PDF output (e.g., `Smith_2023_Analysis`).
    *   *(Optional)* **`Bing Link`**: A custom search URL if the DOI is known to be dead. (Auto-generated via article name if left blank).
    """)

# Use a custom class instance to hold state instead of mutating st.session_state directly.
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
    max_workers = st.slider("Concurrent Browsers", min_value=1, max_value=5, value=2, help="Number of Chrome instances to run in parallel.")
    paywall_wait = st.slider("Paywall Wait (s)", min_value=5, max_value=60, value=15,
                             help="Seconds to wait after a paywall is detected before querying the Unpaywall API.")
    
    tab1, tab2 = st.tabs(["Upload Excel", "Manual Entry"])
    
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
        doi_input = st.text_input("DOI", placeholder="https://doi.org/...")
        article_input = st.text_input("Article Name*", help="Used for fallback searching if DOI fails")
        format_input = st.text_input("Format Name*", placeholder="Smith_2024_Paper", help="Filename for the downloaded PDF")
        
        if not state.is_scraping:
            if st.button("Start Scraping", use_container_width=True, type="primary", key="btn_manual"):
                if not article_input or not format_input:
                    st.error("Article Name and Format Name are required!")
                else:
                    state.is_scraping = True
                    df = pd.DataFrame([{
                        "DOI": doi_input,
                        "Article Name": article_input,
                        "Format Name": format_input
                    }])
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
                        df.to_excel(tmp.name, index=False)
                        tmp_path = tmp.name

    # Trigger scraper engine if a file path was successfully prepared in either tab
    if tmp_path and state.is_scraping:
        state.logs = []
        state.progress = 0.0
        state.scraping_finished = False
        state.latest_screenshot = None
        
        engine = ScraperEngine(tmp_path, log_callback, progress_callback, max_workers, paywall_wait)
        state.engine = engine
        state.output_dir = engine.output_dir
        
        # Start the main scraper engine thread
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

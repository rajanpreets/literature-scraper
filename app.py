import os
import streamlit as st
import tempfile
import time
import threading
import pandas as pd
import gc
from scraper_engine import ScraperEngine

st.set_page_config(page_title="Literature Extractor (Autonomous)", page_icon="📝", layout="wide")

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

st.title("Literature Extractor (Autonomous Sniper)")

with st.expander("ℹ️ User Instructions", expanded=False):
    st.markdown("""
    **What this tool does:**
    This tool utilizes an **Autonomous Human Simulator**. It automatically clears cookies and scrolls to bypass Cloudflare. 
    If a native PDF isn't found, it uses **Smart Snippet Extraction** to find your specific abstract within a page and generates a clean PDF up to 1,000 words.
    """)

class AppState:
    def __init__(self):
        self.logs = []
        self.progress = 0.0
        self.is_scraping = False
        self.engine = None
        self.output_dir = None
        self.tracked_excel_path = None
        self.scraping_finished = False

if "app_state" not in st.session_state:
    st.session_state.app_state = AppState()

state = st.session_state.app_state

def log_callback(msg_type, data):
    curr_time = time.strftime('%H:%M:%S')
    if msg_type == "log":
        state.logs.append(f"{curr_time} - {data}")
        if len(state.logs) > 200: state.logs = state.logs[-200:]
    elif msg_type == "error":
        state.logs.append(f"{curr_time} - ❌ ERROR: {data}")
        if len(state.logs) > 200: state.logs = state.logs[-200:]
    elif msg_type == "done":
        state.logs.append(f"{curr_time} - ✅ Scraping completed!")
        state.is_scraping = False
        state.scraping_finished = True
        state.engine = None      
        gc.collect()             

def progress_callback(progress):
    state.progress = progress

col1, col2 = st.columns([1, 2])

with col1:
    st.subheader("Configuration")
    
    tab1, tab2 = st.tabs(["Upload Excel", "Paste Data Grid"])
    tmp_path = None
    
    with tab1:
        uploaded_file = st.file_uploader("Upload Excel File", type=["xlsx", "xls"])
        if uploaded_file and not state.is_scraping:
            if st.button("Start Autonomous Scraper", use_container_width=True, type="primary", key="btn_excel"):
                state.is_scraping = True
                with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
                    tmp.write(uploaded_file.getvalue())
                    tmp_path = tmp.name

    with tab2:
        st.markdown("**Click the top-left cell and press Ctrl+V to paste data from Excel.**")
        if "paste_df" not in st.session_state:
            st.session_state.paste_df = pd.DataFrame(
                [["", "", "", ""] for _ in range(15)], 
                columns=["DOI", "Article Name", "Format Name", "Bing Link"]
            )
        
        edited_df = st.data_editor(st.session_state.paste_df, num_rows="dynamic", use_container_width=True, key="data_grid")
        
        if not state.is_scraping:
            if st.button("Start Grid Scraper", use_container_width=True, type="primary", key="btn_grid"):
                clean_df = edited_df.replace(r'^\s*$', pd.NA, regex=True)
                clean_df = clean_df.dropna(subset=["Article Name", "Format Name"], how="all")
                
                if clean_df.empty:
                    st.error("Please paste or type at least one valid row!")
                else:
                    state.is_scraping = True
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
                        clean_df.to_excel(tmp.name, index=False)
                        tmp_path = tmp.name

    if tmp_path and state.is_scraping:
        state.logs = []
        state.progress = 0.0
        state.scraping_finished = False
        
        engine = ScraperEngine(tmp_path, log_callback, progress_callback)
        state.engine = engine
        state.output_dir = engine.output_dir
        state.tracked_excel_path = engine.tracked_excel_path
        
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
            st.download_button("⬇️ Download Extracted PDFs (ZIP)", data=f, file_name="Extracted_PDFs.zip", mime="application/zip", type="primary", use_container_width=True)
            
        if state.tracked_excel_path and os.path.exists(state.tracked_excel_path):
            with open(state.tracked_excel_path, "rb") as f:
                st.download_button("📊 Download Tracked Excel (Shows Content Types)", data=f, file_name="Tracked_Literature_Results.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", type="secondary", use_container_width=True)

with col2:
    st.subheader("Logs")
    log_container = st.empty()
    if state.logs:
        log_container.code("\n".join(state.logs[-30:]), language="text")
    elif not state.is_scraping:
        st.info("Logs will appear here once scraping starts.")

if state.is_scraping:
    time.sleep(1)
    st.rerun()

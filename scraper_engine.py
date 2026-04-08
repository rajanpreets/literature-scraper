import concurrent.futures
import re
import pandas as pd
import json
import os
import time
import base64
import requests
from urllib.parse import quote, urlparse
from seleniumbase import Driver
from thefuzz import fuzz
from fpdf import FPDF
import fitz  # PyMuPDF

# --- Linux / Streamlit Cloud Display Hack ---
if os.name != 'nt':
    try:
        from pyvirtualdisplay import Display
        vdisplay = Display(visible=0, size=(1920, 1080))
        vdisplay.start()
    except Exception as e: print(f"Virtual display warning: {e}")

# Linux friendly temporary paths for Streamlit Cloud
BASE_PROFILE_DIR = "/tmp/scraper_profiles"
os.makedirs(BASE_PROFILE_DIR, exist_ok=True)

STEALTH_SCRIPT = """
    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
    window.navigator.chrome = { runtime: {} };
    Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
"""

def sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "", str(name)).strip()

def sanitize_doi(doi_raw: str) -> str:
    if not doi_raw or str(doi_raw).strip().lower() in ('nan', 'none', ''): return ''
    doi = str(doi_raw).strip()
    doi = re.sub(r'^https?://(dx\.)?doi\.org/', '', doi, flags=re.IGNORECASE)
    doi = re.split(r'[?#]', doi)[0].strip().strip('/')
    return doi if doi.startswith('10.') else ''

class ScraperEngine:
    def __init__(self, excel_path, log_callback, progress_callback, max_workers=1, paywall_wait_seconds=15):
        self.excel_path = excel_path
        self.log_callback = log_callback
        self.progress_callback = progress_callback
        self.running = True
        
        # Streamlit safe output directory
        self.output_dir = "./scraped_pdfs"
        os.makedirs(self.output_dir, exist_ok=True)
        
        # CRITICAL FIX: Initialize tracked_excel_path for the frontend
        self.tracked_excel_path = None
        
        self.state_file = os.path.join(self.output_dir, "scraping_state.json")
        self.completed_dois = self.load_state()
        
        self.df = pd.read_excel(self.excel_path)
        if 'Action_Taken' not in self.df.columns: self.df['Action_Taken'] = ""
        if 'Extraction_Status' not in self.df.columns: self.df['Extraction_Status'] = ""

    def load_state(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r") as f: return set(json.load(f))
            except: pass
        return set()

    def save_state(self, doi):
        if not doi or str(doi) == 'nan': return
        self.completed_dois.add(doi)
        try:
            with open(self.state_file, "w") as f: json.dump(list(self.completed_dois), f)
        except: pass

    def log(self, msg_type, data): self.log_callback(msg_type, data)
    def stop(self): self.running = False

    def validate_pdf(self, pdf_path, article_name="", is_conference=False):
        if not os.path.exists(pdf_path): return False
        try:
            size_kb = os.path.getsize(pdf_path) / 1024
            if is_conference: return size_kb > 1
            if size_kb < 30: 
                self.log("error", f"❌ Junk PDF (Size {size_kb:.1f}KB). Deleting.")
                os.remove(pdf_path)
                return False
            doc = fitz.open(pdf_path)
            pages = doc.page_count
            doc.close()
            if pages <= 1:
                self.log("error", f"❌ Junk PDF (1 Page). Deleting.")
                os.remove(pdf_path)
                return False
            self.log("log", f"✅ PDF Validated: {pages} pages, {size_kb:.1f}KB.")
            return True
        except Exception:
            try: os.remove(pdf_path)
            except: pass
            return False

    def _build_cloud_driver(self):
        try:
            driver = Driver(
                browser="chrome",
                uc=True,            
                headless=True,      
                no_sandbox=True,    
                disable_dev_shm_usage=True, 
                user_data_dir=os.path.join(BASE_PROFILE_DIR, "session")
            )
            try: driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": STEALTH_SCRIPT})
            except: pass
            return driver
        except Exception as e:
            self.log("error", f"Failed to init Cloud Driver: {e}")
            return None

    def run(self):
        driver = None
        try:
            duplicate_dois = self.df[self.df.duplicated(subset=['DOI'], keep=False)]['DOI'].dropna().unique()
            self.log("log", "Booting Autonomous Cloud Engine...")
            driver = self._build_cloud_driver()
            
            if not self.running or not driver: return

            total_rows = len(self.df)
            
            for index, row in self.df.iterrows():
                if not self.running: break
                
                self.progress_callback((index + 1) / total_rows)
                doi = str(row.get('DOI', '')).strip()
                if doi and doi != "nan" and doi in self.completed_dois:
                    continue
                    
                action_taken = self.process_row_autonomous(driver, row, duplicate_dois)
                
                raw_format_name = str(row.get('Format Name', '')).strip()
                clean_format_name = sanitize_filename(raw_format_name)
                article_name = str(row.get('Article Name', '')).strip()

                target_path = os.path.join(self.output_dir, f"{clean_format_name}.pdf")
                extracted = self.validate_pdf(target_path, article_name, is_conference=False)
                
                if not extracted:
                    conf_path = os.path.join(self.output_dir, f"{clean_format_name}_conference.pdf")
                    if os.path.exists(conf_path): extracted = self.validate_pdf(conf_path, article_name, is_conference=True)

                if extracted: self.save_state(doi)

                self.df.at[index, 'Extraction_Status'] = "Success" if extracted else "Failed"
                self.df.at[index, 'Action_Taken'] = action_taken

                if index % 15 == 0 and index > 0:
                    try: driver.quit()
                    except: pass
                    driver = self._build_cloud_driver()
                    
            # CRITICAL FIX: Save the path to self.tracked_excel_path so app.py can access it
            self.tracked_excel_path = os.path.join(self.output_dir, "Tracked_" + os.path.basename(str(self.excel_path)))
            self.df.to_excel(self.tracked_excel_path, index=False)
            self.log("log", f"✅ Tracking Excel saved to: {self.tracked_excel_path}")

        finally:
            if driver:
                try: driver.quit()
                except: pass
            self.log_callback("done", None) 

    def process_row_autonomous(self, driver, row, duplicate_dois):
        doi = str(row['DOI']).strip()
        article_name = str(row['Article Name']).strip()
        format_name = sanitize_filename(str(row['Format Name']).strip())

        self.log("log", f"\n=== AUTO-FOCUS: {article_name} ===")

        if not doi or str(doi) == 'nan' or not doi.startswith('http'):
            return "Failed - Invalid DOI"

        try:
            driver.uc_open_with_reconnect(doi, reconnect_time=4)
            time.sleep(5)
            self.annihilate_overlays(driver)
        except: pass

        self.log("log", "Attempting Unpaywall API...")
        if self.route_paywall_api(driver, sanitize_doi(doi), format_name, article_name): 
            return "Unpaywall API Auto-Fetch"
        
        self.log("log", "Attempting Autonomous HTML Extraction...")
        if self.auto_find_and_download(driver, format_name):
            return "Autonomous HTML Extraction"

        return "Failed Automations"

    def annihilate_overlays(self, driver):
        js = """
        try {
            var kws = ['accept', 'got it', 'agree', 'verify'];
            var els = document.querySelectorAll('button, a');
            for(var i=0; i<els.length; i++) {
                if (kws.some(kw => els[i].innerText.toLowerCase().includes(kw))) { els[i].click(); }
            }
        } catch(e) {}
        """
        try: driver.execute_script(js)
        except: pass

    def auto_find_and_download(self, driver, format_name):
        candidate_urls = []
        try:
            for link in driver.find_elements(By.XPATH, "//a[contains(@href, '.pdf') or contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'download pdf')]"):
                href = link.get_attribute("href")
                if href and not href.startswith("javascript"): candidate_urls.insert(0, href)
        except: pass

        fetch_script = """
        var url = arguments[0]; var cb = arguments[1];
        fetch(url).then(r => {
            if (r.headers.get("content-type").includes("pdf")) {
                r.blob().then(b => {
                    var reader = new FileReader();
                    reader.onload = () => cb({s: true, d: reader.result});
                    reader.readAsDataURL(b);
                });
            } else { cb({s: false}); }
        }).catch(() => cb({s: false}));
        """
        
        for url in candidate_urls[:3]:
            try:
                driver.set_script_timeout(30)
                res = driver.execute_async_script(fetch_script, url)
                if res and res.get("s"):
                    b64 = res.get("d", "").split(",")[-1]
                    with open(os.path.join(self.output_dir, f"{format_name}.pdf"), 'wb') as f: 
                        f.write(base64.b64decode(b64))
                    return True
            except: pass
        return False

    def route_paywall_api(self, driver, doi, format_name, article_name):
        if not doi: return False
        with requests.Session() as session:
            session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
            try:
                for cookie in driver.get_cookies(): session.cookies.set(cookie["name"], cookie["value"], domain=cookie.get("domain", ""))
            except: pass
            try:
                resp = session.get(f"https://api.unpaywall.org/v2/{quote(doi, safe='/')}?email=scraper@example.com", timeout=15)
                if resp.status_code == 200:
                    data = resp.json()
                    locs = [data.get("best_oa_location")] + data.get("oa_locations", [])
                    for loc in filter(None, locs):
                        url = loc.get("url_for_pdf") or loc.get("url")
                        if not url: continue
                        try:
                            pdf_resp = session.get(url, timeout=30, stream=True)
                            if "application/pdf" in pdf_resp.headers.get("Content-Type", "").lower():
                                save_path = os.path.join(self.output_dir, f"{format_name}.pdf")
                                with open(save_path, 'wb') as f:
                                    for chunk in pdf_resp.iter_content(8192): f.write(chunk)
                                if self.validate_pdf(save_path, article_name): return True
                        except: continue
            except: pass
            return False

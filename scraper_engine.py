import queue
import re
import pandas as pd
import json
import os
import sys
import shutil
import subprocess
import time
import random
import base64
import requests
from urllib.parse import quote, urlparse
from seleniumbase import Driver
from thefuzz import fuzz
from fpdf import FPDF
import fitz  # PyMuPDF

if os.name != 'nt':
    try:
        import seleniumbase
        orig_sb = os.path.dirname(seleniumbase.__file__)
        tmp_sb = "/tmp/seleniumbase"
        if not os.path.exists(tmp_sb):
            shutil.copytree(orig_sb, tmp_sb)
            os.system(f"chmod -R 777 {tmp_sb}")
        for k in list(sys.modules.keys()):
            if k.startswith("seleniumbase"): del sys.modules[k]
        if "/tmp" not in sys.path: sys.path.insert(0, "/tmp")
        from pyvirtualdisplay import Display
        vdisplay = Display(visible=0, size=(1024, 768))
        vdisplay.start()
    except: pass

BASE_PROFILE_DIR = os.path.join(os.path.expanduser('~'), '.scraper_profiles')
EDGE_MASTER_PROFILE = os.path.join(BASE_PROFILE_DIR, 'master_profile')

EDGE_ARGS = [
    "--ignore-certificate-errors",
    "--allow-running-insecure-content",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-popup-blocking",
    "--disable-notifications",
    "--disable-blink-features=AutomationControlled",
    "--auth-server-whitelist='*'", 
    "--disable-features=IsolateOrigins,site-per-process", 
]

STEALTH_SCRIPT = """
    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
    window.navigator.chrome = { runtime: {} };
    Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
    Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
"""

class HumanSimulator:
    """Autonomous routines to spoof bot protections and clear overlays."""
    @staticmethod
    def human_delay(min_s=1.5, max_s=4.0):
        time.sleep(random.uniform(min_s, max_s))

    @staticmethod
    def organic_scroll(driver):
        try:
            total_height = driver.execute_script("return document.body.scrollHeight")
            viewport = driver.execute_script("return window.innerHeight")
            if total_height <= viewport: return
            current_pos = 0
            while current_pos < (total_height * 0.6): 
                step = random.randint(150, 400)
                current_pos += step
                driver.execute_script(f"window.scrollTo({{top: {current_pos}, behavior: 'smooth'}});")
                HumanSimulator.human_delay(0.5, 1.2)
        except: pass

    @staticmethod
    def annihilate_overlays(driver):
        js_destroyer = """
        try {
            var keywords = ['accept all', 'accept cookies', 'got it', 'i agree', 'verify', 'agree and continue', 'accept'];
            var elements = document.querySelectorAll('button, a, div[role="button"]');
            for(var i = 0; i < elements.length; i++) {
                if (keywords.some(kw => elements[i].innerText.toLowerCase().includes(kw))) { elements[i].click(); }
            }
            var divs = document.querySelectorAll('div');
            for(var j = 0; j < divs.length; j++) {
                var style = window.getComputedStyle(divs[j]);
                if ((style.position === 'fixed' || style.position === 'sticky') && parseInt(style.zIndex) > 900) {
                    if(divs[j].offsetHeight > window.innerHeight * 0.15) { divs[j].remove(); }
                }
            }
        } catch(e) {}
        """
        try: driver.execute_script(js_destroyer)
        except: pass

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
        self.max_workers = 1 # Force 1 for stability
        self.paywall_wait_seconds = paywall_wait_seconds
        self.running = True
        self.output_dir = os.path.join(os.path.expanduser('~'), "Downloads")
        os.makedirs(self.output_dir, exist_ok=True)
        os.makedirs(EDGE_MASTER_PROFILE, exist_ok=True)
        
        self.master_profile = EDGE_MASTER_PROFILE
        self.state_file = os.path.join(self.output_dir, "scraping_state.json")
        self.completed_dois = self.load_state()
        self.tracked_excel_path = os.path.join(self.output_dir, "Tracked_" + os.path.basename(self.excel_path))
        
        self.df = pd.read_excel(self.excel_path)
        if 'Extraction_Status' not in self.df.columns: self.df['Extraction_Status'] = ""
        if 'Content_Type' not in self.df.columns: self.df['Content_Type'] = ""

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

    def _build_edge_driver(self):
        return Driver(browser="edge", uc=False, headless=False, user_data_dir=self.master_profile, chromium_arg=" ".join(EDGE_ARGS), do_not_track=True)

    def initialize_driver(self):
        self.log("log", "Initializing Autonomous Engine...")
        profile_ready = os.path.exists(os.path.join(self.master_profile, "Default"))
        try:
            warmup = self._build_edge_driver()
            warmup.get("https://google.com")
            if not profile_ready:
                self.log("log", "[Action Required] Log into Zscaler/Azure AD. You have 90s.")
                time.sleep(90) 
            else: time.sleep(10)
            warmup.quit()
            time.sleep(3) 
            if os.name == 'nt': subprocess.call("TASKKILL /F /IM msedge.exe /T", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(2)
            
            driver = self._build_edge_driver()
            try: driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": STEALTH_SCRIPT})
            except: pass
            return driver
        except Exception as e: 
            self.log("error", f"Auth warmup failed: {e}")
            return None

    def run(self):
        driver = None
        try:
            driver = self.initialize_driver()
            if not self.running or not driver: return

            total_rows = len(self.df)
            for current_index in range(total_rows):
                if not self.running: break
                
                row = self.df.iloc[current_index]
                self.progress_callback((current_index + 1) / total_rows)
                
                if row.get('Extraction_Status') == "Success": continue
                    
                status, c_type = self.process_row_with_driver(driver, row)

                # Live Excel Update
                self.df.at[current_index, 'Extraction_Status'] = "Success" if status else "Failed"
                self.df.at[current_index, 'Content_Type'] = c_type
                self.df.to_excel(self.tracked_excel_path, index=False)

        finally:
            if driver:
                try: driver.quit()
                except: pass
            self.log_callback("done", None) 

    def process_row_with_driver(self, driver, row):
        doi = str(row['DOI']).strip()
        article_name = str(row['Article Name']).strip()
        format_name = sanitize_filename(str(row['Format Name']).strip())
        bing_link = str(row.get('Bing Link', '')).strip()

        self.log("log", f"\n=== FOCUS: {article_name} ===")

        target_url = doi if (doi and doi != 'nan' and doi.startswith('http')) else (bing_link if str(bing_link) != 'nan' else f"https://www.bing.com/search?q={article_name}")
        
        try:
            driver.get(target_url)
            HumanSimulator.human_delay(3.0, 5.0)
            HumanSimulator.annihilate_overlays(driver)
            HumanSimulator.organic_scroll(driver)
            HumanSimulator.annihilate_overlays(driver)
        except: pass

        # Route 1: Try Native JS PDF Extraction
        if self.save_pdf_from_browser(driver, format_name):
            self.save_state(sanitize_doi(doi))
            return True, "Full Native PDF"

        # Route 2: Smart Ctrl+A Extraction (1000 words)
        self.log("log", "Native PDF failed. Initiating Smart Content Match...")
        success, content_type = self.smart_extract_text(driver, article_name, format_name)
        if success:
            self.save_state(sanitize_doi(doi))
            return True, content_type

        # Route 3: Unpaywall API Fallback
        self.log("log", "Falling back to Unpaywall API...")
        if self.route_paywall_api(driver, sanitize_doi(doi), format_name):
            self.save_state(sanitize_doi(doi))
            return True, "Full Native PDF (Unpaywall)"
        
        return False, "Failed"

    def smart_extract_text(self, driver, article_name, format_name):
        """
        Acts like Ctrl+A. Uses Fuzzy Logic to find the abstract title.
        Grabs up to 1000 words following the title and saves to a clean PDF.
        """
        try:
            body_text = driver.execute_script("return document.body.innerText;")
            if not body_text or len(body_text.strip()) < 50:
                return False, "No Text Found"

            paragraphs = [p.strip() for p in body_text.split('\n') if len(p.strip()) > 10]
            total_words = len(" ".join(paragraphs).split())
            
            best_idx = -1
            best_score = 0
            
            for i, p in enumerate(paragraphs):
                score = fuzz.token_set_ratio(article_name, p)
                if score > best_score:
                    best_score = score
                    best_idx = i
                        
            # If we find a solid match for the article title
            if best_score > 65 and best_idx != -1:
                self.log("log", f"🎯 Match Found! Extracting up to 1000 words...")
                extracted_words = []
                word_count = 0
                
                for p in paragraphs[best_idx:]:
                    words = p.split()
                    if word_count + len(words) > 1000:
                        remaining = 1000 - word_count
                        extracted_words.extend(words[:remaining])
                        extracted_words.append("... [Text Truncated at 1000 words]")
                        break
                    else:
                        extracted_words.extend(words)
                        word_count += len(words)
                        
                final_text = " ".join(extracted_words)
                
                # Logic to determine if it's an abstract or a full snippet
                c_type = "Extracted Abstract" if total_words < 2500 else "Extracted Snippet (1000w)"
                
                self.generate_clean_pdf(final_text, article_name, format_name)
                return True, c_type
                
            self.log("log", "❌ Title not found in page text.")
            return False, "Title Mismatch"
        except Exception as e:
            self.log("error", f"Smart extract failed: {e}")
            return False, "Error"

    def generate_clean_pdf(self, text, article_name, format_name):
        """Uses FPDF to create a beautifully formatted text PDF."""
        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Arial", size=12)
        
        # Add Header
        pdf.set_font("Arial", style="B", size=14)
        safe_title = article_name.encode('latin-1', 'replace').decode('latin-1')
        pdf.multi_cell(0, 10, txt=f"Article: {safe_title}")
        pdf.ln(5)
        
        # Add Body
        pdf.set_font("Arial", size=11)
        safe_text = text.encode('latin-1', 'replace').decode('latin-1')
        pdf.multi_cell(0, 8, txt=safe_text)
        
        save_path = os.path.join(self.output_dir, f"{format_name}.pdf")
        pdf.output(save_path)
        self.log("log", f"✅ Clean Text PDF Generated: {format_name}.pdf")

    def save_pdf_from_browser(self, driver, format_name):
        candidate_urls = [driver.get_current_url()]
        try:
            for iframe in driver.find_elements(By.TAG_NAME, "iframe"):
                src = iframe.get_attribute("src")
                if src and ("pdf" in src.lower() or "download" in src.lower()): candidate_urls.insert(0, src)
        except: pass

        fetch_script = """
        var url = arguments[0]; var cb = arguments[1];
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 15000); 
        fetch(url, {credentials: 'include', signal: controller.signal}).then(r => {
            clearTimeout(timeoutId);
            if (r.headers.get("content-type").includes("pdf")) {
                r.blob().then(b => {
                    var reader = new FileReader();
                    reader.onload = () => cb({s: true, d: reader.result});
                    reader.readAsDataURL(b);
                });
            } else { cb({s: false}); }
        }).catch(() => { clearTimeout(timeoutId); cb({s: false}); });
        """
        try:
            driver.set_script_timeout(20)
            for test_url in candidate_urls:
                res = driver.execute_async_script(fetch_script, test_url)
                if res and res.get("s"):
                    b64_data = res.get("d", "").split(",")[-1]
                    save_path = os.path.join(self.output_dir, f"{format_name}.pdf")
                    with open(save_path, 'wb') as f: f.write(base64.b64decode(b64_data))
                    return True
        except: pass
        return False

    def route_paywall_api(self, driver, doi, format_name):
        if not doi: return False
        with requests.Session() as session:
            session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
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
                                return True
                        except: continue
            except: pass
            return False

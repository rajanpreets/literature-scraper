import concurrent.futures
import pandas as pd
import json
import os
import sys
import shutil
import time
import base64
import requests
import re
from urllib.parse import quote, urlparse
from thefuzz import fuzz
from fpdf import FPDF
import fitz  # PyMuPDF

# --- CRITICAL STREAMLIT CLOUD PERMISSION HACK ---
# Ensures Undetected Chromedriver can patch itself on locked Linux servers
if os.name != 'nt':
    try:
        import seleniumbase
        orig_sb = os.path.dirname(seleniumbase.__file__)
        tmp_sb = "/tmp/seleniumbase"
        
        if not os.path.exists(tmp_sb):
            shutil.copytree(orig_sb, tmp_sb)
            os.system(f"chmod -R 777 {tmp_sb}")
        
        for k in list(sys.modules.keys()):
            if k.startswith("seleniumbase"):
                del sys.modules[k]
        if "/tmp" not in sys.path:
            sys.path.insert(0, "/tmp")
            
        from pyvirtualdisplay import Display
        vdisplay = Display(visible=0, size=(1920, 1080))
        vdisplay.start()
    except Exception as e:
        print(f"Cloud init warning: {e}")

from seleniumbase import Driver
from selenium.webdriver.common.by import By

# Linux friendly temporary paths for Streamlit Cloud
BASE_PROFILE_DIR = "/tmp/scraper_profiles" if os.name != 'nt' else os.path.join(os.path.expanduser('~'), '.scraper_profiles')
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
    def __init__(self, excel_path, log_callback, progress_callback, max_workers=2, paywall_wait_seconds=15):
        self.excel_path = excel_path
        self.log_callback = log_callback
        self.progress_callback = progress_callback
        self.max_workers = max_workers 
        self.paywall_wait_seconds = paywall_wait_seconds
        self.running = True
        
        self.output_dir = "./scraped_pdfs" if os.name != 'nt' else os.path.join(os.path.dirname(excel_path), "extracted_literature")
        os.makedirs(self.output_dir, exist_ok=True)
        
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
            driver_kwargs = {
                "browser": "chrome",
                "uc": True,            
                "headless": True,      
                "no_sandbox": True,    
                "user_data_dir": os.path.join(BASE_PROFILE_DIR, "session")
            }
            if os.name != 'nt':
                driver_kwargs["chromium_arg"] = "--disable-dev-shm-usage"
                
            driver = Driver(**driver_kwargs)
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
            self.log("log", "Booting Fully Autonomous Engine...")
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

                # Memory cycle protection for continuous autonomous runs
                if index % 15 == 0 and index > 0:
                    try: driver.quit()
                    except: pass
                    driver = self._build_cloud_driver()
                    
            # Final Save
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
        bing_link = str(row.get('Bing Link', '')).strip()

        self.log("log", f"\n=== AUTO-FOCUS: {article_name} ===")

        if not doi or str(doi) == 'nan' or not doi.startswith('http'):
            return self.route_bing_autonomous(driver, article_name, bing_link, format_name)

        try:
            driver.uc_open_with_reconnect(doi, reconnect_time=4)
            time.sleep(5)
            self.annihilate_overlays(driver)
        except: pass

        self.log("log", "Attempting Autonomous HTML Extraction...")
        if self.auto_find_and_download(driver, format_name):
            return "Autonomous DOM Extraction"

        self.log("log", "Attempting Unpaywall API...")
        if self.route_paywall_api(driver, sanitize_doi(doi), format_name, article_name): 
            return "Unpaywall API Auto-Fetch"
        
        self.log("log", "Falling back to Bing Auto-Sniper...")
        return self.route_bing_autonomous(driver, article_name, bing_link, format_name)

    def annihilate_overlays(self, driver):
        """Silently destroys cookie banners that block autonomous clicking."""
        js = """
        try {
            var kws = ['accept', 'got it', 'agree', 'verify'];
            var els = document.querySelectorAll('button, a, div[role="button"]');
            for(var i=0; i<els.length; i++) {
                if (kws.some(kw => els[i].innerText.toLowerCase().includes(kw))) { els[i].click(); }
            }
        } catch(e) {}
        """
        try: driver.execute_script(js)
        except: pass

    def auto_find_and_download(self, driver, format_name):
        """Autonomously scans the page for PDF links and meta tags, and executes JS fetch."""
        candidate_urls = []
        
        # Strategy 1: Look for standard academic Meta Tags
        try:
            for meta in driver.find_elements(By.CSS_SELECTOR, "meta[name='citation_pdf_url']"):
                content = meta.get_attribute("content")
                if content: candidate_urls.append(content)
        except: pass

        # Strategy 2: Look for <iframe> embeds (often used for PDF viewers)
        try:
            for iframe in driver.find_elements(By.TAG_NAME, "iframe"):
                src = iframe.get_attribute("src")
                if src and ("pdf" in src.lower() or "download" in src.lower()): candidate_urls.append(src)
        except: pass

        # Strategy 3: Aggressive DOM XPath hunting for buttons/links
        try:
            xpaths = [
                "//a[contains(@href, '.pdf')]",
                "//a[@title='ePDF' or contains(@class, 'pdf-btn')]",
                "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'download pdf')]",
                "//a[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'pdf')]"
            ]
            for xp in xpaths:
                for link in driver.find_elements(By.XPATH, xp):
                    href = link.get_attribute("href")
                    if href and not href.startswith("javascript"): 
                        candidate_urls.append(href)
        except: pass

        if not candidate_urls:
            return False

        # Execute silent JS fetch on the best candidates
        fetch_script = """
        var url = arguments[0]; var cb = arguments[1];
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 20000); 
        
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
        
        for url in list(dict.fromkeys(candidate_urls))[:4]:  # Deduplicate and limit to top 4
            try:
                driver.set_script_timeout(25)
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

    def route_bing_autonomous(self, driver, article_name, bing_link, format_name):
        """Automatically searches Bing, navigates to the best match, and attempts extraction."""
        try:
            search_url = bing_link if bing_link and bing_link != 'nan' else f"https://www.bing.com/search?q={article_name}"
            driver.uc_open_with_reconnect(search_url, reconnect_time=3)
            time.sleep(3)
            self.annihilate_overlays(driver)
            
            results = driver.find_elements(By.CSS_SELECTOR, "li.b_algo h2 a")
            urls = [el.get_attribute("href") for el in results[:3]] # Check top 3 links
            
            for url in urls:
                if not self.running or not url: continue
                
                self.log("log", f"Checking Auto-Bing result: {url}")
                driver.get(url)
                time.sleep(3)
                self.annihilate_overlays(driver)
                
                try: body_text = driver.get_text("body")[:2000]
                except: body_text = ""
                
                # Check if the page is a match for the article we want
                if max(fuzz.token_set_ratio(article_name, driver.title), fuzz.token_set_ratio(article_name, body_text)) > 85:
                    if self.auto_find_and_download(driver, format_name):
                        return "Bing Auto-Extracted"
                        
            return "Failed - Bing Fallback Exhausted"
        except Exception as e:
            self.log("error", f"Bing Auto Error: {e}")
            return "Error in Bing Fallback"

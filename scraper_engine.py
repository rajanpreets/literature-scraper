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

# --- CRITICAL STREAMLIT CLOUD PERMISSION HACK ---
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
        vdisplay = Display(visible=0, size=(1280, 1024))
        vdisplay.start()
    except Exception as e:
        print("SB Hack failed:", e)

from seleniumbase import Driver
from selenium.webdriver.common.by import By
from thefuzz import fuzz
from fpdf import FPDF
import fitz  # PyMuPDF

# --- Global Configurations ---
STEALTH_SCRIPT = """
    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
    window.navigator.chrome = { runtime: {} };
    Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
    Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
"""

# 100% Web-Native HUD & Notepad (No Tkinter required)
HITL_HUD_SCRIPT = """
    if (!document.getElementById('scraper-hitl-hud')) {
        let hud = document.createElement('div');
        hud.id = 'scraper-hitl-hud';
        hud.style.cssText = "position:fixed; bottom:20px; right:20px; z-index:2147483647; background:rgba(15, 23, 42, 0.95); color:#e2e8f0; padding:20px; border-radius:12px; font-family:system-ui, sans-serif; box-shadow: 0 10px 25px rgba(0,0,0,0.5); border: 1px solid #334155; transition: opacity 0.2s;";
        hud.innerHTML = `
            <h3 style="margin:0 0 10px 0; color:#38bdf8; font-size:16px;">🤖 HITL Active</h3>
            <ul style="margin:0; padding-left:20px; font-size:14px; line-height:1.6;">
                <li><b>[Alt + Click]</b> : Intercept link</li>
                <li><b>[Ctrl + P]</b> : Clean PDF Print</li>
                <li><b>[Ctrl + M]</b> : Extract Abstract</li>
                <hr style="border-color:#334155; margin: 8px 0;">
                <li><b>[ ➔ ]</b> / <b>[ ⬅ ]</b> : Next / Prev</li>
                <li><b>[Ctrl + J]</b> : Jump to Format</li>
            </ul>
        `;
        document.body.appendChild(hud);
    }
    
    if (!document.getElementById('scraper-hitl-notepad')) {
        let notepad = document.createElement('div');
        notepad.id = 'scraper-hitl-notepad';
        notepad.style.cssText = "display:none; position:fixed; top:50%; left:50%; transform:translate(-50%, -50%); z-index:2147483647; background:#1e293b; color:#f8fafc; padding:20px; border-radius:12px; width:600px; box-shadow: 0 20px 40px rgba(0,0,0,0.7); border: 1px solid #475569;";
        notepad.innerHTML = `
            <h3 style="margin-top:0; color:#4ade80;">📝 Abstract Notepad</h3>
            <p style="font-size:13px; color:#94a3b8; margin-bottom:10px;">Edit the text below. When saved, it will be generated as a PDF.</p>
            <textarea id="hitl-notepad-text" style="width:100%; height:300px; background:#0f172a; color:#e2e8f0; border:1px solid #334155; padding:10px; font-family:inherit; border-radius:6px; resize:vertical;"></textarea>
            <div style="margin-top:15px; text-align:right;">
                <button id="hitl-notepad-cancel" style="background:#475569; color:white; border:none; padding:8px 16px; border-radius:6px; cursor:pointer; margin-right:10px;">Cancel</button>
                <button id="hitl-notepad-save" style="background:#4ade80; color:#0f172a; border:none; padding:8px 16px; border-radius:6px; cursor:pointer; font-weight:bold;">Save to PDF</button>
            </div>
        `;
        document.body.appendChild(notepad);
        
        document.getElementById('hitl-notepad-cancel').onclick = function() {
            document.getElementById('scraper-hitl-notepad').style.display = 'none';
        };
        document.getElementById('hitl-notepad-save').onclick = function() {
            let text = document.getElementById('hitl-notepad-text').value;
            window.__HITL_ACTION = {type: 'notepad_save', text: text};
            document.getElementById('scraper-hitl-notepad').style.display = 'none';
            document.getElementById('scraper-hitl-hud').innerHTML = "<h3 style='color:#4ade80; margin:0;'>✅ Saving Abstract...</h3>";
        };
    }

    window.__HITL_ACTION = null;

    document.addEventListener('keydown', function(e) {
        if (e.key === 'ArrowRight') {
            e.preventDefault();
            window.__HITL_ACTION = {type: 'next'};
            document.getElementById('scraper-hitl-hud').innerHTML = "<h3 style='color:#facc15; margin:0;'>⏭ Moving to Next...</h3>";
        }
        if (e.key === 'ArrowLeft') {
            e.preventDefault();
            window.__HITL_ACTION = {type: 'prev'};
            document.getElementById('scraper-hitl-hud').innerHTML = "<h3 style='color:#facc15; margin:0;'>⏮ Moving to Previous...</h3>";
        }
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'p') {
            e.preventDefault();
            window.__HITL_ACTION = {type: 'print'};
        }
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'm') {
            e.preventDefault();
            let suggested = "";
            let ps = document.querySelectorAll('p');
            for(let i=0; i<ps.length; i++) {
                if(ps[i].innerText.length > 150) { suggested = ps[i].innerText; break; }
            }
            document.getElementById('hitl-notepad-text').value = "Article Abstract:\\n\\n" + suggested;
            document.getElementById('scraper-hitl-notepad').style.display = 'block';
        }
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'j') {
            e.preventDefault();
            let target = prompt("Enter 'Format Name' to jump directly to it:");
            if(target) {
                window.__HITL_ACTION = {type: 'jump', target: target};
                document.getElementById('scraper-hitl-hud').innerHTML = "<h3 style='color:#38bdf8; margin:0;'>🔀 Jumping...</h3>";
            }
        }
    });

    document.addEventListener('click', function(e) {
        if (e.altKey) {
            let target = e.target.closest('a, button');
            if (target) {
                let href = target.getAttribute('href') || window.location.href;
                e.preventDefault();
                window.__HITL_ACTION = {type: 'download', url: href};
                document.getElementById('scraper-hitl-hud').innerHTML = "<h3 style='color:#a78bfa; margin:0;'>📥 Intercepting...</h3>";
            }
        }
    }, true);
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
        self.max_workers = 1  # Forced 1 for HITL stability
        self.paywall_wait_seconds = paywall_wait_seconds
        self.running = True
        
        self.output_dir = os.path.join(os.path.dirname(excel_path), "extracted_literature")
        os.makedirs(self.output_dir, exist_ok=True)
        
        self.tracked_excel_path = None
        self.state_file = os.path.join(self.output_dir, "scraping_state.json")
        self.completed_dois = self.load_state()
        
        # Load and Prep Excel
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

    def _build_driver(self):
        try:
            binary_location = None
            if os.path.exists('/usr/bin/chromium'): binary_location = '/usr/bin/chromium'
            elif os.path.exists('/usr/bin/chromium-browser'): binary_location = '/usr/bin/chromium-browser'
            
            driver = Driver(
                uc=True, 
                headless=False,
                binary_location=binary_location
            )
            try: driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": STEALTH_SCRIPT})
            except: pass
            return driver
        except Exception as e:
            self.log("error", f"Failed to init driver: {e}")
            return None

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

    def run(self):
        driver = None
        try:
            duplicate_dois = self.df[self.df.duplicated(subset=['DOI'], keep=False)]['DOI'].dropna().unique()
            self.log("log", "Initializing State-Machine HITL Engine...")
            driver = self._build_driver()
            
            if not self.running or not driver: return

            total_rows = len(self.df)
            current_index = 0
            
            while 0 <= current_index < total_rows:
                if not self.running: break
                
                row = self.df.iloc[current_index]
                self.progress_callback((current_index + 1) / total_rows)
                
                doi = str(row.get('DOI', '')).strip()
                if doi and doi != "nan" and doi in self.completed_dois:
                    current_index += 1
                    continue
                    
                action_result = self.process_row_with_driver(driver, current_index, row, duplicate_dois)
                
                # Handling Jump / Next / Previous dynamically
                if isinstance(action_result, dict) and action_result.get('type') == 'jump':
                    target_format = action_result.get('target', '').strip()
                    match = self.df[self.df['Format Name'].astype(str).str.contains(target_format, case=False, na=False)]
                    if not match.empty:
                        current_index = match.index[0]
                        self.log("log", f"🔀 Successfully Jumped to row {current_index + 1}: {target_format}")
                    else:
                        self.log("error", f"❌ Format Name '{target_format}' not found. Staying on current article.")
                elif action_result == "HITL: Previous":
                    current_index = max(0, current_index - 1)
                elif action_result == "HITL: Next":
                    current_index += 1
                else:
                    current_index += 1
                    
            # Final Clean Save for Streamlit
            self.tracked_excel_path = os.path.join(self.output_dir, "Tracked_" + os.path.basename(str(self.excel_path)))
            self.df.to_excel(self.tracked_excel_path, index=False)
            self.log("log", f"✅ Tracking Excel saved to: {self.tracked_excel_path}")

        finally:
            if driver:
                try: driver.quit()
                except: pass
            self.log_callback("done", None) 

    def process_row_with_driver(self, driver, index, row, duplicate_dois):
        action_taken = self.process_row(driver, row, duplicate_dois)
        
        raw_format_name = str(row.get('Format Name', '')).strip()
        clean_format_name = sanitize_filename(raw_format_name)
        article_name = str(row.get('Article Name', '')).strip()

        target_path = os.path.join(self.output_dir, f"{clean_format_name}.pdf")
        extracted = self.validate_pdf(target_path, article_name, is_conference=False)
        
        if not extracted:
            conf_path = os.path.join(self.output_dir, f"{clean_format_name}_conference.pdf")
            if os.path.exists(conf_path): extracted = self.validate_pdf(conf_path, article_name, is_conference=True)

        if extracted: self.save_state(str(row.get('DOI', '')).strip())

        # Live update of the Pandas frame
        self.df.at[index, 'Extraction_Status'] = "Success" if extracted else "Failed"
        if isinstance(action_taken, str):
            self.df.at[index, 'Action_Taken'] = action_taken
        else:
            self.df.at[index, 'Action_Taken'] = "Jump Navigated"
            
        return action_taken

    def process_row(self, driver, row, duplicate_dois):
        doi = str(row['DOI']).strip()
        article_name = str(row['Article Name']).strip()
        format_name = sanitize_filename(str(row['Format Name']).strip())
        bing_link = str(row.get('Bing Link', '')).strip()

        self.log("log", f"\n=== FOCUS: {article_name} ===")

        if not doi or str(doi) == 'nan' or not doi.startswith('http'):
            return self.route_bing_hitl(driver, article_name, bing_link, format_name)

        try:
            if hasattr(driver, "uc_open_with_reconnect"):
                driver.uc_open_with_reconnect(doi, reconnect_time=4)
            else:
                driver.get(doi)
            time.sleep(4)
            self.annihilate_overlays(driver)
            if "error" in driver.get_current_url().lower():
                return self.route_bing_hitl(driver, article_name, bing_link, format_name)
        except: pass

        human_action = self.wait_for_human(driver, format_name, article_name)
        if human_action: return human_action

        self.log("log", "Falling back to Unpaywall API...")
        if self.route_paywall_api(driver, sanitize_doi(doi), format_name, article_name): 
            return "Unpaywall API Auto-Fetch"
        
        self.log("log", "Falling back to Bing Search...")
        return self.route_bing_hitl(driver, article_name, bing_link, format_name)

    def wait_for_human(self, driver, format_name, article_name):
        try:
            driver.execute_script("window.__HITL_ACTION = null;")
            driver.execute_script(HITL_HUD_SCRIPT)
            self.log("log", f"⏳ Awaiting Human Action for: {article_name}")
            
            start = time.time()
            while time.time() - start < 300: # 5 Minute hard timeout
                if not self.running: return "Aborted by User"
                
                action = driver.execute_script("return window.__HITL_ACTION;")
                if action:
                    action_type = action.get('type')
                    
                    if action_type == 'next':
                        self.log("log", "⏭ Human requested Next.")
                        return "HITL: Next"
                    elif action_type == 'prev':
                        self.log("log", "⏮ Human requested Previous.")
                        return "HITL: Previous"
                    elif action_type == 'jump':
                        return {"type": "jump", "target": action.get('target')}
                    elif action_type == 'print':
                        self.log("log", "🖨 Human requested Print. Generating clean PDF...")
                        self.execute_print_to_pdf(driver, format_name)
                        return "HITL: Ctrl+P Print"
                    elif action_type == 'notepad_save':
                        text = action.get('text', '')
                        self.log("log", "📝 Saving abstract from JS Notepad...")
                        pdf = FPDF()
                        pdf.add_page()
                        pdf.set_font("Arial", size=12)
                        safe_text = text.encode('latin-1', 'replace').decode('latin-1')
                        pdf.multi_cell(0, 10, txt=safe_text)
                        save_path = os.path.join(self.output_dir, f"{format_name}_conference.pdf")
                        pdf.output(save_path)
                        self.log("log", f"✅ Abstract saved to: {format_name}_conference.pdf")
                        return "HITL: Notepad Manual Extract"
                    elif action_type == 'download':
                        url = action.get('url')
                        self.log("log", f"📥 Intercepting Final Link: {url}")
                        if self.execute_js_fetch(driver, url, format_name, article_name): 
                            return "HITL: Alt+Click Intercept"
                        else:
                            driver.get(url)
                            time.sleep(4)
                            self.execute_print_to_pdf(driver, format_name)
                            return "HITL: Link Navigated + Printed"
                            
                time.sleep(0.5)
            
            self.log("log", "⏳ Human input timed out. Moving to fallback.")
            return False 
        except Exception as e:
            self.log("error", f"HITL Error: {e}")
            return "Error"

    def route_bing_hitl(self, driver, article_name, bing_link, format_name):
        try:
            driver.get(bing_link if str(bing_link) != 'nan' else f"https://www.bing.com/search?q={article_name}")
            time.sleep(3)
            self.annihilate_overlays(driver)
            
            self.log("log", "Bing opened. Awaiting Human Action...")
            action = self.wait_for_human(driver, format_name, article_name)
            
            return action if action else "Skipped after Bing Timeout"
        except: return "Bing Error"

    def execute_print_to_pdf(self, driver, format_name):
        try:
            # Hide the HUD instantly before printing
            driver.execute_script("var h = document.getElementById('scraper-hitl-hud'); if(h) h.style.opacity = '0';")
            time.sleep(0.5)
            
            pdf_data = driver.execute_cdp_cmd("Page.printToPDF", {
                "landscape": False, "displayHeaderFooter": False, "printBackground": True, "preferCSSPageSize": True
            })
            
            # Bring the HUD back
            driver.execute_script("var h = document.getElementById('scraper-hitl-hud'); if(h) h.style.opacity = '1';")
            
            pdf_bytes = base64.b64decode(pdf_data['data'])
            save_path = os.path.join(self.output_dir, f"{format_name}.pdf")
            with open(save_path, "wb") as f: f.write(pdf_bytes)
            self.log("log", f"✅ Successfully saved Clean Print-to-PDF: {format_name}.pdf")
            return True
        except Exception as e:
            self.log("error", f"Print-to-PDF failed: {e}")
            return False

    def execute_js_fetch(self, driver, target_url, format_name, article_name):
        fetch_script = """
        var url = arguments[0]; var cb = arguments[1];
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 25000); 
        
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
            driver.set_script_timeout(30)
            res = driver.execute_async_script(fetch_script, target_url)
            if res and res.get("s"):
                b64_data = res.get("d", "").split(",")[-1]
                save_path = os.path.join(self.output_dir, f"{format_name}.pdf")
                with open(save_path, 'wb') as f: f.write(base64.b64decode(b64_data))
                if self.validate_pdf(save_path, article_name): 
                    self.log("log", f"✅ File auto-intercepted and saved: {format_name}.pdf")
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

import concurrent.futures
import queue
import pandas as pd
import json
import os
import sys
import shutil

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
            
        # Start virtual display manually instead of passing xvfb=True to Driver()
        from pyvirtualdisplay import Display
        vdisplay = Display(visible=0, size=(1280, 1024))
        vdisplay.start()
    except Exception as e:
        print("SB Hack failed:", e)
import time
import base64
from urllib.parse import urlparse
from seleniumbase import Driver
from selenium.webdriver.common.by import By
from thefuzz import fuzz
from fpdf import FPDF
import fitz  # PyMuPDF

# Keywords that strongly indicate a paywall page
PAYWALL_KEYWORDS = [
    "access this article", "purchase article", "buy article",
    "subscribe to access", "institutional login", "sign in to access",
    "full text access", "get access", "rent or buy",
    "pay-per-view", "purchase access", "access options",
    "subscribe or purchase", "paywall", "not have access",
    "unlock the full article", "article purchase",
]

class ScraperEngine:
    def __init__(self, excel_path, log_callback, progress_callback, max_workers=3, paywall_wait_seconds=15):
        self.excel_path = excel_path
        self.log_callback = log_callback
        self.progress_callback = progress_callback
        self.max_workers = max_workers
        self.paywall_wait_seconds = paywall_wait_seconds
        self.running = True
        self.output_dir = os.path.join(os.path.dirname(excel_path), "extracted_literature")
        os.makedirs(self.output_dir, exist_ok=True)
        self.rules = self.load_rules()
        self.driver_pool = queue.Queue()

    def log(self, msg_type, data):
        self.log_callback(msg_type, data)

    def load_rules(self):
        rules_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "journal_rules.json")
        try:
            with open(rules_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            self.log("error", f"Failed to load journal rules: {e}. Using default.")
            return {"default": {"pdf_meta_tag": "citation_pdf_url", "button_xpath": "//button[contains(., 'Download PDF')]", "timeout": 20}}

    def stop(self):
        self.running = False

    def initialize_drivers(self):
        self.log("log", f"Initializing {self.max_workers} browser instances sequentially...")
        for i in range(self.max_workers):
            if not self.running:
                break
            try:
                binary_location = None
                if os.path.exists('/usr/bin/chromium'):
                    binary_location = '/usr/bin/chromium'
                elif os.path.exists('/usr/bin/chromium-browser'):
                    binary_location = '/usr/bin/chromium-browser'
                
                # Use headless=False + explicit virtual display on Linux to defeat Cloudflare
                is_linux = (os.name != 'nt')
                
                driver = Driver(
                    uc=True, 
                    headless=False,
                    binary_location=binary_location
                )
                self.driver_pool.put(driver)
                self.log("log", f"Browser {i+1} initialized.")
            except Exception as e:
                self.log("error", f"Failed to init browser {i+1}: {e}")

    def cleanup_drivers(self):
        self.log("log", "Cleaning up browser instances...")
        close_count = 0
        while not self.driver_pool.empty():
            driver = self.driver_pool.get()
            try:
                driver.quit()
                close_count += 1
            except:
                pass
        self.log("log", f"Cleaned up {close_count} browsers.")

    def run(self):
        try:
            self.log("log", "Reading Excel file...")
            df = pd.read_excel(self.excel_path)
            
            required_cols = ['DOI', 'Article Name', 'Format Name']
            for col in required_cols:
                if col not in df.columns:
                    self.log("error", f"Missing required column: {col}")
                    return

            duplicate_dois = df[df.duplicated(subset=['DOI'], keep=False)]['DOI'].dropna().unique()
            self.log("log", f"Found {len(duplicate_dois)} duplicate DOIs for Conference process.")

            self.initialize_drivers()
            if not self.running:
                self.cleanup_drivers()
                return

            total_rows = len(df)
            completed = 0

            with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = []
                for index, row in df.iterrows():
                    futures.append(executor.submit(self.process_row_with_driver, row, duplicate_dois))

                for future in concurrent.futures.as_completed(futures):
                    if not self.running:
                        self.log("log", "Process stopped by user.")
                        break
                    try:
                        future.result()
                    except Exception as e:
                        self.log("error", f"Thread error: {e}")
                    
                    completed += 1
                    progress = completed / total_rows
                    self.progress_callback(progress)

            self.log("done", None)
        except Exception as e:
            self.log("error", f"Fatal error: {str(e)}")
        finally:
            self.cleanup_drivers()
            self.log_callback("done", None) # Trigger the UI to know it definitively finished

    def process_row_with_driver(self, row, duplicate_dois):
        if not self.running:
            return
            
        driver = self.driver_pool.get()
        try:
            self.process_row(driver, row, duplicate_dois)
        finally:
            self.driver_pool.put(driver)

    def process_row(self, driver, row, duplicate_dois):
        doi = str(row['DOI']).strip()
        article_name = str(row['Article Name']).strip()
        format_name = str(row['Format Name']).strip()
        bing_link = str(row.get('Bing Link', '')).strip()

        is_conference = doi in duplicate_dois
        
        self.log("log", f"Processing: {article_name}")

        if not doi or doi == 'nan' or not doi.startswith('http'):
            self.log("log", f"Invalid/Missing DOI for {article_name}. Attempting Route 4 (Bing).")
            if self.route_4_bing_fallback(driver, article_name, bing_link, format_name):
                return
            self.log("log", f"Failed to extract {article_name} via Bing.")
            return

        try:
            if hasattr(driver, "uc_open_with_reconnect"):
                driver.uc_open_with_reconnect(doi, reconnect_time=3)
            else:
                driver.get(doi)
            time.sleep(4)
            
            page_text = driver.get_page_source().lower()
            if "error" in driver.get_current_url().lower() or "not found" in page_text:
                 self.log("log", f"DOI appears dead for {article_name}. Attempting Route 4 (Bing).")
                 if self.route_4_bing_fallback(driver, article_name, bing_link, format_name):
                     return
                 self.log("log", f"Failed to extract {article_name} via Bing.")
                 return
                 
        except Exception as e:
            self.log("log", f"Failed to navigate to DOI: {e}. Attempting Bing.")
            self.route_4_bing_fallback(driver, article_name, bing_link, format_name)
            return

        current_url = driver.get_current_url()
        domain = urlparse(current_url).netloc.replace("www.", "")
        
        self.log("log", f"Redirected to domain: {domain} for {article_name}")

        if is_conference:
            self.log("log", f"Executing Route 3 (Conference/Duplicate DOI) for {article_name}.")
            if self.route_3_conference(driver, article_name, format_name):
                return

        rule = self.rules.get("default", {})
        for k in self.rules.keys():
            if k in domain and k != "default":
                rule = self.rules[k]
                break

        # Check for paywall BEFORE attempting expensive browser extraction
        raw_doi = doi.replace("https://doi.org/", "").replace("http://doi.org/", "").strip()
        if self.is_paywall_page(driver):
            wait = rule.get("paywall_wait", self.paywall_wait_seconds)
            self.log("log", f"⏳ PAYWALL detected for '{article_name}'. Waiting {wait}s for session redirect...")
            self.log("screenshot", driver.get_screenshot_as_base64())
            time.sleep(wait)
            # If wait resolved it (e.g. institutional redirect), try again
            if not self.is_paywall_page(driver):
                self.log("log", f"✅ Paywall resolved after wait for '{article_name}'. Retrying Route 1...")
                if self.execute_route_1_and_2(driver, rule, format_name, article_name):
                    return
            # Still paywalled — go straight to Unpaywall API
            self.log("log", f"🔓 Paywall still active. Querying Unpaywall REST API for '{article_name}'...")
            if self.route_paywall_api(driver, raw_doi, format_name, article_name):
                return
            self.log("log", f"All paywall bypass routes exhausted for '{article_name}'.")
            return
        
        success = self.execute_route_1_and_2(driver, rule, format_name, article_name)
        if not success:
            self.log("log", f"Browser extraction failed. Trying Unpaywall API for {article_name}...")
            if not self.route_paywall_api(driver, raw_doi, format_name, article_name):
                self.log("log", f"All extraction routes failed for {article_name}.")

    def is_paywall_page(self, driver):
        """Return True if the current browser page appears to be behind a paywall."""
        try:
            page_text = (driver.get_page_source() or "").lower()
            return any(kw in page_text for kw in PAYWALL_KEYWORDS)
        except:
            return False

    def execute_route_1_and_2(self, driver, rule, format_name, article_name):
        timeout = rule.get("timeout", 15)
        pdf_meta = rule.get("pdf_meta_tag", "citation_pdf_url")
        xpath_btn = rule.get("button_xpath", "")

        try:
            driver.implicitly_wait(min(timeout, 5))
            meta_element = driver.find_elements(By.CSS_SELECTOR, f"meta[name='{pdf_meta}']")
            if meta_element:
                pdf_url = meta_element[0].get_attribute("content")
                if pdf_url:
                    self.log("log", f"Found PDF via meta tag for {article_name}. Navigating...")
                    driver.get(pdf_url)
                    time.sleep(5)
                    self.log("screenshot", driver.get_screenshot_as_base64())
                    try:
                        if driver.is_element_present("iframe[src*='cloudflare']"):
                            self.log("log", "Cloudflare intercepted. Auto-clicking CAPTCHA...")
                            driver.uc_gui_click_captcha()
                            time.sleep(6)
                            self.log("screenshot", driver.get_screenshot_as_base64())
                    except: pass
                    # Check for paywall on the PDF page itself
                    if self.is_paywall_page(driver):
                        wait = rule.get("paywall_wait", self.paywall_wait_seconds)
                        self.log("log", f"⏳ PDF page also paywalled for {article_name}. Waiting {wait}s...")
                        time.sleep(wait)
                    if self.save_pdf_from_browser(driver, format_name, xpath_btn):
                        return True
            
            if xpath_btn:
                try:
                    if driver.is_element_present(xpath_btn):
                        self.log("log", f"Found PDF button for {article_name}. Clicking...")
                        btn_href = driver.find_element(By.XPATH, xpath_btn).get_attribute("href")
                        if btn_href: driver.get(btn_href)
                        else: driver.click(xpath_btn)
                        time.sleep(5)
                        self.log("screenshot", driver.get_screenshot_as_base64())
                        try:
                            if driver.is_element_present("iframe[src*='cloudflare']"):
                                driver.uc_gui_click_captcha()
                                time.sleep(6)
                                self.log("screenshot", driver.get_screenshot_as_base64())
                        except: pass
                        if self.save_pdf_from_browser(driver, format_name, xpath_btn):
                            return True
                except:
                    pass
                
        except Exception as e:
            self.log("log", f"Route 1 exception for {article_name}: {e}")
        finally:
            driver.implicitly_wait(10)

        # CAPTCHA retry: if current page has "Just a moment" or CF challenge, click and retry
        try:
            page_title = driver.title.lower()
            if "just a moment" in page_title or "cloudflare" in page_title or "security" in page_title:
                self.log("log", f"Cloudflare challenge page detected for {article_name}. Auto-clicking...")
                try:
                    driver.uc_gui_click_captcha()
                    time.sleep(8)
                    self.log("screenshot", driver.get_screenshot_as_base64())
                except Exception as cf_e:
                    self.log("log", f"CAPTCHA click error: {cf_e}")
                xpath_btn = rule.get("button_xpath", "") if isinstance(rule, dict) else ""
                if self.save_pdf_from_browser(driver, format_name, xpath_btn):
                    return True
        except: pass

        self.log("log", f"Route 1 failed for {article_name}.")
        return False

    def route_2_print_abstract(self, driver, format_name, article_name="article"):
        try:
            pdf_data = driver.execute_cdp_cmd("Page.printToPDF", {
                "landscape": False,
                "displayHeaderFooter": False,
                "printBackground": True,
                "preferCSSPageSize": True
            })
            
            pdf_bytes = base64.b64decode(pdf_data['data'])
            save_path = os.path.join(self.output_dir, f"{format_name}.pdf")
            
            with open(save_path, "wb") as f:
                f.write(pdf_bytes)
                
            self.log("log", f"Saved abstract to: {format_name}.pdf")
            self.count_pdf_pages(save_path)
            return True
        except Exception as e:
            self.log("log", f"Route 2 error for {article_name}: {e}")
            return False

    def count_pdf_pages(self, pdf_path):
        try:
            doc = fitz.open(pdf_path)
            self.log("log", f"PDF saved with {doc.page_count} pages.")
            doc.close()
        except:
            pass

    def route_3_conference(self, driver, article_name, format_name):
        try:
            paragraphs = driver.find_elements(By.CSS_SELECTOR, "p")
            best_match = None
            best_score = 0
            
            for p in paragraphs:
                text = p.text.strip()
                if not text: continue
                score = fuzz.token_set_ratio(article_name, text)
                if score > best_score:
                    best_score = score
                    best_match = text
                    
            if best_score > 90 and best_match:
                self.log("log", f"Found high-confidence conference abstract (Score: {best_score}) for {article_name}.")
                pdf = FPDF()
                pdf.add_page()
                pdf.set_font("Arial", size=12)
                safe_text = best_match.encode('latin-1', 'replace').decode('latin-1')
                pdf.multi_cell(0, 10, txt=safe_text)
                
                save_path = os.path.join(self.output_dir, f"{format_name}_conference.pdf")
                pdf.output(save_path)
                self.log("log", f"Saved conference abstract: {format_name}_conference.pdf")
                return True
            else:
                self.log("log", f"No matching abstract found > 90% (Best: {best_score}) for {article_name}.")
                return False
        except Exception as e:
            self.log("log", f"Route 3 error for {article_name}: {e}")
            return False


    def route_paywall_api(self, driver, doi, format_name, article_name):
        """
        Query the Unpaywall REST API (https://api.unpaywall.org/v2/{doi}?email=) to find a
        free, legal Open Access PDF. Browser cookies are forwarded so institutional session
        PDFs also work. Falls back to printing the abstract page if nothing is found.
        """
        import requests

        # ── 1. Build a requests session that carries the browser's cookies ──────────
        session = requests.Session()
        session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; LiteratureScraper/1.0; mailto:scraper@example.com)"})
        try:
            for cookie in driver.get_cookies():
                session.cookies.set(cookie["name"], cookie["value"],
                                    domain=cookie.get("domain", ""))
        except Exception as ce:
            self.log("log", f"Could not transfer browser cookies: {ce}")

        # ── 2. Query Unpaywall REST API ───────────────────────────────────────────
        unpaywall_email = "scraper@example.com"  # required by Unpaywall ToS
        api_url = f"https://api.unpaywall.org/v2/{doi}?email={unpaywall_email}"
        self.log("log", f"📡 Querying Unpaywall REST API for DOI: {doi}")

        try:
            resp = session.get(api_url, timeout=12)
            if resp.status_code == 200:
                data = resp.json()
                is_oa = data.get("is_oa", False)
                self.log("log", f"Unpaywall: is_oa={is_oa}, title='{data.get('title', '')}' for {article_name}")

                # Collect candidate PDF URLs: best_oa_location first, then remaining oa_locations
                locations = []
                best = data.get("best_oa_location")
                if best:
                    locations.append(best)
                for loc in data.get("oa_locations", []):
                    if loc not in locations:
                        locations.append(loc)

                for loc in locations:
                    pdf_url = loc.get("url_for_pdf") or loc.get("url")
                    if not pdf_url:
                        continue

                    host_type = loc.get("host_type", "unknown")
                    version = loc.get("version", "unknown")
                    self.log("log", f"Unpaywall OA candidate ({host_type}/{version}): {pdf_url}")

                    try:
                        # First try a direct requests download (fast, no browser overhead)
                        pdf_resp = session.get(pdf_url, timeout=35, stream=True)
                        content_type = pdf_resp.headers.get("Content-Type", "").lower()

                        if "application/pdf" in content_type:
                            save_path = os.path.join(self.output_dir, f"{format_name}.pdf")
                            with open(save_path, 'wb') as f:
                                for chunk in pdf_resp.iter_content(chunk_size=8192):
                                    if chunk:
                                        f.write(chunk)
                            self.log("log", f"✅ Unpaywall PDF saved directly: {format_name}.pdf")
                            self.count_pdf_pages(save_path)
                            return True
                        else:
                            # Content-type is HTML/redirect — navigate browser and try JS fetch
                            self.log("log", f"Not a direct PDF ({content_type}). Trying via browser for {article_name}...")
                            driver.get(pdf_url)
                            time.sleep(5)
                            if self.is_paywall_page(driver):
                                self.log("log", f"OA URL also paywalled. Skipping: {pdf_url}")
                                continue
                            if self.save_pdf_from_browser(driver, format_name, ""):
                                return True

                    except Exception as loc_e:
                        self.log("log", f"Failed OA URL {pdf_url}: {loc_e}")
                        continue

            elif resp.status_code == 404:
                self.log("log", f"Unpaywall: DOI not found ({doi})")
            else:
                self.log("log", f"Unpaywall returned HTTP {resp.status_code} for {doi}")

        except Exception as e:
            self.log("log", f"Unpaywall API error: {e}")

        # ── 3. Final fallback: print the current browser page as an abstract PDF ──
        self.log("log", f"No OA PDF found. Printing current page as abstract for '{article_name}'.")
        return self.route_2_print_abstract(driver, format_name, article_name)

    def route_unpaywall(self, driver, doi, format_name, article_name):
        """Legacy alias — delegates to route_paywall_api."""
        return self.route_paywall_api(driver, doi, format_name, article_name)

    def route_4_bing_fallback(self, driver, article_name, bing_link, format_name):
        self.log("log", f"Executing Route 4 for: {article_name}")
        try:
            search_url = bing_link if bing_link and bing_link != 'nan' else f"https://www.bing.com/search?q={article_name}"
            if hasattr(driver, "uc_open_with_reconnect"):
                driver.uc_open_with_reconnect(search_url, reconnect_time=2)
            else:
                driver.get(search_url)
            time.sleep(3)
            
            results = driver.find_elements(By.CSS_SELECTOR, "li.b_algo h2 a")
            urls = [el.get_attribute("href") for el in results[:10]]
            
            for url in urls:
                if not self.running: break
                if not url: continue
                
                self.log("log", f"Checking Bing result: {url}")
                driver.get(url)
                time.sleep(3)
                
                page_title = driver.title
                page_body = driver.get_text("body")[:5000]
                
                title_score = fuzz.token_set_ratio(article_name, page_title)
                body_score = fuzz.token_set_ratio(article_name, page_body)
                
                if max(title_score, body_score) > 90:
                    self.log("log", f"High match found in Bing results for {article_name}. Proceeding with Route 1 & 2.")
                    domain = urlparse(url).netloc.replace("www.", "")
                    rule = self.rules.get(domain, self.rules.get("default", {}))
                    return self.execute_route_1_and_2(driver, rule, format_name, article_name)
                    
            self.log("log", f"No matching results found in top 10 Bing URLs for {article_name}.")
            return False
        except Exception as e:
             self.log("log", f"Route 4 error for {article_name}: {e}")
             return False

    def save_pdf_from_browser(self, driver, format_name, xpath_btn=""):
        self.log("log", f"Finding and extracting raw PDF bytes...")
        self.log("screenshot", driver.get_screenshot_as_base64())
        current_url = driver.get_current_url()
        candidate_urls = [current_url]
        
        if xpath_btn:
            try:
                for el in driver.find_elements(By.XPATH, xpath_btn):
                    href = el.get_attribute("href")
                    if href and href not in candidate_urls: candidate_urls.insert(0, href)
            except: pass
                
        try:
            for iframe in driver.find_elements(By.TAG_NAME, "iframe"):
                src = iframe.get_attribute("src")
                if src and ("pdf" in src.lower() or "download" in src.lower()) and src not in candidate_urls:
                    candidate_urls.insert(0, src)
        except: pass
            
        try:
            for link in driver.find_elements(By.XPATH, "//a[contains(@href, '.pdf') or @title='ePDF' or contains(@class, 'pdf-btn') or contains(@class, 'coolBar-download') or contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'download pdf')]"):
                href = link.get_attribute("href")
                if href and href not in candidate_urls and not href.startswith("javascript"): candidate_urls.insert(0, href)
        except: pass
        
        try:
            if "onlinelibrary.wiley.com/doi/pdf/" in current_url: candidate_urls.insert(0, current_url.replace("/doi/pdf/", "/doi/pdfdirect/").split("?")[0] + "?download=true")
            elif "/doi/epdf/" in current_url: candidate_urls.insert(0, current_url.replace("/doi/epdf/", "/doi/pdf/").split("?")[0] + "?download=true")
            elif "literatumonline.com/doi/pdf/" in current_url: candidate_urls.insert(0, current_url.replace("/doi/pdf/", "/doi/pdfdirect/").split("?")[0] + "?download=true")
        except: pass

        fetch_script = """
        var url = arguments[0];
        var callback = arguments[1];
        fetch(url, {credentials: 'include'})
            .then(response => {
                const ct = response.headers.get("content-type") || "";
                if (ct.includes("pdf")) {
                    return response.blob().then(blob => {
                        var reader = new FileReader();
                        reader.onload = function() {
                            callback({success: true, data: reader.result, type: ct});
                        };
                        reader.readAsDataURL(blob);
                    });
                } else {
                    callback({success: false, error: "Not a PDF: " + ct});
                }
            })
            .catch(error => {
                callback({success: false, error: error.message});
            });
        """
        
        try:
            driver.set_script_timeout(30)
            for test_url in candidate_urls:
                try:
                    self.log("log", f"Testing candidate securely via Browser JS: {test_url}")
                    result = driver.execute_async_script(fetch_script, test_url)
                    if result and result.get("success"):
                        b64_data = result.get("data", "")
                        if "," in b64_data: b64_data = b64_data.split(",")[1]
                        
                        import base64
                        pdf_bytes = base64.b64decode(b64_data)
                        save_path = os.path.join(self.output_dir, f"{format_name}.pdf")
                        with open(save_path, 'wb') as f: f.write(pdf_bytes)
                                
                        self.log("log", f"✅ Original PDF elegantly saved via Browser JS: {format_name}.pdf")
                        self.count_pdf_pages(save_path)
                        return True
                except Exception as loop_e:
                    self.log("log", f"JS Fetch failed for {test_url}: {str(loop_e)}")
        except Exception as e: self.log("log", f"PDF download logic error: {e}")
            
        self.log("log", f"No raw PDF stream found natively. Falling back to print.")
        self.route_2_print_abstract(driver, format_name)
        return False

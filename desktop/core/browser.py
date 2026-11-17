import re
from urllib.parse import quote_plus, urljoin, urlparse

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
except ImportError:
    sync_playwright = None
    PlaywrightTimeoutError = Exception


class BrowserAgent:
    """Real browser automation using Playwright; degrades cleanly if Playwright is unavailable."""
    def __init__(self):
        import config
        self.headless = bool(getattr(config, "BROWSER_HEADLESS", False))
        self.channel = str(getattr(config, "BROWSER_CHANNEL", "chrome") or "").strip() or None
        self.timeout_ms = int(getattr(config, "BROWSER_TIMEOUT_MS", 20000))
        self.available = sync_playwright is not None

    def _launch(self, p):
        kwargs = {"headless": self.headless}
        if self.channel:
            try:
                return p.chromium.launch(channel=self.channel, **kwargs)
            except Exception:
                pass
        return p.chromium.launch(**kwargs)

    @staticmethod
    def _clean(text, limit=12000):
        text = re.sub(r"\n{3,}", "\n\n", text or "")
        return text.strip()[:limit]

    def _check(self):
        if not self.available:
            raise RuntimeError("Playwright kurulu değil. INSTALL.bat çalıştırılmalı.")

    def visit(self, url, extract=True):
        self._check()
        if not url.startswith(("http://", "https://")):
            raise ValueError("Yalnızca http/https adresleri kullanılabilir.")
        with sync_playwright() as p:
            browser = self._launch(p)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
                try:
                    page.wait_for_load_state("networkidle", timeout=5000)
                except PlaywrightTimeoutError:
                    pass
                title = page.title()
                body = self._clean(page.locator("body").inner_text() if extract else "")
                return {"url": page.url, "title": title, "text": body}
            finally:
                browser.close()

    def search(self, query, max_results=8):
        self._check()
        search_url = "https://html.duckduckgo.com/html/?q=" + quote_plus(query)
        with sync_playwright() as p:
            browser = self._launch(p)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            try:
                page.goto(search_url, wait_until="domcontentloaded", timeout=self.timeout_ms)
                links = page.locator("a.result__a")
                count = min(links.count(), max_results)
                results = []
                for i in range(count):
                    a = links.nth(i)
                    href = a.get_attribute("href") or ""
                    title = self._clean(a.inner_text(), 300)
                    parent = a.locator("xpath=..")
                    snippet = ""
                    try:
                        snippet = self._clean(parent.locator("xpath=following-sibling::*[1]").inner_text(), 700)
                    except Exception:
                        pass
                    results.append({
                        "title": title,
                        "url": urljoin(search_url, href),
                        "host": urlparse(urljoin(search_url, href)).netloc,
                        "snippet": snippet,
                    })
                return {"query": query, "results": results}
            finally:
                browser.close()

    def click(self, url, text=None, selector=None):
        self._check()
        if not url.startswith(("http://", "https://")):
            raise ValueError("Yalnızca http/https adresleri kullanılabilir.")
        with sync_playwright() as p:
            browser = self._launch(p)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
                if selector:
                    page.locator(selector).first.click(timeout=self.timeout_ms)
                elif text:
                    page.get_by_text(text, exact=False).first.click(timeout=self.timeout_ms)
                else:
                    raise ValueError("text veya selector gerekli.")
                page.wait_for_timeout(700)
                return {"url": page.url, "title": page.title(), "text": self._clean(page.locator("body").inner_text())}
            finally:
                browser.close()

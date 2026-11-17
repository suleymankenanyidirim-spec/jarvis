from urllib.parse import urlparse

try:
    from duckduckgo_search import DDGS
except ImportError:
    DDGS = None

class WebResearcher:
    HINTS = (
        "internetten", "webden", "web'den", "araştır", "güncel",
        "bugün", "şu an", "şuan", "son dakika", "haber", "fiyat",
        "en son", "latest", "current",
    )

    def __init__(self, max_results=6):
        self.max_results = max_results
        self.available = DDGS is not None

    def should_search(self, text):
        low = text.lower()
        return any(h in low for h in self.HINTS)

    def search(self, query):
        if not self.available:
            return [], "duckduckgo-search kurulu değil."
        try:
            with DDGS(timeout=10) as ddgs:
                results = list(ddgs.text(
                    query,
                    region="tr-tr",
                    safesearch="moderate",
                    max_results=self.max_results,
                ))
        except Exception as exc:
            return [], str(exc)

        cleaned = []
        for item in results:
            url = item.get("href") or item.get("url") or ""
            cleaned.append({
                "title": item.get("title", "").strip(),
                "body": item.get("body", "").strip(),
                "url": url,
                "host": urlparse(url).netloc,
            })
        return cleaned, None

    def context(self, results):
        if not results:
            return "Web sonucu bulunamadı."
        chunks = []
        for i, item in enumerate(results, 1):
            chunks.append(
                f"[KAYNAK {i}]\n"
                f"Başlık: {item['title']}\n"
                f"Alan adı: {item['host']}\n"
                f"URL: {item['url']}\n"
                f"Özet: {item['body']}\n"
            )
        return "\n".join(chunks)

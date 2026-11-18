"""HUD yazı kutusu (v2.8) — pygame'den bağımsız, test edilebilir düzenleme mantığı.

Harfler pygame'in TEXTINPUT olayından gelir (Türkçe ğ ş ı İ ve AltGr ile yazılan @ € { doğru gelsin);
tuşlar (Enter, Backspace, oklar, Ctrl+V…) KEYDOWN'dan. Önceki mesajlar ↑ / ↓ ile geri çağrılır.
"""

MAX_CHARS = 2000
MAX_HISTORY = 50


class TextBox:
    def __init__(self):
        self.text = ""
        self.cursor = 0
        self.history = []
        self._hist_i = None
        self._draft = ""

    # ------------------------------------------------------------ düzenleme
    def clear(self):
        self.text, self.cursor, self._hist_i, self._draft = "", 0, None, ""

    def insert(self, s):
        s = "".join(ch for ch in str(s or "").replace("\r\n", " ").replace("\n", " ").replace("\t", " ")
                    if ch.isprintable())
        room = MAX_CHARS - len(self.text)
        if not s or room <= 0:
            return
        s = s[:room]
        self.text = self.text[:self.cursor] + s + self.text[self.cursor:]
        self.cursor += len(s)

    def backspace(self, word=False):
        if self.cursor == 0:
            return
        start = self._word_start(self.cursor) if word else self.cursor - 1
        self.text = self.text[:start] + self.text[self.cursor:]
        self.cursor = start

    def delete(self):
        self.text = self.text[:self.cursor] + self.text[self.cursor + 1:]

    def _word_start(self, i):
        while i > 0 and self.text[i - 1] == " ":
            i -= 1
        while i > 0 and self.text[i - 1] != " ":
            i -= 1
        return i

    def left(self, word=False):
        self.cursor = self._word_start(self.cursor) if word else max(0, self.cursor - 1)

    def right(self, word=False):
        if not word:
            self.cursor = min(len(self.text), self.cursor + 1)
            return
        i = self.cursor
        while i < len(self.text) and self.text[i] == " ":
            i += 1
        while i < len(self.text) and self.text[i] != " ":
            i += 1
        self.cursor = i

    def home(self):
        self.cursor = 0

    def end(self):
        self.cursor = len(self.text)

    # ------------------------------------------------------------ geçmiş
    def history_prev(self):
        if not self.history:
            return
        if self._hist_i is None:
            self._draft = self.text
            self._hist_i = len(self.history)
        self._hist_i = max(0, self._hist_i - 1)
        self.text = self.history[self._hist_i]
        self.cursor = len(self.text)

    def history_next(self):
        if self._hist_i is None:
            return
        self._hist_i += 1
        if self._hist_i >= len(self.history):
            self.text, self._hist_i = self._draft, None
        else:
            self.text = self.history[self._hist_i]
        self.cursor = len(self.text)

    def submit(self):
        """Metni döndürür, geçmişe ekler ve kutuyu temizler. Boşsa ''."""
        text = self.text.strip()
        if text and (not self.history or self.history[-1] != text):
            self.history.append(text)
            del self.history[:-MAX_HISTORY]
        self.clear()
        return text

    # ------------------------------------------------------------ görünüm
    def view(self, fits, caret="|"):
        """Ekrana sığan parça (imleç görünür kalır). fits(metin) -> bool."""
        text = self.text[:self.cursor] + caret + self.text[self.cursor:]
        start, end = 0, len(text)
        c = self.cursor
        while start < c and not fits(text[start:end]):
            start += 1
        while end > c + len(caret) and not fits(text[start:end]):
            end -= 1
        return text[start:end]

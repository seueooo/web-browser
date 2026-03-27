import tkinter
from browser import HttpClient, decode_body
from layout import WIDTH, HEIGHT, HSTEP, VSTEP, SCROLL_STEP, lex, layout as do_layout


class Browser:
    def __init__(self):
        self.window = tkinter.Tk()
        self.window.title("SimpleBrowser")

        self.canvas = tkinter.Canvas(
            self.window,
            width=WIDTH,
            height=HEIGHT,
            bg="white",
        )
        self.canvas.pack(fill="both", expand=True)

        self._client = HttpClient()
        self.display_list: list[tuple[float, float, str]] = []
        self.scroll = 0
        self.width = WIDTH
        self.height = HEIGHT
        self._text = ""

        self._bind_events()

    def _bind_events(self):
        self.window.bind("<Down>", self.scrolldown)
        self.window.bind("<Up>", self.scrollup)
        self.window.bind("<MouseWheel>", self.on_mousewheel)
        self.window.bind("<Button-4>", self.on_mousewheel)   # Linux scroll up
        self.window.bind("<Button-5>", self.on_mousewheel)   # Linux scroll down
        self.window.bind("<Configure>", self.on_resize)

    def load(self, url: str):
        if url == "about:blank":
            body = ""
        else:
            try:
                _, headers, body_bytes = self._client.request(url)
                body = decode_body(body_bytes, headers)
            except Exception:
                body = ""

        self._text = lex(body)
        self.scroll = 0
        self._relayout()
        self.draw()

    def _relayout(self):
        self.display_list = do_layout(self._text, width=self.width)

    def draw(self):
        self.canvas.delete("all")

        for x, y, char in self.display_list:
            screen_y = y - self.scroll
            if screen_y + VSTEP < 0:
                continue
            if screen_y > self.height:
                continue
            self.canvas.create_text(x, screen_y, text=char, anchor="nw")

        self._draw_scrollbar()

    def _draw_scrollbar(self):
        if not self.display_list:
            return

        page_height = max(y for _, y, _ in self.display_list) + SCROLL_STEP
        if page_height <= self.height:
            return

        bar_x0 = self.width - 8
        bar_x1 = self.width
        ratio_top = self.scroll / page_height
        ratio_bot = (self.scroll + self.height) / page_height
        bar_y0 = ratio_top * self.height
        bar_y1 = ratio_bot * self.height

        self.canvas.create_rectangle(
            bar_x0, bar_y0, bar_x1, bar_y1,
            fill="blue", outline="blue",
        )

    def _max_scroll(self) -> int:
        if not self.display_list:
            return 0
        page_height = max(y for _, y, _ in self.display_list) + SCROLL_STEP
        return max(0, page_height - self.height)

    def scrolldown(self, event=None):
        self.scroll = min(self.scroll + SCROLL_STEP, self._max_scroll())
        self.draw()

    def scrollup(self, event=None):
        self.scroll = max(0, self.scroll - SCROLL_STEP)
        self.draw()

    def on_mousewheel(self, event):
        if getattr(event, "num", None) == 4 or getattr(event, "delta", 0) > 0:
            self.scrollup()
        else:
            self.scrolldown()

    def on_resize(self, event):
        if event.widget == self.window:
            self.width = event.width
            self.height = event.height
            self._relayout()
            self.draw()

    def run(self):
        self.window.mainloop()

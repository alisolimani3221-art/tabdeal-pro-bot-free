# -*- coding: utf-8 -*-
"""Standalone Persian candle-viewer demo. No exchange access or trading code."""
from kivy.app import App
from kivy.core.window import Window
from kivy.core.text import LabelBase
from kivy.graphics import Color, Line
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.widget import Widget

try:
    import arabic_reshaper
    from bidi.algorithm import get_display
except Exception:
    arabic_reshaper = None
    get_display = None

FONT = "assets/NotoNaskhArabic-Regular.ttf"
SAMPLE = [
    ("12:00", 100.0, 103.0, 99.0, 102.0, 1200),
    ("12:01", 102.0, 104.0, 101.0, 103.0, 980),
    ("12:02", 103.0, 103.5, 100.5, 101.0, 1100),
    ("12:03", 101.0, 105.0, 100.0, 104.0, 1430),
    ("12:04", 104.0, 106.0, 103.0, 105.0, 870),
    ("12:05", 105.0, 105.5, 102.0, 103.0, 920),
]

def fa(value):
    value = str(value)
    if arabic_reshaper and get_display:
        try:
            return get_display(arabic_reshaper.reshape(value), base_dir="R")
        except Exception:
            return value
    return value

def make_label(value, size=14, color=(0.90, 0.93, 0.98, 1), height=38, bold=False):
    return Label(
        text=fa(value), font_name=FONT, font_size=size, color=color,
        size_hint_y=None, height=height, halign="right", valign="middle",
        text_size=(Window.width * 0.88, None), bold=bold,
    )

class CandleChart(Widget):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.size_hint_y = None
        self.height = 190
        self.bind(pos=self.redraw, size=self.redraw)

    def redraw(self, *_):
        self.canvas.clear()
        if not self.width or not self.height:
            return
        values = SAMPLE
        low = min(x[3] for x in values)
        high = max(x[2] for x in values)
        span = max(high - low, 1)
        pad_x, pad_y = 28, 18
        usable_h = self.height - 2 * pad_y
        step = (self.width - 2 * pad_x) / len(values)
        with self.canvas:
            Color(0.25, 0.31, 0.40, 1)
            for j in range(4):
                y = pad_y + j * usable_h / 3
                Line(points=[self.x + pad_x, self.y + y,
                             self.right - pad_x, self.y + y], width=0.6)
            for i, candle in enumerate(values):
                _, opn, top, bottom, close, _ = candle
                x = self.x + pad_x + step * (i + 0.5)
                y_low = self.y + pad_y + (bottom - low) / span * usable_h
                y_high = self.y + pad_y + (top - low) / span * usable_h
                y_open = self.y + pad_y + (opn - low) / span * usable_h
                y_close = self.y + pad_y + (close - low) / span * usable_h
                rising = close >= opn
                if rising:
                    Color(0.20, 0.75, 0.52, 1)
                else:
                    Color(0.92, 0.35, 0.38, 1)
                Line(points=[x, y_low, x, y_high], width=1.4)
                body_h = max(abs(y_close - y_open), 3)
                Line(rectangle=(x - step * 0.22, min(y_open, y_close),
                                step * 0.44, body_h), width=3.0)

class CandleDemoApp(App):
    title = "Candle Viewer Demo"

    def build(self):
        Window.clearcolor = (0.055, 0.075, 0.11, 1)
        try:
            LabelBase.register(name="PersianDemo", fn_regular=FONT)
        except Exception:
            pass
        root = BoxLayout(orientation="vertical", padding=14, spacing=8)
        root.add_widget(make_label("نمایشگر آزمایشی کندل‌ها", 21, height=48, bold=True))
        root.add_widget(make_label("نسخه نمایشی مستقل | بدون اتصال به صرافی", 13,
                                   color=(1, 0.72, 0.35, 1), height=32))
        root.add_widget(make_label("این داده‌ها کاملاً نمونه‌ای هستند و قیمت واقعی نیستند.",
                                   12, color=(0.95, 0.68, 0.35, 1), height=34))
        root.add_widget(make_label("نمودار شمعی نمونه - بازه یک دقیقه", 15, height=34))
        root.add_widget(CandleChart())
        root.add_widget(make_label("جدول کندل‌ها (OHLCV)", 16, height=36, bold=True))

        scroller = ScrollView()
        table = GridLayout(cols=6, size_hint_y=None, spacing=3, padding=2)
        table.bind(minimum_height=table.setter("height"))
        headers = ["زمان", "باز", "بیشینه", "کمینه", "بسته", "حجم"]
        for item in headers:
            table.add_widget(make_label(item, 11, color=(0.55, 0.78, 1, 1), height=34))
        for row in SAMPLE:
            for value in row:
                table.add_widget(make_label(value, 11, height=34))
        scroller.add_widget(table)
        root.add_widget(scroller)
        root.add_widget(make_label(
            "توجه: این برنامه فقط برای آزمایش رابط و نمایش ساختار کندل است. "
            "هیچ API، حساب کاربری، سیگنال یا سفارش معاملاتی ندارد.",
            11, color=(0.72, 0.78, 0.86, 1), height=62))
        return root

if __name__ == "__main__":
    CandleDemoApp().run()

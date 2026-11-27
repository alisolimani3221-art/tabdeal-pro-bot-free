import os
import sys
import json
import re
import math
import threading
import time
import requests
import certifi
from android_tls import (mount_tabdeal_tls_compat,
                         tabdeal_websocket_ssl_options)

APP_VERSION = "1.56"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VENDOR_DIR = os.path.join(BASE_DIR, "vendor")
if os.path.isdir(VENDOR_DIR) and VENDOR_DIR not in sys.path:
    sys.path.insert(0, VENDOR_DIR)

try:
    import arabic_reshaper
    from bidi.algorithm import get_display
except Exception:
    arabic_reshaper = None
    get_display = None

from kivy.app import App
from kivy.clock import Clock
from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget
from kivy.graphics import Color, Line
from kivy.uix.spinner import Spinner, SpinnerOption
from kivy.core.window import Window
from kivy.core.clipboard import Clipboard
from kivy.core.text import LabelBase
from kivy.resources import resource_add_path
# Compare market identifiers by normalized BASEUSDT pair, while retaining
# the exact exchange-native spelling for REST requests and UI display.
from market_symbols import (select_market_symbol, list_market_symbols,
                            normalize_market_symbol)
from orderbook_poller import fetch_order_book_snapshot
from risk_analysis import analyze_klines, calculate_position_risk, recommend_leverage
from market_format import format_market_value
from market_candles import TradeCandleBuilder
from candle_history import (load_candle_cache, save_candle_cache,
                            validate_closed_candles, archive_candle_cache)
from candle_archive import (record_closed_candles, count_closed_candles,
                            load_latest_contiguous_candles)
from paper_trader import (PaperTradingEngine, diagnose_signal,
                          prepare_live_market_snapshot, paper_scan_due,
                          reconcile_paper_market_tick, PAPER_SCAN_INTERVAL_SECONDS,
                          summarize_closed_trades)
from trade_stream_probe import (
    BROADCAST_URL, parse_trade_message, is_trade_timestamp_fresh,
)
from openai_assistant import analyze_with_openai
from ai_diagnostics import (build_ai_diagnostic_context,
                             build_engine_health_report_lines)
from trade_stream_watchdog import TradeStreamWatchdog, reconnect_backoff_seconds
from app_runtime_guard import AppRuntimeGuard
from trade_coordinator_session import TradeCoordinatorSession
from paper_runner_gate import ensure_paper_runner_ready, PaperRunnerGateError
from tabdeal_account import read_futures_account
from secure_credentials import CredentialStore
from multi_market_scan import scan_market_universe
from multi_symbol_stream import (
    select_broadcast_symbols, include_required_symbol,
    subscribe_broadcast_symbols,
)
from multi_symbol_paper_coordinator import MultiSymbolPaperCoordinator
from multi_timeframe import analyze_multi_timeframe

# Resolve the bundled font from both the app directory and Kivy resources.
# Resolve bundled Arabic/Persian font robustly. Prefer Noto Sans Arabic, which
# covers Persian letters and Latin digits in one face; also check Android's
# system Noto font locations as a last-resort fallback.
FONT_CANDIDATES = [
    # Preserve the proven v1.15 rendering path: use the unified Noto Sans
    # Arabic + Latin face first, with Naskh only as a fallback.
    os.path.join(BASE_DIR, "NotoSansArabic-Regular.ttf"),
    os.path.join(BASE_DIR, "assets", "NotoSansArabic-Regular.ttf"),
    os.path.join(BASE_DIR, "NotoNaskhArabic-Regular.ttf"),
    os.path.join(BASE_DIR, "assets", "NotoNaskhArabic-Regular.ttf"),
    "/system/fonts/NotoSansArabic-Regular.ttf",
    "/system/fonts/NotoNaskhArabicUI-Regular.ttf",
    "/system/fonts/NotoNaskhArabic-Regular.ttf",
]
FONT_PATH = next((p for p in FONT_CANDIDATES if os.path.isfile(p)), None)
FONT_LOAD_ERROR = None
if FONT_PATH:
    try:
        resource_add_path(os.path.dirname(FONT_PATH))
        LabelBase.register(
            name="PersianNoto",
            fn_regular=FONT_PATH,
            fn_bold=FONT_PATH,
            fn_italic=FONT_PATH,
            fn_bolditalic=FONT_PATH,
        )
        # Kivy widgets such as Popup titles, Spinner dropdown rows and some
        # Buttons use the built-in "Roboto" default unless font_name is set.
        # Register the bundled Arabic-capable Noto face under that default
        # name too, so Persian glyphs never fall back to tofu squares.
        LabelBase.register(
            name="Roboto",
            fn_regular=FONT_PATH,
            fn_bold=FONT_PATH,
            fn_italic=FONT_PATH,
            fn_bolditalic=FONT_PATH,
        )
        FONT_NAME = "PersianNoto"
    except Exception as exc:
        FONT_LOAD_ERROR = "{}: {}".format(type(exc).__name__, str(exc))
        FONT_NAME = "Roboto"
else:
    FONT_LOAD_ERROR = "No bundled or Android system Noto Arabic font was found."
    FONT_NAME = "Roboto"

# Use a separate Noto Sans Latin face for ASCII-only content (tickers,
# English diagnostics, prices and numeric inputs). Persian/mixed RTL text keeps
# the Arabic-capable face to avoid glyph fallback inconsistencies on Android.
LATIN_FONT_CANDIDATES = [
    os.path.join(BASE_DIR, "NotoSans-Regular.ttf"),
    os.path.join(BASE_DIR, "assets", "NotoSans-Regular.ttf"),
    "/system/fonts/Roboto-Regular.ttf",
]
LATIN_FONT_PATH = next((p for p in LATIN_FONT_CANDIDATES if os.path.isfile(p)), None)
if LATIN_FONT_PATH:
    try:
        resource_add_path(os.path.dirname(LATIN_FONT_PATH))
        LabelBase.register(
            name="LatinNoto",
            fn_regular=LATIN_FONT_PATH,
            fn_bold=LATIN_FONT_PATH,
            fn_italic=LATIN_FONT_PATH,
            fn_bolditalic=LATIN_FONT_PATH,
        )
        NUMERIC_FONT_NAME = "LatinNoto"
    except Exception:
        NUMERIC_FONT_NAME = "Roboto"
else:
    NUMERIC_FONT_NAME = "Roboto"


# Android/Kivy can render the same screen with different fallback fonts when
# Latin and Arabic labels use separate font faces. That inconsistency can
# produce tofu/square glyphs in tickers, digits, Spinner rows and popup controls.
# Use the verified bundled Arabic-capable Noto face for EVERY widget; these
# Noto fonts include the ASCII letters, digits and punctuation used in BTC_USDT.
NUMERIC_FONT_NAME = FONT_NAME


def font_for_text(text):
    """Choose a font with glyph coverage for the text's script."""
    value = str(text)
    if any("\u0600" <= ch <= "\u06ff" or "\u0750" <= ch <= "\u077f"
           or "\u08a0" <= ch <= "\u08ff" for ch in value):
        return FONT_NAME
    return NUMERIC_FONT_NAME

class LatinSpinnerOption(SpinnerOption):
    """Render every market-symbol dropdown row with the bundled Latin font."""
    def __init__(self, **kwargs):
        kwargs.setdefault("font_name", NUMERIC_FONT_NAME)
        kwargs.setdefault("font_size", sp(16))
        kwargs.setdefault("color", (1, 1, 1, 1))
        super().__init__(**kwargs)


TABDEAL_WEBSITE = "https://tabdeal.org/"
FUTURES_API = "https://api1.tabdeal.org/r/fapi/v1"


def normalize_digits(value):
    """Convert Persian and Arabic-Indic digits to ASCII before numeric parsing."""
    return str(value).translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))


def contains_arabic(text):
    """Return whether text contains Arabic-script code points."""
    return any(
        0x0600 <= ord(char) <= 0x06FF
        or 0x0750 <= ord(char) <= 0x077F
        or 0x08A0 <= ord(char) <= 0x08FF
        for char in str(text)
    )


def fa_plain(text):
    """Shape Persian text for widgets that do not support Kivy markup."""
    value = str(text)
    if not contains_arabic(value):
        return value
    try:
        if arabic_reshaper is not None and get_display is not None:
            return get_display(arabic_reshaper.reshape(value), base_dir="R")
    except Exception:
        pass
    return value

def assistant_rtl_text(text):
    """Resolve Persian shaping and bidi order once before a neutral-direction Kivy widget."""
    value = str(text)
    if not contains_arabic(value):
        return value
    try:
        if arabic_reshaper is not None and get_display is not None:
            # get_display returns presentation-form glyphs in visual order.
            # Assistant widgets must then use base_direction='ltr' so Kivy
            # does not apply a second RTL reorder to the already-resolved text.
            return get_display(arabic_reshaper.reshape(value), base_dir="R")
    except Exception:
        pass
    return value


def fa(text):
    """Shape Persian text and mark Latin runs to use the Latin-capable font."""
    value = str(text)
    if not contains_arabic(value):
        return value
    try:
        if arabic_reshaper is not None and get_display is not None:
            value = get_display(arabic_reshaper.reshape(value), base_dir="R")
            # Kivy Label markup lets a mixed RTL/LTR sentence use the correct
            # font for each run instead of asking the Arabic font for Latin glyphs.
            value = re.sub(
                r"[\x20-\x7e]+",
                lambda match: "[font=Roboto]{}[/font]".format(
                    match.group(0).replace("&", "&amp;").replace("[", "&bl;").replace("]", "&br;")
                ),
                value,
            )
    except Exception:
        pass
    return value


def rtl_runtime_status():
    """Return a compact runtime check for the actual Persian shaping pipeline."""
    if FONT_LOAD_ERROR:
        return "persian_font=FAIL; " + FONT_LOAD_ERROR[:180]
    if arabic_reshaper is None or get_display is None:
        return "rtl_libraries=FAIL; reshaper_or_bidi_unavailable"
    try:
        sample = "فارسی آزمایش پشتیبانی"
        shaped = arabic_reshaper.reshape(sample)
        displayed = get_display(shaped, base_dir="R")
        forms = [
            ord(char) for char in displayed
            if 0xFB50 <= ord(char) <= 0xFDFF
            or 0xFE70 <= ord(char) <= 0xFEFF
        ]
        if not forms:
            return "rtl_libraries=PARTIAL; no_presentation_forms"
        return "rtl_libraries=OK; shaping=OK; font=" + str(FONT_NAME)
    except Exception as exc:
        return "rtl_libraries=ERROR; {}: {}".format(
            type(exc).__name__, str(exc)[:120]
        )


class LivePriceChart(Widget):
    """Lightweight rolling live-price line chart; no candle wait or extra deps."""
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.prices = []
        self.bind(pos=self._redraw, size=self._redraw)

    def add_price(self, price):
        try:
            value = float(price)
            if not (value > 0 and value < float("inf")):
                return
        except (TypeError, ValueError, OverflowError):
            return
        self.prices.append(value)
        del self.prices[:-120]
        self._redraw()

    def _redraw(self, *_args):
        self.canvas.clear()
        x, y = self.pos
        width, height = self.size
        if width <= 2 or height <= 2:
            return
        with self.canvas:
            Color(0.055, 0.075, 0.105, 1)
            from kivy.graphics import Rectangle
            Rectangle(pos=self.pos, size=self.size)
            Color(0.20, 0.28, 0.36, 0.8)
            for fraction in (0.25, 0.5, 0.75):
                gy = y + height * fraction
                Line(points=[x + dp(5), gy, x + width - dp(5), gy], width=0.7)
            if len(self.prices) < 2:
                return
            low, high = min(self.prices), max(self.prices)
            span = high - low
            if span <= max(abs(high), 1.0) * 1e-9:
                span = max(abs(high) * 0.0002, 1e-8)
                low -= span
            points = []
            left, right = x + dp(7), x + width - dp(7)
            bottom, top = y + dp(8), y + height - dp(8)
            for index, value in enumerate(self.prices):
                px = left + (right - left) * index / max(1, len(self.prices) - 1)
                py = bottom + (top - bottom) * (value - low) / span
                points.extend([px, py])
            Color(0.20, 0.85, 0.66, 1)
            Line(points=points, width=1.7)

class FuturesAnalyzerApp(App):
    def build(self):
        # User-editable capital is kept locally on this device between launches.
        self.settings_path = os.path.join(self.user_data_dir, "settings.json")
        try:
            os.makedirs(self.user_data_dir, exist_ok=True)
        except OSError:
            pass
        self.capital_usdt = 8.0
        try:
            with open(self.settings_path, "r", encoding="utf-8") as settings_file:
                saved = json.load(settings_file)
            saved_capital = float(saved.get("capital_usdt", 8.0))
            if saved_capital > 0 and saved_capital < 1000000000:
                self.capital_usdt = saved_capital
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
        Window.clearcolor = (0.035, 0.045, 0.065, 1)
        root = BoxLayout(orientation="vertical", padding=[dp(16), dp(14), dp(16), dp(14)], spacing=dp(10))

        root.add_widget(Label(text="Futures Analyzer", font_name=NUMERIC_FONT_NAME, font_size=sp(23), bold=True,
                              color=(0.35, 0.78, 1, 1), size_hint_y=None, height=dp(48)))
        root.add_widget(self.make_label("تحلیل‌گر بازار فیوچرز", size=18, height=dp(42)))

        scroll = ScrollView(do_scroll_x=False)
        content = BoxLayout(orientation="vertical", spacing=dp(12), size_hint_y=None,
                            padding=[0, dp(4), 0, dp(8)])
        content.bind(minimum_height=content.setter("height"))

        content.add_widget(self.section_title("وضعیت اتصال"))
        self.connection = self.make_label("وضعیت اینترنت و سایت تبدیل هنوز بررسی نشده است",
                                          size=15, color=(1, 0.72, 0.35, 1), height=dp(58))
        content.add_widget(self.connection)

        self.account_summary = self.make_label(
            "حساب تبدیل هنوز متصل نشده است؛ اطلاعات حساب فقط با درخواست دستی خوانده می‌شود.",
            size=13, color=(0.75, 0.82, 0.92, 1), height=dp(62))
        content.add_widget(self.account_summary)
        self.account_button = Button(
            markup=True, text=fa("اتصال امن و فقط‌خواندنی به حساب تبدیل"),
            font_name=FONT_NAME, font_size=sp(15), size_hint_y=None,
            height=dp(48), background_normal="",
            background_color=(0.12, 0.34, 0.42, 1), color=(1, 1, 1, 1))
        self.account_button.bind(on_press=self.open_account_connection)
        content.add_widget(self.account_button)

        content.add_widget(self.section_title("نماد معاملاتی"))
        # Keep Latin ticker and Persian contract text in separate labels.
        # Mixing LTR ticker/punctuation with RTL text caused tofu boxes on some
        # Android/Kivy font fallback combinations on the home screen.
        self.current_symbol = "BTC_USDT"
        self.verified_market_symbols = set()
        self.orderbook_poll_event = None
        self.orderbook_poll_stop = threading.Event()
        self.orderbook_poll_lock = threading.Lock()
        self.orderbook_poll_thread = None
        self.orderbook_last_success_at = None
        self.orderbook_last_error = None
        self.orderbook_update_count = 0
        self.orderbook_price_history = []
        self.symbol_spinner = Spinner(
            text=self.current_symbol,
            values=(self.current_symbol,),
            font_name=NUMERIC_FONT_NAME,
            option_cls=LatinSpinnerOption,
            font_size=sp(16),
            size_hint_y=None,
            height=dp(46),
            background_normal="",
            background_color=(0.10, 0.20, 0.30, 1),
            color=(1, 1, 1, 1),
        )
        self.symbol_spinner.bind(text=self.on_symbol_selected)
        content.add_widget(self.symbol_spinner)
        content.add_widget(self.make_label("قرارداد فیوچرز فعال تبدیل", size=17, height=dp(34)))

        content.add_widget(self.section_title("اطلاعات بازار"))
        cards = GridLayout(cols=2, spacing=dp(8), size_hint_y=None, height=dp(142))
        card, self.price_value = self.info_card("قیمت میانی دفتر سفارش", "هنوز دریافت نشده")
        cards.add_widget(card)
        card, self.bid_value = self.info_card("بهترین قیمت خرید", "-")
        cards.add_widget(card)
        card, self.ask_value = self.info_card("بهترین قیمت فروش", "—")
        cards.add_widget(card)
        cards.add_widget(self.info_card("نوع داده", "تصویر دفتر سفارش")[0])
        content.add_widget(cards)

        # Tappable order-book header refreshes live data. Each level is also
        # a real button so the user can inspect that exact bid/ask price and size.
        book_title_button = Button(
            markup=True, text=fa("دفتر سفارش  -  لمس برای به‌روزرسانی"),
            font_name=FONT_NAME, font_size=sp(17),
            size_hint_y=None, height=dp(42),
            background_normal="", background_color=(0.08, 0.28, 0.42, 1),
            color=(0.35, 0.78, 1, 1),
        )
        book_title_button.bind(on_press=self._manual_refresh_order_book)
        content.add_widget(book_title_button)
        self.orderbook_freshness = self.make_label(
            "به‌روزرسانی خودکار پس از دریافت نخستین دفتر سفارش فعال می‌شود",
            size=11, height=dp(32), color=(0.72, 0.80, 0.90, 1)
        )
        content.add_widget(self.orderbook_freshness)
        book_grid = BoxLayout(orientation="vertical", spacing=dp(10), size_hint_y=None, height=dp(520))
        self.bid_rows = []
        self.ask_rows = []

        def make_book_panel(title, rows, title_color):
            panel = BoxLayout(orientation="vertical", spacing=dp(3), size_hint_y=None, height=dp(255))
            panel.add_widget(self.make_label(title, size=15, color=title_color, height=dp(34)))
            header = GridLayout(cols=2, spacing=dp(2), size_hint_y=None, height=dp(28))
            header.add_widget(self.make_label("قیمت", size=11, color=(0.7, 0.78, 0.88, 1), height=dp(28)))
            header.add_widget(self.make_label("مقدار", size=11, color=(0.7, 0.78, 0.88, 1), height=dp(28)))
            panel.add_widget(header)
            for _ in range(5):
                row_button = Button(
                    text="--       --", font_name=NUMERIC_FONT_NAME, font_size=sp(14),
                    color=(0.95, 0.97, 1, 1), size_hint_y=None, height=dp(34),
                    background_normal="", background_color=(0.10, 0.13, 0.18, 1),
                )
                row_button.bind(
                    on_press=lambda instance, side=title: self.open_order_level(
                        side, instance.text
                    )
                )
                panel.add_widget(row_button)
                rows.append(row_button)
            return panel

        book_grid.add_widget(make_book_panel("خرید", self.bid_rows, (0.3, 0.85, 0.55, 1)))
        book_grid.add_widget(make_book_panel("فروش", self.ask_rows, (1, 0.48, 0.42, 1)))
        content.add_widget(book_grid)

        content.add_widget(self.section_title("تحلیل تکنیکال"))
        self.analysis_signal = self.make_label("برای تحلیل تکنیکال، معامله‌گر آزمایشی را اجرا کنید؛ ابتدا داده کافی جمع‌آوری می‌شود.", size=15, height=dp(42))
        content.add_widget(self.analysis_signal)
        self.analysis_metrics = self.make_label("SMA10: --   SMA20: --   RSI14: --   ATR14: --", size=12, height=dp(38))
        self.analysis_metrics.font_name = NUMERIC_FONT_NAME

        self.smart_analysis_button = Button(
            markup=True, text=fa("تحلیل هوشمند و پیشنهاد معامله"), font_name=FONT_NAME,
            font_size=sp(16), size_hint_y=None, height=dp(50),
            background_normal="", background_color=(0.12, 0.38, 0.58, 1),
            color=(1, 1, 1, 1),
        )
        self.smart_analysis_button.bind(on_press=self.open_smart_analysis)

        content.add_widget(self.smart_analysis_button)

        self.openai_assistant_button = Button(
            markup=True, text=fa("اتصال به هوش مصنوعی رایگان"), font_name=FONT_NAME,
            font_size=sp(16), size_hint_y=None, height=dp(50),
            background_normal="", background_color=(0.18, 0.34, 0.48, 1),
            color=(1, 1, 1, 1))
        self.openai_assistant_button.bind(on_press=self.open_openai_assistant)
        content.add_widget(self.openai_assistant_button)
        self.latest_technical_analysis = None
        content.add_widget(self.analysis_metrics)

        self.risk_button = Button(markup=True, text=fa("محاسبه مدیریت ریسک"), font_name=FONT_NAME, font_size=sp(16),
                                  size_hint_y=None, height=dp(48), background_normal="",
                                  background_color=(0.12, 0.48, 0.38, 1), color=(1, 1, 1, 1))
        self.risk_button.bind(on_press=self.open_risk_calculator)
        content.add_widget(self.risk_button)

        self.diagnostics_button = Button(
            markup=True, text=fa("مرکز عیب‌یابی و تعمیر برنامه"), font_name=FONT_NAME,
            font_size=sp(16), size_hint_y=None, height=dp(50),
            background_normal="", background_color=(0.30, 0.24, 0.48, 1),
            color=(1, 1, 1, 1))
        self.diagnostics_button.bind(on_press=self.open_diagnostics)
        content.add_widget(self.diagnostics_button)
        self.last_diagnostic_results = {}
        self.last_diagnostic_time = "هنوز اجرا نشده"
        self.ai_font_loaded = bool(FONT_PATH)
        self.ai_font_error = str(FONT_LOAD_ERROR or "")
        self.check_in_progress = False

        self.auto_trader_button = Button(
            markup=True, text=fa("معامله‌گر خودکار آزمایشی"), font_name=FONT_NAME,
            font_size=sp(16), size_hint_y=None, height=dp(50),
            background_normal="", background_color=(0.42, 0.30, 0.12, 1),
            color=(1, 1, 1, 1))
        self.auto_trader_button.bind(on_press=self.open_auto_trader)
        content.add_widget(self.auto_trader_button)

        self.multi_scan_button = Button(
            markup=True, text=fa("اسکن همه نمادهای فعال فیوچرز"),
            font_name=FONT_NAME, font_size=sp(16), size_hint_y=None,
            height=dp(50), background_normal="",
            background_color=(0.10, 0.36, 0.34, 1), color=(1, 1, 1, 1))
        self.multi_scan_button.bind(on_press=self.open_multi_market_scan)
        content.add_widget(self.multi_scan_button)
        self.multi_scan_thread = None
        self.multi_scan_status_label = None

        self.auto_stop_event = threading.Event()
        self.auto_trader_thread = None
        self.auto_engine = None
        # The WebSocket callback and periodic scan worker may persist the same
        # engine concurrently. Serialize snapshot creation and atomic replace.
        self._paper_state_lock = threading.RLock()
        self.auto_status_label = None
        self.latest_market_price = None

        content.add_widget(self.section_title("نتیجه بررسی"))
        self.analysis = self.make_label(
            "برای بررسی، دکمه پایین را بزنید. این آزمایش دسترسی سایت و API عمومی فیوچرز را می‌سنجد.",
            size=15, height=dp(105))
        content.add_widget(self.analysis)

        content.add_widget(self.make_label(
            "معامله‌گر آزمایشی فقط با داده معاملات لحظه‌ای و بدون ارسال سفارش واقعی کار می‌کند.",
            size=13, color=(1, 0.68, 0.35, 1), height=dp(66)))
        scroll.add_widget(content)
        root.add_widget(scroll)

        self.check_button = Button(markup=True, text=fa("بررسی اتصال"), font_name=FONT_NAME, font_size=sp(17),
                                   size_hint_y=None, height=dp(54), background_normal="",
                                   background_color=(0.08, 0.42, 0.68, 1), color=(1, 1, 1, 1))
        self.check_button.bind(on_press=self.show_status)
        root.add_widget(self.check_button)
        return root

    def make_label(self, text, size=15, color=(1, 1, 1, 1), height=dp(48)):
        return Label(text=fa(text), font_name=font_for_text(text), markup=True, font_size=sp(size), color=color,
                     size_hint_y=None, height=height, halign="center", valign="middle",
                     text_size=(Window.width - dp(40), None))

    def section_title(self, text):
        return self.make_label(text, size=17, color=(0.35, 0.78, 1, 1), height=dp(38))

    def info_card(self, heading, value):
        card = BoxLayout(orientation="vertical", padding=dp(7), spacing=dp(4))
        card.add_widget(self.make_label(heading, size=14, color=(0.45, 0.78, 1, 1), height=dp(42)))
        # Keep Persian placeholders and mixed ticker labels on the bundled
        # Persian font. Switch to Roboto only after a numeric market value arrives.
        value_label = self.make_label(value, size=12, color=(0.9, 0.93, 0.98, 1), height=dp(52))
        card.add_widget(value_label)
        return card, value_label

    def on_symbol_selected(self, spinner, value):
        if value:
            selected = str(value).strip().upper()
            if selected == self.current_symbol:
                return
            self.current_symbol = selected
            # Never leave another contract's prices visible under the new ticker.
            for label in (self.price_value, self.bid_value, self.ask_value):
                label.text = "--"
            for row in self.bid_rows + self.ask_rows:
                row.text = "--       --"
                row.disabled = True
            self.orderbook_freshness.text = fa("در انتظار دریافت دفتر سفارش نماد جدید...")
            matched = next((item for item in self.verified_market_symbols
                            if normalize_market_symbol(item) == normalize_market_symbol(selected)), None)
            if matched is not None:
                # Keep the exchange-native spelling used by exchangeInfo.
                self.current_symbol = matched
                self._schedule_order_book_poll(0)

    def update_symbol_options(self, symbols):
        if not symbols:
            return
        self.verified_market_symbols = set(str(item) for item in symbols)
        cache_manager = getattr(self, "multi_symbol_candle_cache", None)
        if cache_manager is not None:
            cache_manager.set_allowed_symbols(self.verified_market_symbols - {self.current_symbol})
        current = self.current_symbol
        matched_current = next((item for item in symbols
                                if normalize_market_symbol(item) == normalize_market_symbol(current)), None)
        self.symbol_spinner.values = tuple(symbols)
        if matched_current is not None:
            self.current_symbol = matched_current
            self.symbol_spinner.text = matched_current
        else:
            self.symbol_spinner.text = symbols[0]
            self.current_symbol = symbols[0]

    def show_status(self, instance):
        if self.check_in_progress:
            return
        self.check_in_progress = True
        self.check_button.disabled = True
        self.symbol_spinner.disabled = True
        self.check_button.text = fa("در حال بررسی...")
        self.connection.text = fa("در حال آزمایش سایت و API بازار فیوچرز...")
        self.analysis.text = fa("لطفاً چند لحظه صبر کنید.")
        # Never leave a previous symbol's book visible while a new request runs.
        self.last_market_snapshot = None
        for label in (self.price_value, self.bid_value, self.ask_value):
            label.font_name = FONT_NAME
            label.text = fa("در حال دریافت...")
        for row in self.bid_rows + self.ask_rows:
            row.text = "--       --"
            row.disabled = True
            row.background_color = (0.10, 0.13, 0.18, 1)
        selected_symbol = self.current_symbol
        threading.Thread(
            target=self.check_services, args=(selected_symbol,), daemon=True
        ).start()

    def check_services(self, requested_symbol="BTC_USDT"):
        session = requests.Session()
        mount_tabdeal_tls_compat(session, certifi.where())
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36",
            "Accept": "application/json",
            "Connection": "close",
        })
        results = {}

        def get_response(path, params=None, timeout=22):
            # Safe GET retry policy: transient transport failures, HTTP 429,
            # and HTTP 5xx are retried. Permanent 4xx errors fail immediately.
            # TLS certificate verification remains enabled on every attempt.
            url = FUTURES_API + path
            last_error = None
            last_http_error = None
            for attempt in range(3):
                response = None
                try:
                    response = session.get(
                        url, params=params, timeout=timeout,
                        verify=certifi.where(), headers={"Connection": "close"}
                    )
                    status_code = response.status_code
                    results[path.strip("/").replace("/", "_") + "_status"] = status_code
                    if status_code == 429 or status_code >= 500:
                        detail = response.text[:500].replace("\n", " ")
                        if path == "/depth":
                            results["depth_diagnostic"] = "HTTP {} {} body={}".format(
                                status_code, response.url, detail
                            )
                        retry_after = response.headers.get("Retry-After", "")
                        try:
                            delay = min(8.0, max(0.5, float(retry_after)))
                        except (TypeError, ValueError, OverflowError):
                            delay = min(4.0, 1.0 * (attempt + 1))
                        last_http_error = RuntimeError(
                            "{} returned temporary HTTP {}: {}".format(path, status_code, detail)
                        )
                        response.close()
                        response = None
                        if attempt < 2:
                            time.sleep(delay)
                            continue
                        raise last_http_error
                    if not response.ok:
                        detail = response.text[:500].replace("\n", " ")
                        if path == "/depth":
                            results["depth_diagnostic"] = "HTTP {} {} body={}".format(
                                status_code, response.url, detail
                            )
                        response.close()
                        response = None
                        raise RuntimeError("{} returned HTTP {}: {}".format(
                            path, status_code, detail
                        ))
                    successful_response = response
                    response = None  # caller owns and closes the successful response
                    return successful_response
                except (requests.exceptions.ConnectionError,
                        requests.exceptions.SSLError,
                        requests.exceptions.Timeout) as exc:
                    last_error = exc
                    if attempt < 2:
                        time.sleep(1.0 * (attempt + 1))
                finally:
                    if response is not None:
                        response.close()
            if last_error is not None:
                raise RuntimeError("{}: ارتباط امن برقرار نشد پس از 3 تلاش: {}".format(
                    path, "{}: {}".format(type(last_error).__name__, str(last_error)[:220])
                ))
            raise RuntimeError("{}: درخواست پس از 3 تلاش ناموفق بود: {}".format(
                path, str(last_http_error or "unknown HTTP error")[:220]
            ))

        try:
            try:
                web = session.get(TABDEAL_WEBSITE, timeout=18, allow_redirects=True,
                                  verify=certifi.where())
                results["website"] = web.status_code
                web.close()
            except Exception as exc:
                results["website_error"] = "{}: {}".format(type(exc).__name__, str(exc)[:180])

            # These are Tabdeal's documented public Futures read-only endpoints.
            try:
                ping = get_response("/ping", timeout=12)
                ping.close()
                results["market_ping"] = 200
            except Exception as ping_exc:
                # /ping is only a health probe. A transient failure must not
                # prevent independent exchangeInfo and depth requests.
                results["ping_error"] = "{}: {}".format(
                    type(ping_exc).__name__, str(ping_exc)[:260]
                )

            # Do not make connectivity depend on an undocumented /time route.
            # /ping, /exchangeInfo and /depth are the verified public endpoints.

            # exchangeInfo is useful for refreshing the symbol list, but it is
            # not a prerequisite for the depth endpoint. On mobile networks one
            # request can fail TLS while the next succeeds. Keep the last selected
            # symbol as a safe fallback and always attempt depth independently.
            market_symbol = requested_symbol or "BTC_USDT"
            try:
                info = get_response("/exchangeInfo", timeout=25)
                try:
                    data = info.json()
                except Exception as exc:
                    raise RuntimeError("/exchangeInfo returned invalid JSON: {}".format(str(exc)[:120]))
                finally:
                    info.close()

                symbols = data
                if isinstance(data, dict):
                    symbols = data.get("symbols")
                    if symbols is None and isinstance(data.get("data"), dict):
                        symbols = data["data"].get("symbols")
                    if symbols is None and isinstance(data.get("data"), list):
                        symbols = data["data"]
                if not isinstance(symbols, list):
                    preview = str(data)[:220]
                    raise RuntimeError("/exchangeInfo HTTP 200 but unexpected response format: {}".format(preview))
                results["market_info"] = 200
                from market_symbols import list_market_symbols
                available_symbols = list_market_symbols(symbols)
                if not available_symbols:
                    raise RuntimeError("/exchangeInfo contained no active USDT perpetual symbols")
                results["symbol_count"] = len(available_symbols)
                results["verified_symbols"] = list(available_symbols)
                Clock.schedule_once(
                    lambda dt, values=available_symbols: self.update_symbol_options(values), 0
                )
                try:
                    market_symbol = select_market_symbol(symbols, requested_symbol)
                except ValueError:
                    market_symbol = available_symbols[0]
            except Exception as info_exc:
                results["market_info_error"] = "{}: {}".format(
                    type(info_exc).__name__, str(info_exc)[:260]
                )                # Fail closed: a syntactically plausible ticker is not proof
                # that the contract exists or is active. Never query depth for
                # an unverified/stale symbol when exchangeInfo is unavailable.
                market_symbol = None

            if not market_symbol:
                results["market_symbol"] = "UNVERIFIED"
                raise RuntimeError(
                    "Futures symbol could not be verified because exchangeInfo was unavailable"
                )

            results["market_symbol"] = market_symbol
            results["symbol_verified"] = True
            depth = get_response("/depth", params={"symbol": market_symbol, "limit": 5}, timeout=25)
            results["depth_http"] = depth.status_code
            results["depth_url"] = depth.url
            results["depth_content_type"] = depth.headers.get("Content-Type", "unknown")
            try:
                raw_text = depth.text[:1200]
                try:
                    book = depth.json()
                except Exception:
                    results["depth_diagnostic"] = (
                        "HTTP {}; non-JSON response; content-type={}; body={}".format(
                            depth.status_code, results["depth_content_type"],
                            raw_text.replace("\n", " ")
                        )
                    )
                    raise RuntimeError("depth endpoint returned non-JSON data")
            finally:
                depth.close()

            from orderbook import parse_order_book
            bids, asks = parse_order_book(book)
            bid, ask = bids[0][0], asks[0][0]
            if bid <= 0 or ask <= 0 or ask < bid:
                results["depth_diagnostic"] = "HTTP 200; invalid best prices; bid={} ask={}".format(bid, ask)
                raise RuntimeError("depth returned invalid prices")
            results["depth_status"] = 200
            results["best_bid"], results["best_ask"] = bid, ask
            results["bids"] = bids[:5]
            results["asks"] = asks[:5]
            results["mid_price"] = bid / 2.0 + ask / 2.0
            results["depth_diagnostic"] = "HTTP 200; bids={}; asks={}; parsed=OK".format(len(bids), len(asks))

            # The documented public Futures REST API currently exposes
            # ping, time, exchangeInfo and depth, but no public kline or
            # 24h ticker endpoint. Do not substitute another exchange's paths.
            # The order-book midpoint is an estimate, not a last-trade price.
            results["market_price_source"] = "order_book_midpoint_estimate"
            results["market_summary"] = None
            results["technical_analysis"] = None

        except Exception as exc:
            results["market_error"] = "{}: {}".format(type(exc).__name__, str(exc)[:320])
        finally:
            session.close()
        Clock.schedule_once(lambda dt, res=results: self.show_result(res), 0)



    def _manual_refresh_order_book(self, *_args):
        """Refresh the visible order book immediately when its header is tapped."""
        if self.orderbook_poll_stop.is_set():
            self.orderbook_freshness.text = fa("به‌روزرسانی در دسترس نیست؛ پایش بازار متوقف است.")
            return
        if self.current_symbol not in self.verified_market_symbols:
            self.orderbook_freshness.text = fa("ابتدا نماد را با بررسی اتصال تأیید کنید.")
            return
        self.orderbook_freshness.text = fa("درخواست دفتر سفارش جدید ارسال شد…")
        self.orderbook_freshness.color = (0.85, 0.78, 0.35, 1)
        self._schedule_order_book_poll(0)

    def _start_order_book_polling(self):
        """Start one foreground-only Kivy timer; network reads run off the UI thread."""
        if self.orderbook_poll_event is None:
            self.orderbook_poll_stop.clear()
            self.orderbook_poll_event = Clock.schedule_interval(
                self._schedule_order_book_poll, 2.0
            )
        self._schedule_order_book_poll(0)

    def _schedule_order_book_poll(self, _dt):
        """Schedule a depth read every two seconds without allowing overlap."""
        if self.orderbook_poll_stop.is_set():
            return False
        requested_symbol = str(self.current_symbol).strip().upper()
        symbol = next((item for item in self.verified_market_symbols
                       if normalize_market_symbol(item) == normalize_market_symbol(requested_symbol)), None)
        if symbol is None:
            return True
        if not self.orderbook_poll_lock.acquire(False):
            return True
        worker = threading.Thread(
            target=self._order_book_poll_worker, args=(symbol,), daemon=True
        )
        self.orderbook_poll_thread = worker
        try:
            worker.start()
        except Exception:
            self.orderbook_poll_lock.release()
            raise
        return True

    def _order_book_poll_worker(self, symbol):
        snapshot = None
        error = None
        session = None
        try:
            session = requests.Session()
            mount_tabdeal_tls_compat(session, certifi.where())
            session.headers.update({
                "User-Agent": "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36",
                "Accept": "application/json",
                "Connection": "close",
            })
            snapshot = fetch_order_book_snapshot(
                session, FUTURES_API, symbol, certifi.where(),
                timeout=(3.05, 6.0)
            )
        except Exception as exc:
            error = "{}: {}".format(type(exc).__name__, str(exc)[:220])
        finally:
            try:
                if session is not None:
                    session.close()
            except Exception:
                pass
            self.orderbook_poll_lock.release()
        if not self.orderbook_poll_stop.is_set():
            Clock.schedule_once(
                lambda _dt, data=snapshot, failure=error:
                    self._apply_order_book_poll(data, failure), 0
            )

    def _apply_order_book_poll(self, snapshot, error):
        """Publish only a validated fresh snapshot; failures never refresh old prices."""
        if self.orderbook_poll_stop.is_set():
            return
        if snapshot is not None:
            if normalize_market_symbol(snapshot.get("symbol")) != normalize_market_symbol(self.current_symbol):
                return
            self.orderbook_last_success_at = snapshot["received_at"]
            self.orderbook_last_error = None
            self.latest_market_price = snapshot["mid_price"]
            self.orderbook_update_count += 1
            self.orderbook_price_history.append({
                "timestamp": snapshot["received_at"],
                "symbol": snapshot["symbol"],
                "bid": snapshot["best_bid"],
                "ask": snapshot["best_ask"],
                "mid": snapshot["mid_price"],
            })
            # Keep a bounded ten-minute rolling sample; never write every tick
            # to flash storage or let market polling grow memory without limit.
            if len(self.orderbook_price_history) > 300:
                del self.orderbook_price_history[:-300]
            for label, value in (
                (self.price_value, snapshot["mid_price"]),
                (self.bid_value, snapshot["best_bid"]),
                (self.ask_value, snapshot["best_ask"]),
            ):
                label.font_name = NUMERIC_FONT_NAME
                label.text = format_market_value(value)
            for row_buttons, levels in (
                (self.bid_rows, snapshot["bids"]),
                (self.ask_rows, snapshot["asks"]),
            ):
                for index, row in enumerate(row_buttons):
                    if index < len(levels):
                        price, quantity = levels[index]
                        row.text = "{}       {}".format(
                            format_market_value(price),
                            format_market_value(quantity)
                        )
                        row.disabled = False
                        row.background_color = (
                            (0.10, 0.18, 0.20, 1)
                            if row_buttons is self.bid_rows
                            else (0.22, 0.13, 0.13, 1)
                        )
                    else:
                        row.text = "--       --"
                        row.disabled = True
            # Keep the AI's read-only market context aligned with the latest
            # verified book. This snapshot is never passed to the paper engine.
            current = dict(getattr(self, "last_market_snapshot", {}) or {})
            current.update({
                "symbol": snapshot["symbol"],
                "snapshot_time": snapshot["received_at"],
                "best_bid": snapshot["best_bid"],
                "best_ask": snapshot["best_ask"],
                "midpoint_estimate": snapshot["mid_price"],
                "bids": snapshot["bids"],
                "asks": snapshot["asks"],
                "data_kind": "order_book_snapshot",
            })
            self.last_market_snapshot = current
            diagnostic = dict(getattr(self, "last_diagnostic_results", {}) or {})
            diagnostic.update({
                "market_symbol": snapshot["symbol"],
                "symbol_verified": True,
                "depth_status": 200,
                "depth_http": 200,
                "best_bid": snapshot["best_bid"],
                "best_ask": snapshot["best_ask"],
                "mid_price": snapshot["mid_price"],
                "bids": snapshot["bids"],
                "asks": snapshot["asks"],
                "orderbook_poll_last_success": snapshot["received_at"],
                "orderbook_poll_updates": self.orderbook_update_count,
                "orderbook_poll_error": None,
            })
            self.last_diagnostic_results = diagnostic
            self.orderbook_freshness.text = fa(
                "آخرین ثبت موفق: {} | هدف به‌روزرسانی: ۲ ثانیه | ثبت‌های موفق: {}".format(
                    snapshot["received_at"].split("T")[-1],
                    self.orderbook_update_count
                )
            )
            self.orderbook_freshness.color = (0.35, 0.9, 0.55, 1)
            return

        self.orderbook_last_error = str(error or "unknown error")[:220]
        diagnostic = dict(getattr(self, "last_diagnostic_results", {}) or {})
        diagnostic["orderbook_poll_error"] = self.orderbook_last_error
        diagnostic["orderbook_poll_updates"] = self.orderbook_update_count
        self.last_diagnostic_results = diagnostic
        last_time = self.orderbook_last_success_at
        if last_time:
            last_clock = last_time.split("T")[-1]
            message = (
                "دریافت جدید ناموفق؛ قیمت قبلی تازه محسوب نمی‌شود. "
                "آخرین ثبت موفق: {} | علت: {}"
            ).format(last_clock, self.orderbook_last_error)
        else:
            message = "دریافت دفتر سفارش ناموفق است؛ علت: " + self.orderbook_last_error
        self.orderbook_freshness.text = fa(message)
        self.orderbook_freshness.color = (1, 0.55, 0.4, 1)

    def open_account_connection(self, instance):
        """Read account data; persist credentials only after a successful GET."""
        store = CredentialStore(os.path.join(self.user_data_dir, "account-credentials.enc"))
        body = BoxLayout(orientation="vertical", padding=dp(10), spacing=dp(6))
        body.add_widget(self.make_label(
            "کلید فقط‌خواندنی فیوچرز؛ ذخیره‌سازی فقط با رمزگذاری Android Keystore انجام می‌شود.",
            size=11, height=dp(44), color=(1, 0.76, 0.38, 1)))
        key_input = TextInput(
            hint_text="API Key", multiline=False, password=False,
            font_name=NUMERIC_FONT_NAME, font_size=sp(13),
            size_hint_y=None, height=dp(38))
        secret_input = TextInput(
            hint_text="API Secret", multiline=False, password=True,
            font_name=NUMERIC_FONT_NAME, font_size=sp(13),
            size_hint_y=None, height=dp(38))
        load_status = None
        saved_credentials_available = False
        try:
            saved = store.load()
            if saved:
                # Keep secrets out of visible text fields. The read action can
                # retrieve them directly from the encrypted Android Keystore file.
                saved_credentials_available = True
                load_status = "کلید رمزگذاری‌شده روی دستگاه موجود است؛ برای خواندن موجودی نیازی به نمایش دوباره کلیدها نیست."
        except Exception as load_exc:
            # Never expose credentials after a failed authentication/decryption,
            # but do not hide the reason the saved values were not restored.
            key_input.text = ""
            secret_input.text = ""
            load_status = (
                "بازیابی کلید ذخیره‌شده ناموفق بود؛ "
                + type(load_exc).__name__
                + ". کلیدها را دوباره وارد و ذخیره امن را آزمایش کنید."
            )
        body.add_widget(key_input)
        body.add_widget(secret_input)
        status = self.make_label(
            load_status or "مجوز برداشت و معامله را برای این کلید فعال نکنید.",
            size=11, height=dp(40), color=(1, 0.68, 0.35, 1))
        body.add_widget(status)
        buttons = BoxLayout(orientation="vertical", size_hint_y=None,
                            height=dp(88), spacing=dp(6))
        primary_actions = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(6))
        secondary_actions = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(6))
        save_button = Button(markup=True, text=fa("ذخیره کلید"), font_name=FONT_NAME, font_size=sp(12))
        connect = Button(markup=True, text=fa("خواندن موجودی"), font_name=FONT_NAME, font_size=sp(12))
        delete_saved = Button(markup=True, text=fa("حذف کلید ذخیره‌شده"), font_name=FONT_NAME, font_size=sp(11))
        cancel = Button(markup=True, text=fa("انصراف"), font_name=FONT_NAME, font_size=sp(11))
        primary_actions.add_widget(save_button)
        primary_actions.add_widget(connect)
        secondary_actions.add_widget(delete_saved)
        secondary_actions.add_widget(cancel)
        buttons.add_widget(primary_actions)
        buttons.add_widget(secondary_actions)
        body.add_widget(buttons)
        popup = Popup(title=fa_plain("اتصال فقط‌خواندنی حساب"), title_font=FONT_NAME,
                      content=body, size_hint=(0.94, 0.70), auto_dismiss=False)
        cancel.bind(on_press=popup.dismiss)

        def remove_saved(instance):
            nonlocal saved_credentials_available
            try:
                store.delete()
                saved_credentials_available = False
                key_input.text = ""
                secret_input.text = ""
                status.text = fa("کلید ذخیره‌شده و کلید رمزگذاری حذف شد.")
            except Exception as exc:
                status.text = fa("حذف امن انجام نشد: " + type(exc).__name__)
        delete_saved.bind(on_press=remove_saved)

        def save_credentials(instance):
            nonlocal saved_credentials_available
            api_key = key_input.text.strip()
            api_secret = secret_input.text.strip()
            if not api_key or not api_secret:
                status.text = fa("برای ذخیره امن، هر دو فیلد API Key و API Secret الزامی هستند.")
                return
            save_button.disabled = True
            connect.disabled = True
            delete_saved.disabled = True
            status.text = fa("در حال رمزگذاری و ذخیره کلیدها با Android Keystore...")
            def save_worker(key_value, secret_value):
                try:
                    store.save(key_value, secret_value)
                    outcome = (True, "کلیدها با رمزگذاری Android Keystore ذخیره و بازیابی آن‌ها بررسی شد.")
                except Exception as exc:
                    outcome = (False, "{}: {}".format(type(exc).__name__, str(exc)[:160]))
                finally:
                    key_value = ""
                    secret_value = ""
                def finish_save(_dt):
                    nonlocal saved_credentials_available
                    save_button.disabled = False
                    connect.disabled = False
                    delete_saved.disabled = False
                    if outcome[0]:
                        saved_credentials_available = True
                        key_input.text = ""
                        secret_input.text = ""
                        status.text = fa(outcome[1] + " اکنون می‌توانید موجودی را جداگانه بخوانید.")
                    else:
                        status.text = fa("ذخیره امن انجام نشد؛ کلیدها ذخیره‌شده محسوب نمی‌شوند. " + outcome[1])
                Clock.schedule_once(finish_save, 0)
            threading.Thread(target=save_worker, args=(api_key, api_secret), daemon=True).start()

        save_button.bind(on_press=save_credentials)

        def start_read(instance):
            key = key_input.text.strip()
            secret = secret_input.text.strip()
            if not key or not secret:
                try:
                    saved = store.load()
                except Exception as load_exc:
                    status.text = fa("بازیابی امن کلید ناموفق بود: " + type(load_exc).__name__)
                    return
                if saved:
                    key, secret = saved
                else:
                    status.text = fa("هر دو فیلد الزامی هستند؛ ابتدا کلیدها را وارد و ذخیره کنید.")
                    return
            # Keep the user's input visible until both account verification and
            # encrypted persistence have succeeded. Clearing it before the worker
            # finishes made a Keystore/storage failure look like a lost credential.
            key_input.disabled = True
            secret_input.disabled = True
            save_button.disabled = True
            connect.disabled = True
            delete_saved.disabled = True
            status.text = fa("در حال دریافت امن اطلاعات حساب...")
            # Capture credentials as worker arguments. Reassigning the outer
            # names inside worker would make them local and trigger
            # UnboundLocalError before the first account request.
            def worker(api_key, api_secret):
                try:
                    data = read_futures_account(api_key, api_secret, timeout=22)
                    try:
                        store.save(api_key, api_secret)
                        storage_status = "ذخیره امن روی همین دستگاه انجام شد."
                    except Exception as storage_exc:
                        storage_status = (
                            "حساب خوانده شد، اما ذخیره امن ممکن نشد؛ "
                            + "{}: {}".format(type(storage_exc).__name__, str(storage_exc)[:180])
                        )
                    result = (True, data, storage_status)
                except Exception as exc:
                    result = (False, "{}: {}".format(type(exc).__name__, str(exc)[:220]), "")
                finally:
                    # Drop the worker's references as soon as the request finishes.
                    api_key = ""
                    api_secret = ""
                Clock.schedule_once(lambda dt, value=result: finish(value), 0)
            def finish(result):
                key_input.disabled = False
                secret_input.disabled = False
                save_button.disabled = False
                connect.disabled = False
                delete_saved.disabled = False
                if result[0]:
                    data = result[1]
                    lines = [
                        "موجودی کیف پول: {}".format(data["wallet_balance"]),
                        "موجودی آزاد: {}".format(data["available_balance"]),
                        "سود/زیان شناور: {}".format(data["unrealized_pnl"]),
                        result[2],
                    ]
                    positions = data.get("positions", [])
                    if positions:
                        lines.append("موقعیت‌های باز:")
                        for pos in positions[:8]:
                            lines.append("{} | مقدار {} | ورود {} | PnL {}".format(
                                pos["symbol"], pos["amount"], pos["entry_price"],
                                pos["unrealized_pnl"]))
                    else:
                        lines.append("موقعیت باز فعالی گزارش نشد.")
                    self.account_summary.text = fa("\n".join(lines))
                    if result[2].startswith("ذخیره امن روی همین دستگاه انجام شد"):
                        saved_credentials_available = True
                        # Clear the sensitive fields only after the encrypted file
                        # has been written successfully, then close the dialog.
                        key_input.text = ""
                        secret_input.text = ""
                        popup.dismiss()
                        status.text = fa("اتصال حساب و ذخیره امن کلید با موفقیت انجام شد.")
                    else:
                        # Keep the dialog open and preserve input so the user can
                        # retry after a Keystore/storage problem without retyping.
                        status.text = fa(result[2] + " کلیدها هنوز ذخیره نشده‌اند؛ پس از رفع خطا دوباره تلاش کنید.")
                else:
                    self.account_summary.text = fa("خطا در خواندن حساب: " + result[1])
                    # Do not discard the user's credentials on a transient API,
                    # permission, clock-skew, or network failure.
                    status.text = fa("اتصال موفق نشد: " + result[1][:150])
            worker_thread = threading.Thread(
                target=worker, args=(key, secret), daemon=True
            )
            # Remove the UI callback's copies; the worker owns the short-lived
            # arguments and clears them in its finally block.
            key = ""
            secret = ""
            worker_thread.start()

        connect.bind(on_press=start_read)
        popup.open()

    def open_order_level(self, side, row_text):
        parts = row_text.split()
        if len(parts) < 2 or parts[0] == "--" or parts[1] == "--":
            return
        body = BoxLayout(orientation="vertical", padding=dp(14), spacing=dp(10))
        body.add_widget(self.make_label("سمت سفارش: " + side, size=16, height=dp(42)))
        body.add_widget(self.make_label("قیمت: " + parts[0], size=15, height=dp(38)))
        body.add_widget(self.make_label("مقدار: " + parts[1], size=15, height=dp(38)))
        close_button = Button(markup=True, text=fa("بستن"), font_name=FONT_NAME,
                              size_hint_y=None, height=dp(44))
        popup = Popup(title=fa_plain("جزئیات ردیف دفتر سفارش"),
                      title_font=FONT_NAME, content=body,
                      size_hint=(0.88, 0.42))
        close_button.bind(on_press=popup.dismiss)
        body.add_widget(close_button)
        popup.open()

    def open_openai_assistant(self, instance):
        """Multi-turn Groq conversation with a session-only key and read-only app context."""
        body = BoxLayout(orientation="vertical", padding=dp(14), spacing=dp(9))

        key_label = self.make_label(
            "کلید رایگان Groq (فقط در همین نشست؛ ذخیره نمی‌شود)",
            size=13, height=dp(40))
        key_label.halign = "right"
        key_label.bind(width=lambda widget, width: setattr(widget, "text_size", (width, None)))
        body.add_widget(key_label)

        # API credentials are Latin text and remain left-to-right.
        key_input = TextInput(
            multiline=False, password=True, hint_text="gsk_...",
            font_name=NUMERIC_FONT_NAME, font_size=sp(14),
            base_direction="ltr", text_language="en", halign="left",
            size_hint_y=None, height=dp(46))
        body.add_widget(key_input)

        chat_history = []
        # Kivy's native RTL pass produced reversed/misordered Persian on the
        # actual Android device. Resolve shaping and bidi once for this display
        # Label, then keep the widget LTR so Android/Kivy cannot reorder it again.
        # Keep chat_history and API payloads in logical Unicode; transform only
        # the presentation string assigned to this read-only result Label.
        greeting = "سلام! من دستیار هوشمند برنامه هستم. پیام خود را بنویسید."
        result = Label(
            text=assistant_rtl_text(greeting),
            font_name=FONT_NAME, font_size=sp(13), markup=False,
            color=(1, 1, 1, 1), size_hint_y=None, height=dp(110),
            halign="right", valign="top",
            base_direction="ltr", text_language="en",
        )
        result.bind(width=lambda widget, width: setattr(widget, "text_size", (width, None)))
        result.bind(texture_size=lambda widget, size: setattr(
            widget, "height", max(dp(110), size[1] + dp(16))))
        result_scroll = ScrollView(
            do_scroll_x=False, do_scroll_y=True, bar_width=dp(4),
            size_hint=(1, 1))
        result_scroll.add_widget(result)
        body.add_widget(result_scroll)

        prompt_label = self.make_label("پیام شما", size=13, height=dp(28))
        prompt_label.halign = "right"
        prompt_label.bind(width=lambda widget, width: setattr(widget, "text_size", (width, None)))
        body.add_widget(prompt_label)
        prompt_input = TextInput(
            multiline=True, font_name=FONT_NAME, font_size=sp(14),
            # Persian prompt entry must use RTL direction explicitly. Keep
            # the stored text as logical Unicode; do not reshape/reorder input
            # before sending it to the AI API.
            # Use the Android/Kivy Persian-language shaping path for Persian
            # input. The TextInput retains logical Unicode for the API request.
            base_direction="rtl", text_language="ar", halign="right",
            padding=[dp(10), dp(10), dp(10), dp(10)],
            size_hint_y=None, height=dp(92))
        body.add_widget(prompt_input)

        controls = BoxLayout(orientation="horizontal", spacing=dp(8),
                             size_hint_y=None, height=dp(48))
        send_button = Button(markup=True, text=fa("ارسال به هوش مصنوعی"),
                             font_name=FONT_NAME, font_size=sp(14))
        close_button = Button(markup=True, text=fa("بستن"),
                              font_name=FONT_NAME, font_size=sp(14))
        controls.add_widget(close_button)
        controls.add_widget(send_button)
        body.add_widget(controls)

        popup = Popup(title=fa_plain("گفت‌وگو با هوش مصنوعی Groq"),
                      title_font=FONT_NAME, content=body, size_hint=(0.95, 0.92))

        def render_chat():
            lines = []
            for item in chat_history:
                label = "شما" if item["role"] == "user" else "Groq"
                lines.append(label + ":\n" + item["content"])
            result.text = assistant_rtl_text("\n\n".join(lines))
            Clock.schedule_once(lambda _dt: setattr(
                result_scroll, "scroll_y", 0), 0)

        def send_request(_button):
            api_key = key_input.text.strip()
            prompt = prompt_input.text.strip()
            if not api_key or not prompt:
                result.text = assistant_rtl_text("کلید Groq و پیام خود را وارد کنید.")
                return

            # This context is allow-listed; account data, balances, positions,
            # orders and credentials are never included in requests.
            context = build_ai_diagnostic_context(self)
            history_snapshot = list(chat_history)
            send_button.disabled = True
            send_button.text = fa("در حال دریافت پاسخ...")
            prompt_input.disabled = True

            def worker():
                try:
                    answer = analyze_with_openai(
                        api_key, prompt, context,
                        conversation_history=history_snapshot)
                    success = True
                    message = answer
                except Exception as exc:
                    success = False
                    message = "خطا در ارتباط با Groq: {}: {}".format(
                        type(exc).__name__, str(exc)[:240])

                def finish(_dt):
                    if success:
                        chat_history.append({"role": "user", "content": prompt})
                        chat_history.append({"role": "assistant", "content": message})
                        prompt_input.text = ""
                        render_chat()
                    else:
                        result.text = assistant_rtl_text(message)
                    send_button.disabled = False
                    send_button.text = fa("ارسال به هوش مصنوعی")
                    prompt_input.disabled = False

                Clock.schedule_once(finish, 0)

            threading.Thread(target=worker, daemon=True).start()

        send_button.bind(on_press=send_request)

        def close_popup(_button):
            key_input.text = ""
            chat_history[:] = []
            popup.dismiss()

        close_button.bind(on_press=close_popup)
        popup.bind(on_dismiss=lambda *_: setattr(key_input, "text", ""))
        popup.open()

    def open_smart_analysis(self, instance):
        technical = self.latest_technical_analysis
        body = BoxLayout(orientation="vertical", padding=dp(14), spacing=dp(10))
        report = BoxLayout(orientation="vertical", spacing=dp(8),
                           size_hint_y=None)
        report.bind(minimum_height=report.setter("height"))

        def add_line(text, size=15, height=dp(42), color=(0.94, 0.96, 1, 1), font=FONT_NAME):
            label = Label(text=fa(text), font_name=font, markup=True, font_size=sp(size),
                          color=color, size_hint_y=None, height=height,
                          halign="center", valign="middle",
                          text_size=(Window.width * 0.82, None))
            report.add_widget(label)

        # Render the Persian caption and Latin market symbol separately so each uses a font with the required glyphs.
        add_line("نماد:", size=15, font=FONT_NAME)
        add_line(self.current_symbol, size=17, font=NUMERIC_FONT_NAME)
        if not technical:
            add_line("API عمومی مستندشده تبدیل در حال حاضر داده کندل تاریخی ارائه نمی‌کند.", height=dp(70))
            add_line("بنابراین هیچ سیگنال خرید یا فروش و هیچ ورود آزمایشی بر پایه داده حدسی صادر نمی‌شود.", size=13, color=(1, 0.72, 0.35, 1), height=dp(65))
        else:
            signal = technical.get("signal", "NEUTRAL")
            price = float(technical["price"])
            signal_atr = float(technical["atr14"])
            timeframe = technical.get("timeframe_analysis")
            atr_by_timeframe = (
                timeframe.get("atr14_by_timeframe")
                if isinstance(timeframe, dict) else None
            )
            risk_atr = None
            try:
                candidate_atr = float(
                    atr_by_timeframe.get("5")
                    if isinstance(atr_by_timeframe, dict) else None
                )
                if math.isfinite(candidate_atr) and candidate_atr > 0:
                    risk_atr = candidate_atr
            except (TypeError, ValueError, OverflowError):
                risk_atr = None
            if signal == "BULLISH":
                decision = "تمایل صعودی — بررسی فرصت خرید"
                stop = price - 1.5 * risk_atr if risk_atr is not None else None
                target = price + 3.0 * risk_atr if risk_atr is not None else None
                decision_color = (0.35, 0.9, 0.55, 1)
            elif signal == "BEARISH":
                decision = "تمایل نزولی — بررسی فرصت فروش"
                stop = price + 1.5 * risk_atr if risk_atr is not None else None
                target = price - 3.0 * risk_atr if risk_atr is not None else None
                decision_color = (1, 0.55, 0.42, 1)
            else:
                decision = "سیگنال روشن نیست — فعلاً صبر کن"
                stop = target = None
                decision_color = (1, 0.75, 0.35, 1)

            add_line("نتیجه: " + decision, size=17, height=dp(58), color=decision_color)
            leverage_plan = recommend_leverage(technical, max_leverage=3)
            add_line("اهرم پیشنهادی خودکار: {}x (سقف مجاز: 3x)".format(
                leverage_plan["leverage"]), size=16, color=(0.35, 0.82, 1, 1),
                font=FONT_NAME)
            add_line("نوسان نسبی بازار: {:.2f}% — {}".format(
                leverage_plan["volatility_pct"], leverage_plan["reason"]),
                size=12, height=dp(48), font=FONT_NAME)
            add_line("ریسک پیشنهادی هر معامله: حداکثر 0.5% سرمایه", size=13)
            add_line("قیمت مبنا: {}".format(format_market_value(price)), font=FONT_NAME)
            add_line("RSI 14: {:.1f}    SMA10: {}".format(
                float(technical["rsi14"]), format_market_value(technical["sma10"])),
                size=13, font=NUMERIC_FONT_NAME)
            add_line("SMA20: {}    ATR یک‌دقیقه‌ای: {}".format(
                format_market_value(technical["sma20"]),
                format_market_value(signal_atr)), size=13, font=FONT_NAME)
            if risk_atr is not None:
                add_line("ATR مبنای ریسک (۵ دقیقه‌ای): {}".format(
                    format_market_value(risk_atr)), size=13, font=FONT_NAME)
            if stop is not None and target is not None and stop > 0 and target > 0:
                add_line("حد ضرر تخمینی بر پایه ATR پنج‌دقیقه‌ای: {}".format(
                    format_market_value(stop)), font=FONT_NAME)
                add_line("هدف سود تخمینی بر پایه ATR پنج‌دقیقه‌ای: {}".format(
                    format_market_value(target)), font=FONT_NAME)
                add_line("نسبت سود به زیان هدف: 2 به 1", size=14)
            else:
                add_line("ATR پنج‌دقیقه‌ای معتبر نیست؛ حد ضرر و هدف تخمینی نمایش داده نمی‌شود.", size=13)
            add_line("این نتیجه فقط بر اساس کندل‌های بسته‌شده و شاخص‌های SMA، RSI و ATR است؛ تضمین سود نیست.",
                     size=12, height=dp(68), color=(1, 0.72, 0.35, 1))

        report_scroll = ScrollView(do_scroll_x=False)
        report_scroll.add_widget(report)
        body.add_widget(report_scroll)
        close_button = Button(markup=True, text=fa("بستن"), font_name=FONT_NAME,
                              size_hint_y=None, height=dp(46))
        body.add_widget(close_button)
        popup = Popup(title=fa_plain("تحلیل هوشمند بازار"), title_font=FONT_NAME,
                      content=body, size_hint=(0.94, 0.82))
        close_button.bind(on_press=popup.dismiss)
        popup.open()

    def open_risk_calculator(self, instance):
        body = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(8), size_hint_y=None)
        body.bind(minimum_height=body.setter("height"))
        fields = {}

        def add_field(key, label, value):
            row = BoxLayout(orientation="horizontal", spacing=dp(8),
                            size_hint_y=None, height=dp(52))
            label_widget = self.make_label(label, size=13, height=dp(48))
            label_widget.size_hint_x = 0.46
            label_widget.halign = "right"
            label_widget.bind(width=lambda widget, width: setattr(widget, "text_size", (width, None)))
            row.add_widget(label_widget)
            field = TextInput(
                text=value, multiline=False,
                font_name=NUMERIC_FONT_NAME, font_size=sp(15),
                size_hint_x=0.54, size_hint_y=None, height=dp(46),
                hint_text="0",
            )
            row.add_widget(field)
            body.add_widget(row)
            fields[key] = field

        # Keep field labels Persian-only; avoid mixed RTL/LTR unit strings.
        add_field("capital", "سرمایه به تتر", "{:.2f}".format(self.capital_usdt))
        add_field("entry", "قیمت ورود (تخمین دفتر سفارش)", format_market_value(self.latest_market_price) if self.latest_market_price else "")
        add_field("risk", "ریسک هر معامله", "1")
        add_field("stop", "فاصله حد ضرر", "1")
        add_field("leverage", "اهرم (حداکثر 3)", "3")
        add_field("fee", "کارمزد هر سمت (درصد)", "0.05")
        add_field("slippage", "لغزش هر سمت (درصد)", "0.02")

        result_box = BoxLayout(orientation="vertical", spacing=dp(5),
                               size_hint_y=None, height=dp(10 * 38 + 68 + 10 * 5))
        result_rows = {}

        def add_result_row(key, label):
            row = BoxLayout(orientation="horizontal", spacing=dp(8),
                            size_hint_y=None, height=dp(38))
            label_widget = self.make_label(label, size=13, height=dp(36))
            label_widget.size_hint_x = 0.60
            label_widget.halign = "right"
            label_widget.bind(width=lambda widget, width: setattr(widget, "text_size", (width, None)))
            value_widget = Label(
                text="—", font_name=NUMERIC_FONT_NAME, font_size=sp(13),                color=(0.95, 0.97, 1, 1), size_hint_x=0.40,
                size_hint_y=None, height=dp(36), halign="left",
                valign="middle",
            )
            value_widget.bind(width=lambda widget, width: setattr(widget, "text_size", (width, None)))
            row.add_widget(label_widget)
            row.add_widget(value_widget)
            result_box.add_widget(row)
            result_rows[key] = value_widget

        add_result_row("risk", "ریسک مجاز به تتر")
        add_result_row("quantity", "حجم تخمینی")
        add_result_row("notional", "ارزش موقعیت به تتر")
        add_result_row("margin", "وجه تضمین تقریبی")
        add_result_row("stop_buy", "حد ضرر خرید")
        add_result_row("stop_sell", "حد ضرر فروش")
        add_result_row("target_buy", "هدف خرید")
        add_result_row("target_sell", "هدف فروش")
        add_result_row("fees", "کارمزد رفت‌وبرگشت تخمینی")
        add_result_row("slippage", "هزینه لغزش تخمینی")
        result_note = self.make_label(
            "فرض اولیه مدل: کارمزد ۰٫۰۵٪ و لغزش ۰٫۰۲٪ هر سمت؛ نرخ واقعی را بررسی کنید. هزینه تأمین مالی لحاظ نشده است.",
            size=11, height=dp(68)
        )
        result_box.add_widget(result_note)
        body.add_widget(result_box)

        actions = BoxLayout(orientation="horizontal", spacing=dp(8),
                            size_hint_y=None, height=dp(46))
        calculate_button = Button(markup=True, text=fa("محاسبه"), font_name=FONT_NAME)
        close_button = Button(markup=True, text=fa("بستن"), font_name=FONT_NAME)
        actions.add_widget(calculate_button)
        actions.add_widget(close_button)
        body.add_widget(actions)

        risk_scroll = ScrollView(do_scroll_x=False, bar_width=dp(4))
        risk_scroll.add_widget(body)
        popup = Popup(title=fa_plain("مدیریت ریسک معامله"), title_font=FONT_NAME,
                      content=risk_scroll, size_hint=(0.98, 0.94), auto_dismiss=False)

        def calculate(_button):
            try:
                capital_value = float(normalize_digits(fields["capital"].text))
                result = calculate_position_risk(
                    capital_value,
                    normalize_digits(fields["risk"].text),
                    normalize_digits(fields["entry"].text),
                    normalize_digits(fields["stop"].text),
                    normalize_digits(fields["leverage"].text),
                    2.0,
                    fee_rate=float(normalize_digits(fields["fee"].text)) / 100.0,
                    slippage_rate=float(normalize_digits(fields["slippage"].text)) / 100.0,
                )
                # Save only a validated capital value; it will be prefilled next time.
                self.capital_usdt = capital_value
                try:
                    with open(self.settings_path, "w", encoding="utf-8") as settings_file:
                        json.dump({"capital_usdt": self.capital_usdt}, settings_file)
                except OSError:
                    pass
                result_rows["risk"].text = "{:.2f}".format(result["risk_amount"])
                result_rows["quantity"].text = format_market_value(result["quantity"])
                result_rows["notional"].text = "{:.2f}".format(result["notional"])
                result_rows["margin"].text = "{:.2f}".format(result["margin_estimate"])
                result_rows["stop_buy"].text = format_market_value(result["stop_price_long"])
                result_rows["stop_sell"].text = format_market_value(result["stop_price_short"])
                result_rows["target_buy"].text = format_market_value(result["target_price_long"])
                result_rows["target_sell"].text = format_market_value(result["target_price_short"])
                result_rows["fees"].text = "{:.4f}".format(result["fees_round_trip_estimate"])
                result_rows["slippage"].text = "{:.4f}".format(result["slippage_estimate"])
                if result["margin_estimate"] > capital_value:
                    result_note.text = fa(
                        "هشدار: وجه تضمین تخمینی از سرمایه واردشده بیشتر است؛ این حجم قابل اجرا نیست."
                    )
                elif result["liquidation_warning"]:
                    result_note.text = fa("هشدار: اهرم بالا خطر لیکویید شدن را افزایش می‌دهد.")
                else:
                    result_note.text = fa(
                        "ریسک شامل کارمزد و لغزش واردشده است؛ نرخ واقعی حساب را بررسی کنید. "
                        "هزینه تأمین مالی و تغییر نرخ کارمزد لحاظ نشده است."
                    )
            except (TypeError, ValueError, ZeroDivisionError):
                result_note.text = fa("ورودی نامعتبر است؛ مقادیر را بررسی کنید.")
                for value_widget in result_rows.values():
                    value_widget.text = "—"

        calculate_button.bind(on_press=calculate)
        close_button.bind(on_press=popup.dismiss)
        popup.open()

    def show_result(self, results):
        self.check_in_progress = False
        self.last_diagnostic_results = dict(results)
        # Preserve a compact snapshot for the read-only AI assistant. This is
        # diagnostic market data only; it contains no account credentials.
        if results.get("verified_symbols"):
            self.verified_market_symbols = set(results["verified_symbols"])
        self.last_market_snapshot = {
            key: results.get(key) for key in (
                "market_symbol", "symbol_verified", "depth_status", "depth_diagnostic",
                "best_bid", "best_ask", "mid_price", "market_price_source",
                "market_error", "market_info_error", "depth_http", "depth_content_type"
            ) if key in results
        }
        self.last_diagnostic_time = __import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self.check_button.disabled = False
        self.symbol_spinner.disabled = False
        self.check_button.text = fa("بررسی دوباره اتصال")
        website_status = results.get("website", results.get("website_error", "ناموفق"))
        # The ping endpoint is advisory only. Real market availability
        # is established by exchangeInfo plus a successfully parsed depth book.
        market_ok = (
            "depth_status" in results and 200 <= results["depth_status"] < 400
            and "mid_price" in results
            and bool(results.get("bids")) and bool(results.get("asks"))
        )
        # No documented public REST candle/ticker feed is available.
        # Keep all technical signals disabled until a verified trade-stream
        # candle builder is implemented and tested.
        self.latest_technical_analysis = None
        self.latest_market_price = results.get("mid_price") if market_ok else None
        self.analysis_signal.text = fa(
            "تحلیل تکنیکال غیرفعال است؛ API عمومی مستندشده تبدیل کندل ارائه نمی‌کند."
        )
        self.analysis_metrics.text = "No verified candle feed; midpoint is not a candle close"

        if market_ok:
            # Switch to Roboto only for actual ASCII numeric values.
            for value_label in (self.price_value, self.bid_value, self.ask_value):
                value_label.font_name = NUMERIC_FONT_NAME
            self.price_value.text = format_market_value(results["mid_price"])
            self.bid_value.text = format_market_value(results["best_bid"])
            self.ask_value.text = format_market_value(results["best_ask"])
        else:
            # Persian failure/placeholder text must never be rendered with Roboto.
            for value_label in (self.price_value, self.bid_value, self.ask_value):
                value_label.font_name = FONT_NAME
            self.price_value.text = fa("دریافت نشد")
            self.bid_value.text = "—"
            self.ask_value.text = "—"

        # Render valid depth data independently from website/ping status.
        # A parsed order book remains visible if an unrelated check fails.
        book_ok = bool(results.get("bids")) and bool(results.get("asks"))
        for row_buttons, levels in ((self.bid_rows, results.get("bids", [])),
                                    (self.ask_rows, results.get("asks", []))):
            for index, row_button in enumerate(row_buttons):
                if book_ok and index < len(levels):
                    level_price, level_qty = levels[index]
                    row_button.text = "{}       {}".format(
                        format_market_value(level_price), format_market_value(level_qty)
                    )
                    row_button.disabled = False
                    row_button.background_color = (0.10, 0.18, 0.20, 1) if row_buttons is self.bid_rows else (0.22, 0.13, 0.13, 1)
                else:
                    row_button.text = "--       --"
                    row_button.disabled = True
                    row_button.background_color = (0.10, 0.13, 0.18, 1)

        if market_ok:
            self.connection.text = fa("اتصال API بازار و دریافت دفتر سفارش برقرار است")
            self.connection.color = (0.35, 0.9, 0.55, 1)
            message = (
                "وضعیت سایت: {}\nپینگ بازار: {}\nاطلاعات بازار: {}\n"
                "دفتر سفارش: کد {}\nنماد معاملاتی: {}\n"
                "بهترین خرید: {}\nبهترین فروش: {}\n"
                "قیمت میانی تقریبی: {}"
            ).format(website_status, results.get("market_ping", "ناموفق؛ غیرمسدودکننده"),
                     results.get("market_info", "ناموفق؛ نماد انتخاب‌شده استفاده شد"),
                     results["depth_status"], results.get("market_symbol", "نامشخص"),
                     format_market_value(results["best_bid"]),
                     format_market_value(results["best_ask"]),
                     format_market_value(results["mid_price"]))
            popup_title = "دریافت داده بازار موفق"
        else:
            self.connection.text = fa("دریافت کامل داده بازار برقرار نشد")
            self.connection.color = (1, 0.55, 0.4, 1)
            message = "سایت: {}\nپینگ بازار: {}\nاطلاعات بازار: {}\nدفتر سفارش: {}\nجزئیات: {}".format(
                results.get("website", results.get("website_error", "ناموفق")),
                results.get("market_ping", "ناموفق (بررسی سلامت؛ غیرمسدودکننده)"),
                results.get("market_info", "ناموفق"),
                results.get("depth_status", "ناموفق"),
                results.get("market_error", results.get("website_error", "پاسخ کامل دریافت نشد")))
            popup_title = "گزارش اتصال"

        # Keep the main-page summary short. The detailed report belongs in
        # the popup; long mixed Persian/ASCII diagnostics clipped and
        # overlapped the safety notice on small screens.
        if market_ok and book_ok:
            self.analysis.text = fa(
                "تصویر فعلی دفتر سفارش دریافت شد.\n"
                "قیمت‌ها مربوط به همین پاسخ API هستند، نه جریان معاملات لحظه‌ای.\n"
                "گزارش کامل اتصال در پنجره جزئیات نمایش داده می‌شود."
            )
        else:
            self.analysis.text = fa(
                "دریافت کامل اطلاعات بازار موفق نبود.\n"
                "دفتر سفارش ممکن است به‌روزرسانی نشده باشد.\n"
                "برای دیدن علت، گزارش جزئیات را بررسی کنید."
            )

        body = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(10))

        # Keep the Persian summary and technical diagnostics in one scrollable
        # report. The previous fixed 145dp summary clipped long Persian text
        # and let the ASCII diagnostic overlap it on smaller Android screens.
        report_scroll = ScrollView(do_scroll_x=False, size_hint=(1, 1))
        report_content = BoxLayout(orientation="vertical", spacing=dp(12),
                                   size_hint_y=None, padding=[0, dp(4), 0, dp(8)])
        report_content.bind(minimum_height=report_content.setter("height"))

        # Avoid Latin acronyms/tickers inside the Arabic-script font. The
        # technical endpoint details below remain ASCII and use Roboto.
        message = message.replace("API پینگ", "پینگ بازار")
        message = message.replace("نماد BTCUSDT", "نماد معاملاتی بیت‌کوین")
        message_label = Label(
            text=fa(message), font_name=FONT_NAME, markup=True, font_size=sp(15),
            halign="center", valign="middle", size_hint=(1, None),            text_size=(Window.width * 0.80, None)
        )
        message_label.bind(texture_size=lambda widget, size: setattr(widget, "height", size[1] + dp(20)))
        report_content.add_widget(message_label)

        # Technical diagnostics are ASCII-only and therefore rendered with Roboto.
        diagnostic_parts = []
        for key in ("depth_diagnostic", "ping_error", "market_info_error", "market_error"):
            value = results.get(key)
            if value:
                diagnostic_parts.append("{}: {}".format(key, str(value)))
        for key in ("depth_http", "depth_content_type", "depth_url"):
            value = results.get(key)
            if value:
                diagnostic_parts.append("{}: {}".format(key, str(value)))
        diagnostic_text = "\n\n".join(diagnostic_parts) or "No detailed API diagnostic was returned."
        diagnostic_label = Label(
            text=diagnostic_text[:3500], font_name=NUMERIC_FONT_NAME, font_size=sp(11),
            color=(0.82, 0.88, 0.95, 1), halign="left", valign="top",
            size_hint=(1, None), text_size=(Window.width * 0.80, None)
        )
        diagnostic_label.bind(texture_size=lambda widget, size: setattr(widget, "height", size[1] + dp(16)))
        report_content.add_widget(diagnostic_label)
        report_scroll.add_widget(report_content)
        body.add_widget(report_scroll)

        close_button = Button(markup=True, text=fa("متوجه شدم"), font_name=FONT_NAME, size_hint_y=None, height=dp(46))
        body.add_widget(close_button)
        popup = Popup(title=fa_plain(popup_title), title_font=FONT_NAME, content=body,
                      size_hint=(0.94, 0.78), auto_dismiss=False)
        close_button.bind(on_press=popup.dismiss)
        popup.open()
        if market_ok and book_ok and results.get("symbol_verified"):
            self._start_order_book_polling()


    def open_diagnostics(self, instance):
        """Show an on-device health report and let the user copy it for support."""
        body = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(9))
        report_scroll = ScrollView(do_scroll_x=False)
        report = BoxLayout(orientation="vertical", spacing=dp(8), size_hint_y=None)
        report.bind(minimum_height=report.setter("height"))

        results = getattr(self, "last_diagnostic_results", {}) or {}
        auto_running = bool(self.auto_trader_thread and self.auto_trader_thread.is_alive())
        font_status = "OK: " + str(FONT_PATH) if FONT_PATH else "MISSING: Persian font"
        latin_status = "OK: " + str(LATIN_FONT_PATH) if LATIN_FONT_PATH else "MISSING: Latin font"
        depth_status = "OK" if results.get("bids") and results.get("asks") else "NOT VERIFIED"
        coordinator_status = "UNAVAILABLE"
        paper_state_status = "NOT FOUND"
        try:
            session = getattr(self, "coordinator_session", None)
            if session is not None:
                coordinator_state = session.snapshot()
                coordinator_status = (
                    "halted={}; reason={!r}; pending_intents={}".format(
                        coordinator_state.get("halted"),
                        coordinator_state.get("halt_reason"),
                        len(coordinator_state.get("intents", {}))
                        if isinstance(coordinator_state.get("intents"), dict) else "INVALID"
                    )
                )
            elif getattr(self, "coordinator_session_error", None):
                coordinator_status = "ERROR: " + str(self.coordinator_session_error)[:180]
        except Exception as coordinator_exc:
            coordinator_status = "ERROR: {}: {}".format(
                type(coordinator_exc).__name__, str(coordinator_exc)[:140]
            )
        try:
            paper_path = os.path.join(self.user_data_dir, "paper-engine-state.json")
            if os.path.isfile(paper_path):
                with open(paper_path, "r", encoding="utf-8") as paper_file:
                    paper_state = json.load(paper_file)
                if isinstance(paper_state, dict):
                    paper_state_status = "halted={}; reason={!r}; detail={!r}; has_position={}; closed_trades={}".format(
                        paper_state.get("halted"), paper_state.get("halt_reason"),
                        paper_state.get("halt_detail"), paper_state.get("position") is not None,
                        len(paper_state.get("closed_trades", []))
                        if isinstance(paper_state.get("closed_trades"), list) else "INVALID"
                    )
                else:
                    paper_state_status = "ERROR: invalid JSON state shape"
        except Exception as paper_exc:
            paper_state_status = "ERROR: {}: {}".format(
                type(paper_exc).__name__, str(paper_exc)[:140]
            )
        try:
            credential_status = CredentialStore(
                os.path.join(self.user_data_dir, "account-credentials.enc")
            ).check_status()
            credential_status = (
                "OK: encrypted credentials decrypt successfully"
                if credential_status == "SAVED"
                else "NOT CONFIGURED: no saved credentials"
            )
        except Exception as credential_exc:
            credential_status = "ERROR: " + type(credential_exc).__name__
        lines = [
            "مرکز عیب‌یابی برنامه",
            "نسخه برنامه: " + APP_VERSION,
            "سیستم: Android / Kivy",
            "فونت فارسی: " + font_status,
            "فونت لاتین: " + latin_status,
            "وضعیت بارگذاری فونت: " + (FONT_LOAD_ERROR or "OK"),
            "آزمایش راست‌به‌چپ: " + rtl_runtime_status(),
            "نماد انتخاب‌شده: " + str(self.current_symbol),
            "دفتر سفارش: " + depth_status,
            "هدف به‌روزرسانی دفتر سفارش: هر ۲ ثانیه؛ درخواست هم‌زمان تکراری اجرا نمی‌شود",
            "ثبت‌های موفق خودکار دفتر سفارش: " + str(getattr(self, "orderbook_update_count", 0)),
            "نمونه‌های نگهداری‌شده قیمت: " + str(len(getattr(self, "orderbook_price_history", []))),
            "آخرین ثبت موفق دفتر سفارش: " + str(getattr(self, "orderbook_last_success_at", "هنوز ثبت نشده")),
            "آخرین خطای دریافت خودکار دفتر سفارش: " + str(getattr(self, "orderbook_last_error", "") or "ندارد")[:180],
            "تعداد ردیف خرید: " + str(len(results.get("bids", []))),
            "تعداد ردیف فروش: " + str(len(results.get("asks", []))),
            "رشته پس‌زمینه معامله‌گر: " + ("ALIVE (سلامت جریان تأیید نشده)" if auto_running else "STOPPED"),
            "وضعیت پایدار هماهنگ‌کننده: " + coordinator_status,
            "وضعیت پایدار موتور آزمایشی: " + paper_state_status,
            "آخرین وضعیت موتور: " + str(getattr(self, "auto_worker_status", "هنوز گزارشی از موتور ثبت نشده"))[:240],
            "زمان آخرین وضعیت موتور: " + str(getattr(self, "auto_worker_status_time", "نامشخص")),
            "آخرین بررسی اتصال: " + str(getattr(self, "last_diagnostic_time", "نامشخص")),
            "وضعیت کلید امن API: " + credential_status,
        ]
        lines.extend(build_engine_health_report_lines(self))
        for key in ("website", "market_ping", "ping_error", "market_info", "depth_status",
                    "depth_http", "depth_content_type", "market_symbol",
                    "depth_diagnostic", "market_error", "analysis_error", "summary_error"):
            if key in results:
                lines.append("{}: {}".format(key, str(results[key])[:260]))
        if not results:
            lines.append("هنوز گزارش اتصال ثبت نشده است؛ ابتدا بررسی اتصال را اجرا کنید.")
        report_text = "\n".join(lines)

        # Render each Persian field name and its Latin/numeric value separately.
        # A single mixed-direction Label can select the Arabic font for ASCII
        # text too, producing tofu rectangles on some Android/Kivy devices.
        def render_report(extra_text=""):
            report.clear_widgets()
            display_lines = list(lines)
            if extra_text:
                display_lines.append(extra_text)
            for line in display_lines:
                if ": " in line:
                    field_name, field_value = line.split(": ", 1)
                    field_label = Label(
                        text=fa(field_name), font_name=FONT_NAME, markup=True,
                        font_size=sp(12), size_hint=(1, None),
                        text_size=(Window.width * 0.82, None),
                        halign="center", valign="middle",
                        color=(0.45, 0.78, 1, 1)
                    )
                    field_label.bind(texture_size=lambda widget, size: setattr(widget, "height", size[1] + dp(5)))
                    report.add_widget(field_label)
                    value_label = Label(
                        text=fa(field_value), font_name=font_for_text(field_value), markup=True,
                        font_size=sp(12), size_hint=(1, None),
                        text_size=(Window.width * 0.82, None),
                        halign="center", valign="middle",
                        color=(0.9, 0.94, 1, 1)
                    )
                    value_label.bind(texture_size=lambda widget, size: setattr(widget, "height", size[1] + dp(7)))
                    report.add_widget(value_label)
                else:
                    line_label = Label(
                        text=fa(line), font_name=font_for_text(line), markup=True,
                        font_size=sp(13), size_hint=(1, None),
                        text_size=(Window.width * 0.82, None),
                        halign="center", valign="middle",
                        color=(0.9, 0.94, 1, 1)
                    )
                    line_label.bind(texture_size=lambda widget, size: setattr(widget, "height", size[1] + dp(8)))
                    report.add_widget(line_label)

        render_report()
        report_scroll.add_widget(report)
        body.add_widget(report_scroll)

        actions = BoxLayout(orientation="horizontal", spacing=dp(8),
                            size_hint_y=None, height=dp(46))
        copy_button = Button(markup=True, text=fa("کپی گزارش"), font_name=FONT_NAME)
        check_button = Button(markup=True, text=fa("آزمایش دوباره"), font_name=FONT_NAME)
        close_button = Button(markup=True, text=fa("بستن"), font_name=FONT_NAME)
        actions.add_widget(copy_button)
        actions.add_widget(check_button)
        actions.add_widget(close_button)
        body.add_widget(actions)
        popup = Popup(title=fa_plain("مرکز عیب‌یابی"), title_font=FONT_NAME,
                      content=body, size_hint=(0.96, 0.82))
        def copy_report(_button):
            try:
                Clipboard.copy(report_text)
                render_report("گزارش کپی شد؛ آن را در گفتگو جای‌گذاری کنید.")
            except Exception as exc:
                render_report("کپی گزارش انجام نشد: " + str(exc)[:100])
        def rerun(_button):
            popup.dismiss()
            self.show_status(self.check_button)
        copy_button.bind(on_press=copy_report)
        check_button.bind(on_press=rerun)
        close_button.bind(on_press=popup.dismiss)
        popup.open()

    def open_multi_market_scan(self, instance):
        """Open a read-only scan of every active USDT futures order book."""
        body = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(8))
        scroll = ScrollView(do_scroll_x=False)
        self.multi_scan_status_label = Label(
            text=assistant_rtl_text(
                "اسکن بازار فقط دفتر سفارش نمادهای فعال را می‌خواند. "
                "از این داده‌ها سیگنال تکنیکال یا معامله ساخته نمی‌شود. "
                "برای شروع، دکمه اسکن را بزنید."
            ),
            font_name=FONT_NAME, font_size=sp(13), markup=False,
            base_direction="ltr", text_language="ar",
            size_hint=(1, None), height=dp(360),
            halign="center", valign="top",
            text_size=(Window.width - dp(60), None),
            color=(0.88, 0.92, 0.98, 1),
        )
        self.multi_scan_status_label.bind(
            texture_size=lambda widget, size: setattr(widget, "height", max(dp(360), size[1] + dp(24)))
        )
        scroll.add_widget(self.multi_scan_status_label)
        body.add_widget(scroll)
        controls = BoxLayout(orientation="horizontal", spacing=dp(8),
                             size_hint_y=None, height=dp(48))
        scan_button = Button(markup=True, text=fa("شروع اسکن"), font_name=FONT_NAME)
        close_button = Button(markup=True, text=fa("بستن"), font_name=FONT_NAME)
        controls.add_widget(scan_button)
        controls.add_widget(close_button)
        body.add_widget(controls)
        popup = Popup(title=fa_plain("اسکن چندنمادی بازار"),
                      title_font=FONT_NAME, content=body,
                      size_hint=(0.96, 0.82), auto_dismiss=False)
        self.multi_scan_popup = popup
        scan_button.bind(on_press=lambda _button: self.start_multi_market_scan())
        close_button.bind(on_press=popup.dismiss)
        popup.open()

    def _set_multi_scan_status(self, message):
        label = getattr(self, "multi_scan_status_label", None)
        if label is not None:
            label.text = assistant_rtl_text(message)
            label.font_name = FONT_NAME
            label.base_direction = "ltr"
            label.text_language = "ar"

    def start_multi_market_scan(self):
        """Run a bounded, read-only order-book scan off the UI thread."""
        if self.multi_scan_thread is not None and self.multi_scan_thread.is_alive():
            self._set_multi_scan_status("اسکن قبلی هنوز در حال اجراست؛ لطفاً صبر کنید.")
            return
        self._set_multi_scan_status(
            "در حال دریافت فهرست نمادهای فعال و دفتر سفارش آن‌ها... "
            "حداکثر ۴ درخواست هم‌زمان؛ هیچ سفارش واقعی ارسال نمی‌شود."
        )
        self.multi_scan_thread = threading.Thread(
            target=self._multi_market_scan_worker, daemon=True
        )
        self.multi_scan_thread.start()

    def _multi_market_scan_worker(self):
        try:
            result = scan_market_universe(max_workers=4)
            lines = [
                "گزارش اسکن چندنمادی بازار",
                "نمادهای فعال: {} | دفتر سفارش موفق: {} | خطا: {}".format(
                    result["active_count"], result["success_count"], result["error_count"]
                ),
                "نوع داده: تصویر لحظه‌ای دفتر سفارش؛ نه کندل تاریخی.",
                "سیگنال تکنیکال: غیرفعال برای این اسکن.",
                "معامله واقعی: غیرفعال؛ هیچ سفارشی ارسال نشده است.",
                "",
                "کمترین اختلاف خرید و فروش:",
            ]
            for item in result["snapshots"][:12]:
                lines.append(
                    "{} | bid {} | ask {} | spread {:.4f}%".format(
                        item["symbol"], item["best_bid"], item["best_ask"],
                        item["spread_pct"]
                    )
                )
            if result["errors"]:
                lines.extend(["", "نمونه خطاها:"])
                for item in result["errors"][:5]:
                    lines.append("{}: {}".format(item["symbol"], item["error"]))
            message = "\n".join(lines)
        except Exception as exc:
            message = "اسکن انجام نشد: {}: {}".format(
                type(exc).__name__, str(exc)[:220]
            )
        Clock.schedule_once(
            lambda _dt, value=message: self._set_multi_scan_status(value), 0
        )

    def open_auto_trader(self, instance):
        """Show an explicitly paper-only live-trade simulation controller."""
        body = BoxLayout(orientation="vertical", padding=dp(14), spacing=dp(10))
        symbol = self.current_symbol
        initial_status = (
            "شبیه‌ساز فقط برای نماد انتخاب‌شده کار می‌کند: " + symbol + "\n"
            "نمودار خطی با هر معامله معتبر به‌صورت زنده به‌روزرسانی می‌شود؛ برای دیدن نمودار نیازی به بسته‌شدن کندل نیست.\n"
            "تحلیل اندیکاتوری و ورود آزمایشی همچنان به ۲۳ کندل بسته و پیوسته نیاز دارد؛ تاریخچه معتبر در صورت دسترسی بازیابی می‌شود.\n"
            "نمودار زنده به‌تنهایی سیگنال یا مجوز معامله نیست. هیچ سفارش واقعی ارسال نمی‌شود."
        )
        # Shape Persian text once; keep Kivy's bidi direction LTR to avoid
        # a second RTL reorder in this popup on Android.
        self.auto_status_label = Label(
            text=assistant_rtl_text(initial_status),
            font_name=FONT_NAME,
            font_size=sp(13),
            markup=False,
            base_direction="ltr",
            text_language="ar",
            size_hint=(1, None),
            height=dp(160),
            halign="center",
            valign="middle",
            text_size=(Window.width - dp(56), None),
            color=(1, 0.82, 0.48, 1),
        )
        self.auto_status_label.bind(
            texture_size=lambda widget, size: setattr(widget, "height", max(dp(160), size[1] + dp(20)))
        )
        status_scroll = ScrollView(do_scroll_x=False, bar_width=dp(4),
                                   size_hint_y=None, height=dp(142))
        status_scroll.add_widget(self.auto_status_label)
        body.add_widget(status_scroll)
        body.add_widget(self.make_label("نمودار زنده قیمت (آخرین ۱۲۰ معامله)",
                                         size=13, height=dp(28),
                                         color=(0.35, 0.82, 1, 1)))
        self.live_price_label = self.make_label(
            "قیمت زنده: در انتظار اولین معامله معتبر", size=13, height=dp(28))
        self.live_price_chart = LivePriceChart(size_hint_y=None, height=dp(150))
        body.add_widget(self.live_price_label)
        body.add_widget(self.live_price_chart)
        history_button = Button(
            markup=True, text=fa("مشاهده تاریخچه معاملات انجام‌شده"),
            font_name=FONT_NAME, size_hint_y=None, height=dp(44),
            background_normal="", background_color=(0.12, 0.34, 0.42, 1),
        )
        history_button.bind(on_press=lambda _button: self.open_paper_trade_history())
        body.add_widget(history_button)
        rearm_button = Button(
            markup=True, text=fa("بازآماده‌سازی قفل آزمایش"),
            font_name=FONT_NAME, size_hint_y=None, height=dp(42),
        )
        rearm_button.bind(on_press=lambda _button: self.request_rearm_paper_runner())
        body.add_widget(rearm_button)
        controls = BoxLayout(orientation="horizontal", spacing=dp(7),
                             size_hint_y=None, height=dp(48))
        start_button = Button(markup=True, text=fa("شروع آزمایش"), font_name=FONT_NAME)
        stop_button = Button(markup=True, text=fa("توقف اضطراری"), font_name=FONT_NAME,
                             background_normal="", background_color=(0.58, 0.16, 0.16, 1))
        close_button = Button(markup=True, text=fa("بستن"), font_name=FONT_NAME)
        controls.add_widget(start_button)
        controls.add_widget(stop_button)
        controls.add_widget(close_button)
        body.add_widget(controls)
        popup = Popup(title=fa_plain("معامله‌گر آزمایشی زنده"),
                      title_font=FONT_NAME, content=body,
                      size_hint=(0.96, 0.78), auto_dismiss=False)
        self.auto_popup = popup
        start_button.bind(on_press=lambda _button: self.start_auto_paper_trader())
        stop_button.bind(on_press=lambda _button: self.stop_auto_paper_trader())
        close_button.bind(on_press=popup.dismiss)
        popup.open()
        if not getattr(self, "runtime_lock_ready", False):
            detail = getattr(self, "runtime_lock_error", None) or "قفل اجرا برقرار نیست."
            self.update_auto_status("شروع شبیه‌ساز مسدود شد؛ قفل ایمنی حافظه داخلی برقرار نشد. " + detail)
        elif (getattr(self, "auto_trader_thread", None) is not None
              and self.auto_trader_thread.is_alive()):
            # Preserve the actual startup/stream phase instead of replacing it
            # with a generic lock-ready message when the operator opens the panel.
            self.update_auto_status(getattr(
                self, "auto_worker_status",
                "شبیه‌ساز آزمایشی در حال اجراست؛ سفارش واقعی ارسال نمی‌شود."
            ))
        else:
            self.update_auto_status("قفل انحصاری اجرای برنامه در حافظه داخلی برقرار است؛ شبیه‌ساز فقط آزمایشی است و سفارش واقعی ارسال نمی‌کند.")

    def _persist_paper_engine_state(self, engine):
        """Durably persist the latest paper-only snapshot under one writer lock."""
        # Snapshot creation must happen inside the same lock as the file write.
        # Otherwise an older snapshot could overwrite a newer concurrent update.
        from paper_engine_state_store import atomic_write_json
        with self._paper_state_lock:
            state_path = os.path.join(self.user_data_dir, "paper-engine-state.json")
            atomic_write_json(state_path, engine.export_state())

    def _schedule_rearmed_paper_runner(self):
        """Start paper mode after explicit operator confirmation."""
        Clock.schedule_once(lambda _dt: self.start_auto_paper_trader(), 0.5)

    def request_rearm_paper_runner(self):
        """Offer confirmed recovery for eligible emergency halts; preserve all risk stops."""
        guard = getattr(self, "runtime_guard", None)
        session = getattr(self, "coordinator_session", None)
        worker = getattr(self, "auto_trader_thread", None)
        if worker is not None and worker.is_alive():
            self.update_auto_status("بازآماده‌سازی رد شد؛ ابتدا باید رشته شبیه‌ساز کاملاً متوقف شود.")
            return
        if guard is None or not guard.held or session is None:
            self.update_auto_status("بازآماده‌سازی ممکن نیست؛ قفل اجرا یا نشست ایمنی در دسترس نیست.")
            return
        try:
            state = session.snapshot()
            coordinator_halted = state.get("halted") is True
            if coordinator_halted and state.get("halt_reason") != "operator emergency stop":
                self.update_auto_status("بازآماده‌سازی رد شد؛ توقف هماهنگ‌کننده از نوع توقف دستیِ قابل‌بازیابی نیست.")
                return
            if state.get("intents") != {}:
                self.update_auto_status("بازآماده‌سازی رد شد؛ درخواست آماده‌سازی معلق وجود دارد.")
                return
            # A worker-side failure can persist EMERGENCY_STOP in the paper
            # engine without setting the coordinator halt. Permit recovery only
            # after explicit confirmation, with no position and above MAX_LOSS.
            paper_state_path = os.path.join(self.user_data_dir, "paper-engine-state.json")
            paper_engine = None
            if os.path.isfile(paper_state_path):
                with open(paper_state_path, "r", encoding="utf-8") as state_file:
                    paper_engine = PaperTradingEngine.restore_state(json.load(state_file))
                if paper_engine.position is not None:
                    self.update_auto_status(
                        "بازآماده‌سازی رد شد؛ ابتدا باید موقعیت آزمایشی باز بسته شود."
                    )
                    return
                if paper_engine.halted:
                    if paper_engine.halt_reason == "DAILY_MAX_LOSS":
                        if not paper_engine.daily_reset_available():
                            self.update_auto_status(
                                "قفل زیان روزانه هنوز فعال است؛ بازآماده‌سازی فقط پس از شروع روز UTC بعدی ممکن است."
                            )
                            return
                    elif paper_engine.halt_reason not in ("EMERGENCY_STOP", "DATA_GAP"):
                        self.update_auto_status(
                            "بازآماده‌سازی رد شد؛ توقف سقف زیان تجمعی یا قفل ایمنی قابل‌بازیابی نیست."
                        )
                        return
            paper_halted = paper_engine is not None and paper_engine.halted
            if not coordinator_halted and not paper_halted:
                self.update_auto_status("هیچ توقف قابل‌بازیابی فعالی وجود ندارد.")
                return
        except Exception as exc:
            # Keep the fail-closed behavior, but expose the concrete validation
            # reason so an operator can distinguish coordinator vs paper-state
            # corruption without weakening or bypassing either safety gate.
            detail = "{}: {}".format(type(exc).__name__, str(exc)[:180])
            self.update_auto_status(
                "خواندن وضعیت ایمنی ناموفق بود؛ قفل حفظ شد: " + detail
            )
            return

        body = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(10))
        if paper_engine is not None and paper_engine.halt_reason == "DAILY_MAX_LOSS":
            explanation = (
                "روز UTC جدید شروع شده و قفل زیان روزانه اکنون قابل بازبینی است.\n"
                "بازآماده‌سازی فقط پس از کنترل دوباره سقف زیان تجمعی و نبود موقعیت باز انجام می‌شود.\n"
                "اگر سقف زیان تجمعی هم فعال باشد، قفل حفظ خواهد شد.\n"
                "سرمایه مجازی و تاریخچه حفظ می‌شوند؛ هیچ سفارش واقعی ارسال نمی‌شود."
            )
        else:
            explanation = (
                "این کار فقط توقف اضطراریِ قابل‌بازیابی موتور آزمایشی را پس از بررسی دوباره ایمنی بازآماده می‌کند.\n"
                "توقف سقف زیان تجمعی، موقعیت باز، درخواست معلق یا توقف مبهم قابل بازنشانی نیست.\n"
                "سرمایه مجازی و تاریخچه حفظ می‌شوند؛ هیچ سفارش واقعی ارسال یا فعال نمی‌شود.\n"
                "پس از تأیید، شبیه‌ساز آزمایشی به‌صورت خودکار راه‌اندازی می‌شود."
            )
        label = Label(
            text=assistant_rtl_text(explanation), font_name=FONT_NAME,
            base_direction="ltr", text_language="ar", halign="center",
            valign="middle", size_hint=(1, 1),
        )
        body.add_widget(label)
        buttons = BoxLayout(orientation="horizontal", spacing=dp(8),
                            size_hint_y=None, height=dp(46))
        confirm = Button(markup=True, text=fa("تأیید بازآماده‌سازی"),
                         font_name=FONT_NAME)
        cancel = Button(markup=True, text=fa("انصراف"), font_name=FONT_NAME)
        buttons.add_widget(confirm)
        buttons.add_widget(cancel)
        body.add_widget(buttons)
        popup = Popup(title=fa_plain("تأیید بازآماده‌سازی ایمنی"),
                      title_font=FONT_NAME, content=body,
                      size_hint=(0.94, 0.55), auto_dismiss=False)
        cancel.bind(on_press=popup.dismiss)

        def confirm_rearm(_button):
            current_worker = getattr(self, "auto_trader_thread", None)
            if current_worker is not None and current_worker.is_alive():
                self.update_auto_status("بازآماده‌سازی رد شد؛ رشته شبیه‌ساز هنوز فعال است.")
                popup.dismiss()
                return
            rearmed = False
            paper_rearmed = False
            start_after_rearm = False
            try:
                # Re-read both durable states at confirmation time; the popup
                # may have remained open while another lifecycle event changed them.
                if not getattr(guard, "held", False):
                    raise RuntimeError("exclusive app runtime lock is not held")
                current_state = session.snapshot()
                if current_state.get("intents") != {}:
                    raise RuntimeError("pending prepared intents must be resolved before re-arming")
                if current_state.get("halted"):
                    if current_state.get("halt_reason") != "operator emergency stop":
                        raise RuntimeError("coordinator halt is not operator-rearmable")
                    session.rearm_after_operator_stop()
                    rearmed = True
                elif current_state.get("halt_reason") is not None:
                    raise RuntimeError("coordinator recovery metadata is inconsistent")
                # Reload the paper engine rather than trusting the snapshot
                # captured when the confirmation dialog was first opened.
                paper_state_path = os.path.join(self.user_data_dir, "paper-engine-state.json")
                paper_engine = None
                if os.path.isfile(paper_state_path):
                    with open(paper_state_path, "r", encoding="utf-8") as state_file:
                        paper_engine = PaperTradingEngine.restore_state(json.load(state_file))
                if paper_engine is not None and paper_engine.halted:
                    if paper_engine.position is not None:
                        raise RuntimeError("open paper position must be closed before re-arming")
                    if paper_engine.halt_reason == "EMERGENCY_STOP":
                        paper_engine.rearm_after_operator_stop()
                    elif paper_engine.halt_reason == "DAILY_MAX_LOSS":
                        paper_engine.rearm_after_daily_reset()
                        self._persist_paper_engine_state(paper_engine)
                        self.auto_engine = paper_engine
                        paper_rearmed = True
                    elif paper_engine.halt_reason == "DATA_GAP":
                        # Keep halted until fresh continuous multi-timeframe history is rebuilt.
                        self.auto_engine = paper_engine
                        paper_rearmed = True
                    else:
                        raise RuntimeError("paper engine halt is not operator-rearmable")
                if not rearmed and not paper_rearmed:
                    raise RuntimeError("no eligible persisted halt remains to re-arm")
                ensure_paper_runner_ready(guard, session)
                self.update_auto_status(
                    "توقف قابل‌بازیابی با تأیید شما بازآماده شد؛ راه‌اندازی خودکار شبیه‌ساز آزمایشی در حال انجام است. "
                    "سرمایه و تاریخچه حفظ شده‌اند و هیچ سفارش واقعی ارسال نمی‌شود."
                )
                start_after_rearm = True
            except Exception as exc:
                # If any post-rearm readiness check fails, restore the
                # persistent emergency stop before reporting failure. Never
                # leave a cleared halt behind after an unsuccessful re-arm.
                rollback_error = None
                if paper_rearmed:
                    try:
                        paper_engine.emergency_close()
                        self._persist_paper_engine_state(paper_engine)
                    except Exception as paper_rollback_exc:
                        rollback_error = paper_rollback_exc
                if rearmed:
                    try:
                        session.emergency_stop("operator emergency stop")
                    except Exception as rollback_exc:
                        rollback_error = rollback_exc
                detail = type(exc).__name__ + ": " + str(exc)[:120]
                if rollback_error is not None:
                    detail += " | rollback failed: " + type(rollback_error).__name__
                self.update_auto_status(
                    "بازآماده‌سازی انجام نشد؛ تلاش برای حفظ قفل ایمنی انجام شد: " + detail
                )
            popup.dismiss()
            if start_after_rearm:
                self._schedule_rearmed_paper_runner()

        confirm.bind(on_press=confirm_rearm)
        popup.open()

    def start_auto_paper_trader(self):
        """Start a one-symbol, foreground-only paper simulation; never live orders."""
        # Do not reset live telemetry when the operator taps Start again
        # while the existing worker is still running.
        if self.auto_trader_thread is not None and self.auto_trader_thread.is_alive():
            self.update_auto_status("شبیه‌ساز در حال اجراست؛ برای توقف از توقف اضطراری استفاده کنید.")
            return

        self.auto_health_lock = threading.RLock()
        self.auto_health = {
            "worker_phase": "starting", "websocket_connected": False,
            "connected_at": None, "retry_attempt": 0, "reconnect_count": 0,
            "received_messages": 0, "valid_market_trades": 0,
            "control_messages": 0,
            "selected_symbol_trade_count": 0,
            "last_any_market_trade_at": None,
            "last_selected_symbol_trade_at": None,
            "candle_count": 0, "builder_ready": False,
            "reconnect_partial_buckets_discarded": 0,
            "reconnect_history_clear_count": 0,
            "reconnect_last_reset_reason": "startup",
            "duplicate_sequence_rejections": 0,
            "older_sequence_rejections": 0,
            "out_of_order_rejections": 0,
            "analysis_required_candles": 23, "candle_interval_seconds": 60,
            "last_scan_at": None, "last_stream_error": "",
            "subscribed_symbols": [], "secondary_symbol_trade_counts": {},
            "candidate_count": 0, "candidate_symbols": [], "per_symbol_health": {},
        }
        guard = getattr(self, "runtime_guard", None)
        coordinator_session = getattr(self, "coordinator_session", None)
        try:
            ensure_paper_runner_ready(guard, coordinator_session)
        except Exception as exc:
            self._update_engine_health(
                worker_phase="blocked_by_safety",
                last_stream_error="{}: {}".format(type(exc).__name__, str(exc)[:120]),
            )
            self.update_auto_status(
                "شروع شبیه‌ساز مسدود شد؛ کنترل ایمنی وضعیت پایدار تأیید نشد: "
                + type(exc).__name__ + ": " + str(exc)[:120]
            )
            return
        symbol = str(self.current_symbol).upper()
        if not re.fullmatch(r"[A-Z0-9]+_USDT", symbol):
            self.update_auto_status("نماد انتخاب‌شده معتبر نیست؛ شروع انجام نشد.")
            return
        try:
            previous_builder = getattr(self, "auto_candle_builder", None)
            if previous_builder is not None and previous_builder.symbol == symbol:
                # Reuse in-memory candles only if the next trade confirms the same minute.
                builder = previous_builder
                builder.reset_after_reconnect()
            else:
                builder = TradeCandleBuilder(symbol, max_candles=2000)
            candle_cache_dir = os.path.join(self.user_data_dir, "candle-cache")
            candle_cache_path = os.path.join(candle_cache_dir, symbol + ".json")
            candle_archive_path = os.path.join(self.user_data_dir, "closed-candles.sqlite3")
            self.auto_candle_archive_path = candle_archive_path
            restored_candle_count = 0
            candle_cache_note = "هنوز تاریخچه کندل ذخیره‌شده‌ای وجود ندارد."
            if previous_builder is None or previous_builder.symbol != symbol:
                # SQLite is the source of truth. JSON is only a legacy import
                # path: merge any valid old rows first, then always restore the
                # newest contiguous run from the durable archive. A stale but
                # readable JSON file must never shadow newer archived candles.
                cache_import_note = ""
                try:
                    cached_rows = load_candle_cache(
                        candle_cache_path, symbol, int(time.time() * 1000),
                        interval_ms=builder.interval_ms, limit=builder.closed.maxlen,
                    )
                    if cached_rows:
                        record_closed_candles(
                            candle_archive_path, symbol, cached_rows,
                            interval_ms=builder.interval_ms,
                        )
                        cache_import_note = " کش قدیمی با آرشیو ادغام شد."
                except FileNotFoundError:
                    pass
                except Exception as cache_exc:
                    # Preserve a bad legacy cache for diagnosis, but never let
                    # it prevent recovery from the durable SQLite archive.
                    try:
                        archive_candle_cache(
                            candle_cache_path, "invalid_cache",
                            int(time.time() * 1000),
                        )
                    except (OSError, TypeError, ValueError):
                        pass
                    cache_import_note = (
                        " کش قدیمی قابل‌استفاده نبود ({})؛ آرشیو مستقل بررسی شد."
                    ).format(type(cache_exc).__name__)
                try:
                    archived_rows = load_latest_contiguous_candles(
                        candle_archive_path, symbol, int(time.time() * 1000),
                        interval_ms=builder.interval_ms, limit=builder.closed.maxlen,
                    )
                    if archived_rows:
                        restored_candle_count = builder.restore_closed_candles(archived_rows)
                        candle_cache_note = (
                            "تاریخچهٔ پیوسته از آرشیو دائمی بازیابی شد: {} کندل؛ "
                            "تأیید پیوستگی با جریان زنده الزامی است."
                        ).format(restored_candle_count) + cache_import_note
                    else:
                        durable_count = count_closed_candles(
                            candle_archive_path, symbol, builder.interval_ms
                        )
                        candle_cache_note = (
                            "آرشیو دائمی: {} کندل؛ اجرای تحلیل تا تشکیل تاریخچهٔ پیوستهٔ معتبر متوقف می‌ماند."
                        ).format(durable_count) + cache_import_note
                except Exception as archive_exc:
                    candle_cache_note = (
                        "بازیابی آرشیو دائمی ناموفق بود: {}؛ تحلیل غیرفعال می‌ماند."
                    ).format(type(archive_exc).__name__)
            self._update_engine_health(
                analysis_required_candles=int(builder.status().get("analysis_required_candles", 23)),
                candle_interval_seconds=max(1, int(builder.interval_ms // 1000)),
                candle_count=int(builder.status().get("closed_candles", 0)),
                builder_ready=bool(builder.is_ready),
                durable_candle_count=count_closed_candles(
                    candle_archive_path, symbol, builder.interval_ms
                ),
            )
            self.auto_candle_cache_path = candle_cache_path
            self.auto_candle_cache_last_open_time = (
                builder.closed[-1][0] if restored_candle_count else None
            )
            state_path = os.path.join(self.user_data_dir, "paper-engine-state.json")
            if os.path.isfile(state_path):
                with open(state_path, "r", encoding="utf-8") as state_file:
                    engine = PaperTradingEngine.restore_state(json.load(state_file))
                if engine.halted and engine.halt_reason != "DATA_GAP":
                    raise RuntimeError(
                        "موتور قبلاً متوقف شده است؛ علت: {}؛ جزئیات: {}. "
                        "راه‌اندازی خودکار پس از توقف اضطراری یا سقف زیان مجاز نیست؛ "
                        "وضعیت باید آگاهانه بررسی شود."
                        .format(engine.halt_reason, engine.halt_detail or "جزئیات ثبت نشده")
                    )
                if engine.halted and engine.halt_reason == "DATA_GAP" and engine.position is not None:
                    raise RuntimeError("DATA_GAP recovery requires a flattened paper position")
            else:
                engine = PaperTradingEngine(capital=self.capital_usdt, risk_percent=0.5,
                                            max_leverage=3, max_loss_percent=3.0)
            self._persist_paper_engine_state(engine)
        except Exception as exc:
            self._update_engine_health(
                worker_phase="blocked_by_persisted_state",
                last_stream_error="{}: {}".format(type(exc).__name__, str(exc)[:160]),
            )
            self.update_auto_status("راه‌اندازی موتور مسدود شد؛ وضعیت ذخیره‌شده معتبر یا قابل‌بازیابی نیست: "
                                    + type(exc).__name__ + ": " + str(exc)[:160])
            return
        self.auto_stop_event.clear()
        # Tabdeal broadcast is subscribed to exactly the selected symbol.
        # No other market is assumed to arrive on this socket.
        self.auto_engine = engine
        self.auto_candle_builder = builder
        self.auto_trader_thread = threading.Thread(
            target=self._paper_stream_worker,
            args=(symbol, builder, engine),
            daemon=True,
        )
        self.auto_trader_thread.start()
        retained_count = min(builder.status()["closed_candles"], 23)
        if retained_count >= 23:
            warmup_text = "تاریخچه معتبر: ۲۳ از ۲۳؛ تحلیل و ورود آزمایشی پس از تأیید اتصال مجاز است."
        elif retained_count >= 5:
            warmup_text = (
                "پایش اولیه فعال: {} از ۲۳ کندل بسته‌شده؛ تحلیل نهایی و ورود هنوز قفل است."
            ).format(retained_count)
        else:
            warmup_text = (
                "آماده‌سازی پایش: {} از ۵ کندل اولیه؛ هدف تحلیل معتبر ۲۳ کندل بسته‌شده است."
            ).format(retained_count)
        status_text = (
            "در حال اتصال به جریان معاملات " + symbol + ".\n"
            "حالت: فقط آزمایشی؛ سرمایه مجازی: {:.2f} USDT؛ ریسک هر معامله: حداکثر ۰٫۵٪؛ "
            "سقف زیان روزانه: ۱٪؛ سقف زیان تجمعی: ۳٪.\n"
            + warmup_text + "\n" + candle_cache_note
            + " هیچ سفارش واقعی ارسال نمی‌شود."
        ).format(self.capital_usdt)
        self.update_auto_status(status_text)

    def _stop_auto_worker(self, timeout=3.0):
        """Signal the paper worker and confirm it has exited before touching shared state."""
        self.auto_stop_event.set()
        ws = getattr(self, "auto_ws", None)
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
        worker = getattr(self, "auto_trader_thread", None)
        if worker is not None and worker.is_alive() and worker is not threading.current_thread():
            worker.join(timeout=max(0.0, float(timeout)))
        return worker is None or not worker.is_alive()

    def stop_auto_paper_trader(self):
        """Halt the paper engine immediately; worker joining is best-effort cleanup."""
        # First prevent new stream work and close the socket. Do not make the
        # simulated-position close conditional on the worker's join timeout:
        # emergency_close() serializes with process_scan() using the engine lock.
        worker_stopped = self._stop_auto_worker(timeout=0.0)
        # Persist the emergency halt in the cross-process coordinator first.
        # A paper-engine snapshot alone is insufficient: startup checks this
        # durable coordinator state before allowing the runner to start.
        coordinator_session = getattr(self, "coordinator_session", None)
        close_error = None
        if coordinator_session is None:
            close_error = "persistent coordinator session is unavailable"
        else:
            try:
                coordinator_session.emergency_stop("operator emergency stop")
            except Exception as exc:
                close_error = "coordinator halt: {}: {}".format(
                    type(exc).__name__, str(exc)[:100]
                )

        engine = getattr(self, "auto_engine", None)
        close_result = None
        if engine is not None:
            try:
                close_result = engine.emergency_close()
                self._persist_paper_engine_state(engine)
            except Exception as exc:
                engine_error = "paper state: {}: {}".format(
                    type(exc).__name__, str(exc)[:100]
                )
                close_error = (close_error + "; " + engine_error
                               if close_error else engine_error)

        # Give the worker a bounded opportunity to exit after the engine is
        # halted. A slow worker must never prevent closing/persisting the paper
        # position, and a halted engine rejects any later scan attempts.
        worker = getattr(self, "auto_trader_thread", None)
        if worker is not None and worker.is_alive() and worker is not threading.current_thread():
            worker.join(timeout=3.0)
        worker_stopped = worker is None or not worker.is_alive()
        if close_error:
            message = (
                "توقف اضطراری ثبت شد، اما ذخیره/بستن موقعیت مجازی خطا داد: {}. "
                "موتور ورود جدید را مسدود کرده است؛ وضعیت را بررسی کنید."
            ).format(close_error)
        elif close_result and close_result.get("event") == "CLOSED":
            trade = close_result["trade"]
            message = (
                "توقف اضطراری فعال شد؛ موقعیت مجازی در آخرین قیمت ثبت‌شده بسته و ذخیره شد. "
                "سود/زیان خالص: {:.4f} تتر. هیچ سفارش واقعی ارسال نشده است."
            ).format(trade["net_pnl"])
        else:
            message = (
                "توقف اضطراری فعال شد؛ موتور آزمایشی متوقف و وضعیت ذخیره شد. "
                "موقعیت مجازی بازی وجود نداشت. هیچ سفارش واقعی ارسال نشده است."
            )
        if not worker_stopped:
            message += " خروج رشته پس‌زمینه هنوز کامل نشده؛ موتور قفل و متوقف باقی می‌ماند."
        self.update_auto_status(message)

    def _update_engine_health(self, **values):
        """Publish worker telemetry; thread liveness alone is not engine health."""
        lock = getattr(self, "auto_health_lock", None)
        health = getattr(self, "auto_health", None)
        if lock is None or not isinstance(health, dict):
            return
        with lock:
            health.update(values)

    def _paper_stream_worker(self, symbol, builder, engine):
        """Reconnect public trade stream and simulate only after gap-free warm-up."""
        try:
            import websocket
        except Exception:
            Clock.schedule_once(
                lambda dt: self.update_auto_status("کتابخانه جریان معاملات در این نسخه در دسترس نیست."),
                0,
            )
            return

        # A persisted position may have crossed protective levels while the
        # process was stopped. Only its own market can reconcile that gap.
        recovered_open_symbol = (engine.position.get("symbol")
                                 if isinstance(engine.position, dict) else None)
        # The exchange broadcast accepts one plain-text subscription per
        # symbol on the same socket. Resolve active contracts first; cap this
        # rollout at two because that is the largest verified simultaneous set.
        verified_symbols = set(getattr(self, "verified_market_symbols", set()) or ())
        if not verified_symbols:
            try:
                response = requests.get(
                    FUTURES_API + "/exchangeInfo",
                    timeout=(4, 8),
                    headers={"Accept": "application/json"},
                )
                response.raise_for_status()
                info = response.json()
                rows = info.get("symbols") if isinstance(info, dict) else None
                if rows is None and isinstance(info, dict):
                    data = info.get("data")
                    rows = data if isinstance(data, list) else None
                    if rows is None and isinstance(data, dict):
                        rows = data.get("symbols")
                if isinstance(rows, list):
                    verified_symbols = set(list_market_symbols(rows))
            except Exception as exc:
                self._update_engine_health(
                    last_stream_error="subscription_symbol_discovery: {}: {}".format(
                        type(exc).__name__, str(exc)[:120]
                    )
                )
        subscription_symbols = select_broadcast_symbols(symbol, verified_symbols)
        # Keep a restored position market subscribed alongside the UI-selected
        # market; otherwise its protective stop/target could be left unmanaged.
        if recovered_open_symbol and recovered_open_symbol != symbol:
            active_verified = {
                value.strip().upper() for value in verified_symbols
                if isinstance(value, str)
            }
            if recovered_open_symbol in active_verified:
                subscription_symbols = include_required_symbol(
                    subscription_symbols, recovered_open_symbol
                )
        if recovered_open_symbol is not None and recovered_open_symbol not in subscription_symbols:
            self.auto_stop_event.set()
            self._update_engine_health(
                worker_phase="blocked_unsubscribed_open_position",
                subscribed_symbols=list(subscription_symbols),
                last_stream_error="recovered_position_symbol_not_subscribed:" + recovered_open_symbol,
            )
            Clock.schedule_once(
                lambda dt, value=recovered_open_symbol: self.update_auto_status(
                    "موقعیت آزمایشی بازیابی‌شده برای نماد {} باز است، اما این نماد در پنج اشتراک فعال نیست؛ "
                    "برای جلوگیری از رهاشدن موقعیت، شبیه‌ساز شروع نشد.".format(value)
                ), 0
            )
            return
        try:
            coordinator = MultiSymbolPaperCoordinator(
                subscription_symbols, analyze_klines, prepare_live_market_snapshot,
                builder_factory=lambda market: builder if market == symbol else TradeCandleBuilder(market, max_candles=2000),
                max_snapshot_age=30.0,
                analyze_with_context=lambda candles, now_ms: analyze_multi_timeframe(
                    candles, now_ms, analyze_klines
                ),
            )
        except Exception as coordinator_exc:
            self.auto_stop_event.set()
            self._update_engine_health(
                worker_phase="blocked_multi_symbol_setup",
                last_stream_error="{}: {}".format(type(coordinator_exc).__name__, str(coordinator_exc)[:140]),
            )
            Clock.schedule_once(
                lambda dt, value=type(coordinator_exc).__name__: self.update_auto_status(
                    "راه‌اندازی هماهنگ‌کنندهٔ نمادها ناموفق بود؛ موتور برای ایمنی شروع نشد: " + value
                ), 0
            )
            return
        self.auto_multi_symbol_coordinator = coordinator
        self._update_engine_health(
            subscribed_symbols=list(subscription_symbols),
            per_symbol_health=coordinator.status(),
        )
        market_event_watermark_ms = None

        def finalize_market_candles(watermark_ms):
            """Advance closes only from the validated market-wide event-time watermark."""
            finalized = coordinator.finalize_due(watermark_ms, grace_ms=30_000)
            selected_result = finalized.get(symbol, {})
            if selected_result.get("finalized") and builder.closed:
                try:
                    cache_rows = validate_closed_candles(
                        list(builder.closed), int(time.time() * 1000),
                        interval_ms=builder.interval_ms,
                        limit=builder.closed.maxlen,
                    )
                    if cache_rows:
                        durable_count = record_closed_candles(
                            self.auto_candle_archive_path, symbol, cache_rows,
                            interval_ms=builder.interval_ms,
                        )
                        save_candle_cache(
                            self.auto_candle_cache_path, symbol, cache_rows
                        )
                        self.auto_candle_cache_last_open_time = cache_rows[-1][0]
                        self._update_engine_health(durable_candle_count=durable_count)
                except Exception as cache_exc:
                    Clock.schedule_once(
                        lambda dt, name=type(cache_exc).__name__: self.update_auto_status(
                            "کندل بسته شد، اما ذخیره کش ناموفق بود: " + name
                        ), 0
                    )
            return finalized

        last_processed_bucket = None
        scan_state_lock = threading.Lock()
        scan_state = {"technical": None, "received_at": 0.0}
        last_progress_count = -1
        last_status_update = 0.0
        received_messages = 0
        valid_messages = 0
        control_messages = 0
        connected_once = False
        retry_attempt = 0
        total_reconnects = 0
        market_connection_state = {"opened_at": None}
        while not self.auto_stop_event.is_set():
            retry_attempt += 1
            self._update_engine_health(worker_phase="connecting",
                                       websocket_connected=False,
                                       retry_attempt=retry_attempt)
            attempt_delay = reconnect_backoff_seconds(retry_attempt)
            Clock.schedule_once(
                lambda dt, number=retry_attempt: self.update_auto_status(
                    "در حال تلاش برای اتصال به جریان معاملات؛ تلاش شماره {}. "
                    "مهلت هر تلاش ۱۲ ثانیه است؛ در صورت شکست، اتصال دوباره انجام می‌شود."
                    .format(number)
                ), 0
            )
            stream_watchdog = TradeStreamWatchdog(silence_timeout=45.0)
            watchdog_stop = threading.Event()
            # Keep the first transport/callback error so on_close cannot hide
            # the actionable cause behind a generic "no close reason" message.
            socket_diagnostics = {"opened": False, "error": None}

            def on_open(ws):
                nonlocal connected_once, last_processed_bucket, total_reconnects
                nonlocal recovered_open_symbol, market_event_watermark_ms
                market_event_watermark_ms = None
                socket_diagnostics["opened"] = True
                market_connection_state["opened_at"] = time.monotonic()
                stream_watchdog.connected()
                if connected_once:
                    total_reconnects += 1
                    # Any socket interruption invalidates the open position's
                    # price continuity. Its own first fresh tick must flatten it.
                    if isinstance(engine.position, dict):
                        recovered_open_symbol = engine.position.get("symbol")
                    coordinator.reset_after_reconnect()
                    last_processed_bucket = None
                connected_once = True
                self._update_engine_health(
                    worker_phase="connected_waiting_for_trade",
                    websocket_connected=True, connected_at=time.monotonic(),
                    # Preserve the last transport/close diagnostic across a
                    # successful reconnect; clearing it here hid the cause.
                    reconnect_count=total_reconnects,
                )
                # Match the previously observed working public-feed protocol:
                # send the market symbol as plain text after the socket opens.
                # This is a read-only market subscription, never an order action.
                try:
                    subscribe_broadcast_symbols(ws, subscription_symbols)
                except Exception as subscribe_exc:
                    detail = "{}: {}".format(
                        type(subscribe_exc).__name__, str(subscribe_exc)[:180]
                    )
                    socket_diagnostics["error"] = detail
                    self._update_engine_health(
                        worker_phase="reconnecting", websocket_connected=False,
                        last_stream_error="subscription_send_failed: " + detail,
                    )
                    raise
                Clock.schedule_once(
                    lambda dt: self.update_auto_status(
                        "اتصال برقرار شد: " + symbol + ". در حال بررسی پیوستگی داده‌ها؛ " 
                        "در صورت نبود شکاف، کندل‌های معتبر قبلی حفظ می‌شوند."
                    ), 0
                )

            def on_message(ws, message):
                nonlocal last_processed_bucket, last_progress_count
                nonlocal last_status_update, received_messages, valid_messages, control_messages, retry_attempt
                nonlocal recovered_open_symbol, market_event_watermark_ms
                received_messages += 1
                self._update_engine_health(received_messages=received_messages)
                # This socket is a market-wide broadcast. Parse each event's
                # own symbol first: activity in any market proves the socket is
                # alive, even when the selected low-volume symbol has no trade.
                parsed = parse_trade_message(message)
                if self.auto_stop_event.is_set():
                    return
                if not parsed.get("ok") and parsed.get("reason") == "non_trade_event":
                    control_messages += 1
                    self._update_engine_health(control_messages=control_messages)
                    now = time.monotonic()
                    if now - last_status_update >= 10:
                        last_status_update = now
                        status = (
                            "اتصال برقرار است؛ پیام‌های کنترلی/غیرمعاملاتی نادیده گرفته شدند. "
                            "دریافتی: {} | پیام کنترلی: {} | معامله معتبر: {}. "
                            "این پیام‌ها وارد کندل‌سازی نمی‌شوند."
                        ).format(received_messages, control_messages, valid_messages)
                        Clock.schedule_once(
                            lambda dt, value=status: self.update_auto_status(value), 0
                        )
                    return
                if not parsed.get("ok"):
                    now = time.monotonic()
                    if now - last_status_update >= 10:
                        last_status_update = now
                        status = (
                            "اتصال برقرار است، اما قالب پیام بازار معتبر نیست. "
                            "دریافتی: {} | معتبر: {} | علت آخر: {}. "
                            "ساخت کندل تا دریافت داده معتبر متوقف می‌ماند."
                        ).format(received_messages, valid_messages,
                                 str(parsed.get("reason", "unknown"))[:48])
                        Clock.schedule_once(
                            lambda dt, value=status: self.update_auto_status(value), 0
                        )
                    return
                # A structurally valid but old replay is not live market data.
                # Do not refresh the watchdog, tick-level exits, or scan freshness
                # from events more than 30 seconds behind the device clock.
                if not is_trade_timestamp_fresh(parsed.get("timestamp_ms")):
                    now = time.monotonic()
                    if now - last_status_update >= 10:
                        last_status_update = now
                        status = (
                            "پیام بازار دریافت شد، اما زمان آن بیش از ۳۰ ثانیه کهنه است؛ "
                            "برای جلوگیری از قیمت‌گذاری اشتباه نادیده گرفته شد."
                        )
                        Clock.schedule_once(
                            lambda dt, value=status: self.update_auto_status(value), 0
                        )
                    return
                valid_messages += 1
                self._update_engine_health(
                    valid_market_trades=valid_messages,
                    last_any_market_trade_at=time.monotonic(),
                )
                # Only a fresh, fully validated broadcast trade refreshes liveness.
                # Only the selected symbol may enter its candle builder.
                stream_watchdog.mark_valid_trade()
                # Accept only the exact symbols subscribed on this socket.
                # Each market has its own candle builder and technical snapshot.
                event_symbol = parsed.get("symbol")
                if event_symbol not in subscription_symbols:
                    return
                event_timestamp_ms = parsed.get("timestamp_ms")
                if isinstance(event_timestamp_ms, int) and not isinstance(event_timestamp_ms, bool):
                    market_event_watermark_ms = (
                        event_timestamp_ms if market_event_watermark_ms is None
                        else max(market_event_watermark_ms, event_timestamp_ms)
                    )
                    # A fresh subscribed-market trade advances the broadcast watermark.
                    # The 30-second grace tolerates cross-symbol delivery reordering.
                    finalize_market_candles(market_event_watermark_ms)
                if event_symbol != symbol:
                    counts = dict(
                        getattr(self, "auto_health", {}).get(
                            "secondary_symbol_trade_counts", {}
                        )
                    )
                    counts[event_symbol] = int(counts.get(event_symbol, 0)) + 1
                    self._update_engine_health(secondary_symbol_trade_counts=counts)
                    secondary_update = coordinator.on_trade(
                        parsed, received_at=time.monotonic()
                    )
                    if not secondary_update.get("accepted"):
                        return
                    recovered_gap = recovered_open_symbol == event_symbol
                    if recovered_gap:
                        recovered_open_symbol = None
                    try:
                        tick_event = coordinator.reconcile_tick(
                            engine, parsed,
                            gap_detected=(bool(secondary_update.get("gap_detected"))
                                          or recovered_gap),
                        )
                        if tick_event:
                            self._persist_paper_engine_state(engine)
                            trade = tick_event.get("trade")
                            if trade:
                                message_text = (
                                    "خروج حفاظتی موقعیت آزمایشی {}: علت={}؛ سود/زیان خالص={:.4f} USDT."
                                ).format(event_symbol, trade.get("reason", "UNKNOWN"),
                                         trade.get("net_pnl", 0.0))
                            else:
                                message_text = "موتور آزمایشی به دلیل رویداد حفاظتی متوقف شد."
                            if tick_event.get("halted"):
                                message_text += " موتور متوقف است: {}.".format(
                                    tick_event.get("halt_reason", "SAFETY")
                                )
                                self.auto_stop_event.set()
                            Clock.schedule_once(
                                lambda dt, value=message_text: self.update_auto_status(value), 0
                            )
                            if self.auto_stop_event.is_set():
                                ws.close()
                                return
                    except Exception as secondary_exc:
                        try:
                            engine.emergency_close(
                                "secondary tick reconciliation failure: {}".format(type(secondary_exc).__name__)
                            )
                            self._persist_paper_engine_state(engine)
                        except Exception:
                            pass
                        self.auto_stop_event.set()
                        try:
                            ws.close()
                        except Exception:
                            pass
                        Clock.schedule_once(
                            lambda dt, value=type(secondary_exc).__name__: self.update_auto_status(
                                "خطای مدیریت ریسک نماد دوم؛ موتور برای ایمنی متوقف شد: " + value
                            ), 0
                        )
                        return
                    self._update_engine_health(
                        per_symbol_health=coordinator.status(),
                        candidate_symbols=[
                            item["symbol"] for item in coordinator.candidate_snapshots()
                        ],
                    )
                    return
                # Render every fresh selected-symbol trade independently of
                # candle-builder acceptance/readiness. Duplicate sequence or
                # candle-order filtering must not freeze the live price chart.
                live_price = float(parsed["price"])
                Clock.schedule_once(
                    lambda _dt, value=live_price: self.update_live_price_chart(value), 0
                )
                selected_trade_count = int(
                    getattr(self, "auto_health", {}).get("selected_symbol_trade_count", 0)
                ) + 1
                self._update_engine_health(
                    selected_symbol_trade_count=selected_trade_count,
                    last_selected_symbol_trade_at=time.monotonic(),
                )
                update = coordinator.on_trade(parsed, received_at=time.monotonic())
                candle_health = builder.status()
                rejection_counts = candle_health.get("rejection_reasons", {})
                self._update_engine_health(
                    reconnect_partial_buckets_discarded=int(candle_health.get("partial_buckets_discarded", 0)),
                    reconnect_history_clear_count=int(candle_health.get("history_clear_count", 0)),
                    reconnect_last_reset_reason=str(candle_health.get("last_reset_reason", ""))[:80],
                    duplicate_sequence_rejections=int(rejection_counts.get("duplicate_sequence", 0)),
                    older_sequence_rejections=int(rejection_counts.get("older_sequence", 0)),
                    out_of_order_rejections=int(rejection_counts.get("out_of_order", 0)),
                )
                # A restored cache may support indicators while the first live
                # bucket is forming because that incomplete row is excluded.
                # At its close the builder discards it and signals a gap, so the
                # old cache is archived rather than joined across a missing minute.
                if update.get("gap_detected"):
                    # Keep the invalidated candles for diagnosis. They are
                    # moved out of the active cache so a later launch cannot
                    # mistake a known-discontinuous history for live continuity.
                    try:
                        archive_candle_cache(
                            getattr(self, "auto_candle_cache_path", ""),
                            "confirmed_gap", int(time.time() * 1000),
                        )
                    except (OSError, TypeError, ValueError):
                        # A failed archive must not affect market processing;
                        # the continuity gate still blocks use of stale rows.
                        pass
                    self.auto_candle_cache_last_open_time = None
                elif update.get("accepted") and builder.closed:
                    cached_open_time = builder.closed[-1][0]
                    if cached_open_time != getattr(self, "auto_candle_cache_last_open_time", None):
                        try:
                            cache_rows = validate_closed_candles(
                                list(builder.closed), int(time.time() * 1000),
                                interval_ms=builder.interval_ms,
                                limit=builder.closed.maxlen,
                            )
                            if len(cache_rows) >= 1:
                                durable_count = record_closed_candles(
                                    self.auto_candle_archive_path, symbol, cache_rows,
                                    interval_ms=builder.interval_ms,
                                )
                                save_candle_cache(
                                    self.auto_candle_cache_path, symbol, cache_rows
                                )
                                self.auto_candle_cache_last_open_time = cache_rows[-1][0]
                                self._update_engine_health(durable_candle_count=durable_count)
                        except Exception as cache_exc:
                            # Cache is an optimization only; a cache-write error
                            # must not corrupt paper-engine state or enable stale data.
                            now = time.monotonic()
                            if now - last_status_update >= 10:
                                last_status_update = now
                                Clock.schedule_once(
                                    lambda dt, name=type(cache_exc).__name__, detail=str(cache_exc)[:120]: self.update_auto_status(
                                        "ذخیره تاریخچه کندل ناموفق بود؛ تحلیل فقط با داده زنده معتبر ادامه می‌یابد: "
                                        + name + (": " + detail if detail else "")
                                    ), 0
                                )
                self._update_engine_health(
                    candle_count=int(builder.status().get("closed_candles", 0)),
                    builder_ready=bool(builder.is_ready),
                )
                if not update.get("accepted"):
                    now = time.monotonic()
                    if now - last_status_update >= 10:
                        last_status_update = now
                        status = (
                            "پیام معتبر دریافت شد، اما برای کندل‌سازی رد شد. "
                            "معتبر: {} | کندل بسته: {}/23 | علت: {}"
                        ).format(valid_messages, builder.status()["closed_candles"],
                                 str(update.get("reason", "unknown"))[:48])
                        Clock.schedule_once(
                            lambda dt, value=status: self.update_auto_status(value), 0
                        )
                    return
                candle_status = builder.status()
                if builder.is_ready:
                    selected_phase = "analysis_ready_waiting_for_scan"
                elif builder.monitor_ready:
                    selected_phase = "preliminary_monitoring"
                else:
                    selected_phase = "collecting_selected_symbol_candles"
                self._update_engine_health(
                    worker_phase=selected_phase,
                    candle_count=int(candle_status.get("closed_candles", 0)),
                    builder_ready=bool(builder.is_ready),
                )
                # Surface periodic stream and warm-up diagnostics while preserving
                # the v1.22 persistence, tick-level protection and signal gates.
                if not update.get("ready"):
                    now = time.monotonic()
                    if now - last_status_update >= 10:
                        last_status_update = now
                        stream_status = builder.status()
                        durable_total = count_closed_candles(
                            self.auto_candle_archive_path, symbol, builder.interval_ms
                        )
                        status_text = (
                            "جریان معاملات فعال است. کندل‌های ذخیره‌شده در آرشیو: {} | "
                            "کندل‌های پیوستهٔ معتبر برای تحلیل: {}/23 | "
                            "پیام دریافتی: {} | معتبر: {} | ردشده: {} | شکاف: {} | "
                            "علت ردها: {} | اتصال‌مجدد: {} | حذف کندل ناقص: {} | "
                            "پاک‌سازی تاریخچهٔ تحلیلی: {} | آخرین علت بازنشانی: {}. "
                            "آرشیو با شکاف داده پاک نمی‌شود؛ تحلیل فقط از تاریخچهٔ پیوسته استفاده می‌کند."
                        ).format(durable_total, stream_status["closed_candles"],
                                 received_messages, valid_messages,
                                 stream_status["rejected_trades"], stream_status["gaps"],
                                 ",".join("{}={}".format(k, v) for k, v in
                                          sorted(stream_status["rejection_reasons"].items()))
                                 or "ندارد", stream_status["reconnect_count"],
                                 stream_status["partial_buckets_discarded"],
                                 stream_status["history_clear_count"],
                                 stream_status["last_reset_reason"])
                        preliminary = builder.preliminary_trend()
                        if preliminary is not None:
                            trend_names = {"UP": "صعودی", "DOWN": "نزولی", "FLAT": "خنثی"}
                            status_text += (
                                " روند مقدماتی ۵ کندل: {} ({:+.3f}٪). "
                                "این فقط پایش است و سیگنال یا اجازه معامله نیست."
                            ).format(trend_names[preliminary["trend"]],
                                     preliminary["change_pct"])
                        Clock.schedule_once(
                            lambda dt, value=status_text: self.update_auto_status(value), 0
                        )
                    closed_count = int(update.get("closed_candles", 0))
                    if closed_count != last_progress_count and (
                            closed_count == 0 or closed_count % 5 == 0):
                        last_progress_count = closed_count
                        progress = (
                            "در حال آماده‌سازی موتور | {} | کندل بسته معتبر: {}/۲۳ | "
                            "{} | تحلیل و ورود آزمایشی فقط پس از ۲۳ کندل معتبر فعال می‌شود."
                        ).format(symbol, min(closed_count, 23),
                                 "پایش اولیه فعال است" if builder.monitor_ready else "در انتظار ۵ کندل اولیه")
                        Clock.schedule_once(
                            lambda dt, value=progress: self.update_auto_status(value), 0
                        )
                # A validated, fresh trade after a candle gap must still be
                # checked against an existing position's stop/target. The gap
                # blocks indicator analysis and new entries, not risk exits.
                if update.get("gap_detected"):
                    last_processed_bucket = None
                    status = builder.status()
                    gap_message = (
                        "شکاف زمانی در داده بازار شناسایی شد؛ آماده‌سازی از ابتدا انجام می‌شود. "
                        "کندل معتبر: {}/۲۳ | تعداد شکاف‌ها: {} | داده ردشده: {}"
                    ).format(status["closed_candles"], status["gaps"], status["rejected_trades"])
                    Clock.schedule_once(
                        lambda dt, value=gap_message: self.update_auto_status(value), 0
                    )
                # Every accepted, fresh trade may manage an open paper position,
                # including the first tick that reveals a candle-history gap.
                try:
                    recovered_gap = recovered_open_symbol == symbol
                    tick_event = coordinator.reconcile_tick(
                        engine, parsed,
                        gap_detected=(bool(update.get("gap_detected")) or recovered_gap),
                    )
                    if recovered_gap:
                        # Only this position's first accepted fresh tick can
                        # reconcile its unknown offline interval.
                        recovered_open_symbol = None
                    if tick_event:
                        self._persist_paper_engine_state(engine)
                        last_processed_bucket = parsed["timestamp_ms"] - parsed["timestamp_ms"] % 60_000
                        trade = tick_event["trade"]
                        tick_message = "خروج حفاظتی معامله آزمایشی: {}؛ سود/زیان خالص={:.4f} USDT.".format(trade["reason"], trade["net_pnl"])
                        if tick_event["halted"]:
                            tick_message += " موتور به‌دلیل توقف ایمنی متوقف شد: {}.".format(tick_event.get("halt_reason"))
                            self.auto_stop_event.set()
                        Clock.schedule_once(lambda dt, value=tick_message: self.update_auto_status(value), 0)
                        if self.auto_stop_event.is_set():
                            ws.close()
                            return
                except Exception as persist_exc:
                    try:
                        engine.emergency_close("tick persistence failure: {}".format(type(persist_exc).__name__))
                        self._persist_paper_engine_state(engine)
                    except Exception:
                        pass
                    self.auto_stop_event.set()
                    try:
                        ws.close()
                    except Exception:
                        pass
                    Clock.schedule_once(lambda dt, value=type(persist_exc).__name__: self.update_auto_status(
                        "خطای مدیریت موقعیت؛ موتور برای ایمنی متوقف شد: " + value), 0)
                    return
                # A gap invalidates indicators; after protective position handling,
                # stop here so no new entry can use discontinuous candle history.
                if update.get("gap_detected"):
                    return
                # The coordinator owns one independent builder and snapshot per
                # subscribed symbol. The UI's selected-symbol indicators remain
                # tied to the selected market only.
                technical = coordinator.states[symbol].get("latest_technical")
                if technical:
                    Clock.schedule_once(
                        lambda dt, value=dict(technical): self.update_technical_labels(value), 0
                    )
                elif coordinator.states[symbol].get("last_analysis_error"):
                    diagnostic = str(coordinator.states[symbol]["last_analysis_error"])[:180]
                    Clock.schedule_once(
                        lambda dt, value=diagnostic: self.update_auto_status(
                            "تحلیل نماد انتخاب‌شده ناموفق بود؛ ورود آن نماد مسدود است: " + value
                        ), 0
                    )
                self._update_engine_health(
                    per_symbol_health=coordinator.status(),
                    candidate_symbols=[
                        item["symbol"] for item in coordinator.candidate_snapshots()
                    ],
                )

            def on_error(ws, error):
                detail = "{}: {}".format(type(error).__name__, str(error)[:180])
                socket_diagnostics["error"] = detail
                self._update_engine_health(worker_phase="reconnecting",
                                           websocket_connected=False,
                                           last_stream_error=detail)
                Clock.schedule_once(
                    lambda dt, value=detail: self.update_auto_status(
                        "خطای جریان معاملات؛ تلاش برای اتصال مجدد. علت: " + value
                    ), 0
                )

            def on_close(ws, close_status_code, close_message):
                phase = "after_open" if socket_diagnostics["opened"] else "before_open"
                detail = "websocket_closed: phase={}; code={}; message={}".format(
                    phase, close_status_code,
                    str(close_message or "no close reason")[:100]
                )
                previous_error = socket_diagnostics.get("error")
                if previous_error:
                    detail += "; transport_error=" + str(previous_error)[:180]
                elif not socket_diagnostics["opened"]:
                    detail += "; transport_error=not_reported_by_websocket_client"
                self._update_engine_health(
                    worker_phase="reconnecting",
                    websocket_connected=False,
                    last_stream_error=detail,
                )
                if not self.auto_stop_event.is_set():
                    status_detail = "کد={}; پیام={}".format(
                        close_status_code, str(close_message or "بدون توضیح")[:100]
                    )
                    Clock.schedule_once(
                        lambda dt, value=detail: self.update_auto_status(
                            "جریان معاملات بسته شد؛ اتصال مجدد انجام می‌شود. " + value
                        ), 0
                    )

            scan_stop = threading.Event()

            def periodic_paper_scan():
                """Run scans independently of websocket callbacks, using only fresh snapshots."""
                last_scan = 0.0
                last_stale_notice = 0.0
                while not scan_stop.is_set():
                    if self.auto_stop_event.is_set():
                        return
                    # Wait the remaining part of the five-second interval rather
                    # than polling once per second (which made scans drift to 5–6s).
                    if last_scan > 0.0:
                        remaining = PAPER_SCAN_INTERVAL_SECONDS - (
                            time.monotonic() - last_scan
                        )
                        if remaining > 0.0 and scan_stop.wait(remaining):
                            return
                    now = time.monotonic()
                    market_status = coordinator.status(now=now)
                    # The socket watchdog observes the whole broadcast, so a
                    # busy secondary market must not hide silence on the market
                    # backing the one open paper position.
                    position = engine.position
                    if isinstance(position, dict):
                        position_symbol = position.get("symbol")
                        position_market = market_status.get(position_symbol, {})
                        position_age = position_market.get("last_trade_age_seconds")
                        if position_age is None:
                            opened_at = market_connection_state.get("opened_at")
                            position_stale = (
                                opened_at is not None and now - opened_at > 30.0
                            )
                        else:
                            position_stale = (
                                not isinstance(position_age, (int, float))
                                or position_age < 0 or position_age > 30.0
                            )
                        if position_stale:
                            current_position = engine.position
                            if (not isinstance(current_position, dict)
                                    or current_position.get("symbol") != position_symbol):
                                # A fresh tick may have closed the position while
                                # this scan thread was checking its market age.
                                position_stale = False
                        if position_stale:
                            try:
                                current_position = engine.position
                                stale_result = None
                                if (isinstance(current_position, dict)
                                        and current_position.get("symbol") == position_symbol
                                        and not engine.halted):
                                    # During an unobserved interval assume the
                                    # protective stop was hit; do not award a
                                    # hypothetical target or keep an unmanaged
                                    # paper position open.
                                    stale_result = engine.halt_for_data_gap(
                                        position_symbol, current_position.get("stop")
                                    )
                                    if stale_result is None and not engine.halted:
                                        engine.emergency_close(
                                            "position market stale; protective gap close failed"
                                        )
                                self._persist_paper_engine_state(engine)
                            except Exception as stale_exc:
                                try:
                                    engine.emergency_close(
                                        "position market stale: {}".format(
                                            type(stale_exc).__name__
                                        )
                                    )
                                    self._persist_paper_engine_state(engine)
                                except Exception:
                                    pass
                            self.auto_stop_event.set()
                            Clock.schedule_once(
                                lambda dt, market=position_symbol: self.update_auto_status(
                                    "دادهٔ نماد دارای موقعیت باز {} بیش از ۳۰ ثانیه تازه نشد؛ "
                                    "برای ایمنی، نتیجه با فرض برخورد به حد ضرر محافظه‌کارانه ثبت شد "
                                    "و شبیه‌ساز متوقف شد.".format(market)
                                ), 0
                            )
                            try:
                                app.close()
                            except Exception:
                                pass
                            return

                    candidates = coordinator.candidate_snapshots(now=now)
                    if not candidates:
                        if scan_stop.wait(0.25):
                            return
                        continue
                    preferred_symbol = (
                        engine.position.get("symbol") if isinstance(engine.position, dict) else symbol
                    )
                    technical = next(
                        (item for item in candidates if item.get("symbol") == preferred_symbol),
                        next((item for item in candidates if item.get("symbol") == symbol), candidates[0]),
                    )
                    age_value = market_status.get(technical["symbol"], {}).get("last_trade_age_seconds")
                    age = float(age_value) if isinstance(age_value, (int, float)) else 0.0
                    # Never open or manage a new position from an old market snapshot.
                    if age > 30.0:
                        if now - last_stale_notice >= 15.0:
                            last_stale_notice = now
                            Clock.schedule_once(
                                lambda dt, seconds=age: self.update_auto_status(
                                    "بررسی سیگنال به‌دلیل کهنگی داده متوقف شد؛ آخرین قیمت {} ثانیه قبل دریافت شده است. "
                                    "تا داده تازه برسد معامله‌ای باز نمی‌شود.".format(int(seconds))
                                ), 0
                            )
                        if scan_stop.wait(0.25):
                            return
                        continue
                    try:
                        if self.auto_stop_event.is_set():
                            return
                        # Recover DATA_GAP only after fresh continuous candles produce candidates.
                        if engine.halted and engine.halt_reason == "DATA_GAP":
                            if engine.position is not None or not builder.is_ready:
                                if scan_stop.wait(0.25):
                                    return
                                continue
                            engine.rearm_after_data_gap()
                            self._persist_paper_engine_state(engine)
                            self._update_engine_health(
                                worker_phase="data_gap_recovered_waiting_for_scan",
                                builder_ready=True,
                                candle_count=int(builder.status().get("closed_candles", 0)),
                            )
                            Clock.schedule_once(
                                lambda dt: self.update_auto_status(
                                    "تاریخچهٔ پیوسته و تازه آماده شد؛ توقف ناشی از شکاف داده رفع شد. "
                                    "موتور فقط در حالت آزمایشی ادامه می‌دهد."
                                ), 0
                            )
                        result = engine.process_scan(candidates)
                        self._persist_paper_engine_state(engine)
                        last_scan = time.monotonic()
                        self._update_engine_health(
                            worker_phase="scanning", last_scan_at=last_scan,
                            candle_count=int(builder.status().get("closed_candles", 0)),
                            builder_ready=bool(builder.is_ready),
                            candidate_count=len(candidates),
                            candidate_symbols=[item["symbol"] for item in candidates],
                            per_symbol_health=market_status,
                        )
                        events = result.get("events", [])
                        event_text = ""
                        if events:
                            event = events[-1]
                            if event.get("event") == "OPENED":
                                pos = event["position"]
                                event_text = (
                                    "معامله مجازی باز شد: {} {}؛ حد ضرر: {:.8g}؛ حد سود: {:.8g}؛ اهرم: {}x."
                                ).format(pos["symbol"], "خرید" if pos["side"] == "BULLISH" else "فروش",
                                         pos["stop"], pos["target"], pos["leverage"])
                            elif event.get("event") == "CLOSED":
                                trade = event["trade"]
                                event_text = "معامله مجازی بسته شد: علت={}؛ سود/زیان خالص={:.4f} USDT.".format(
                                    trade["reason"], trade["net_pnl"]
                                )
                        if result.get("halted"):
                            event_text += " سقف زیان تجمعی فعال شد؛ موتور متوقف است."
                            self.auto_stop_event.set()
                            try:
                                app.close()
                            except Exception:
                                pass
                        message_text = (
                            "شبیه‌ساز فعال | {}\nسیگنال آخر: {} | قیمت: {:.8g}\n"
                            "آخرین داده: {:.0f} ثانیه قبل | سرمایه مجازی: {:.4f} USDT | معاملات بسته: {}\n"
                            "وضعیت ورود: {}\nحداکثر ریسک هر معامله ۰٫۵٪؛ اهرم حداکثر ۳ برابر.\n{}"
                        ).format(technical["symbol"], technical["signal"], technical["price"], age,
                                 result["equity"], result["closed_trades"],
                                 diagnose_signal(technical), event_text)
                        Clock.schedule_once(
                            lambda dt, value=message_text: self.update_auto_status(value), 0
                        )
                    except Exception as scan_exc:
                        try:
                            engine.emergency_close("periodic scan failure: {}".format(type(scan_exc).__name__))
                            self._persist_paper_engine_state(engine)
                        except Exception:
                            pass
                        self.auto_stop_event.set()
                        try:
                            app.close()
                        except Exception:
                            pass
                        Clock.schedule_once(
                            lambda dt, value=type(scan_exc).__name__: self.update_auto_status(
                                "خطای بررسی دوره‌ای؛ موتور برای ایمنی متوقف شد: " + value
                            ), 0
                        )
                        return

            # Construct the socket before starting the scan worker. The worker
            # may need to close it immediately on a persistence or scan failure.
            # Starting the thread first created a race where app was not yet
            # bound when the exception handler attempted app.close().
            app = websocket.WebSocketApp(
                BROADCAST_URL, on_open=on_open, on_message=on_message,
                on_error=on_error, on_close=on_close,
            )
            self.auto_ws = app

            scan_thread = threading.Thread(
                target=periodic_paper_scan, daemon=True
            )
            scan_thread.start()

            def monitor_stream_silence():
                # A connected socket can remain open while no valid trades arrive.
                # Also monitor the open position's own market: activity in the
                # other subscribed symbol must not hide a silent position market.
                nonlocal recovered_open_symbol
                while not watchdog_stop.wait(5.0):
                    if self.auto_stop_event.is_set():
                        return
                    position = engine.position
                    if isinstance(position, dict):
                        position_symbol = position.get("symbol")
                        position_market = coordinator.status().get(position_symbol, {})
                        position_age = position_market.get("last_trade_age_seconds")
                        if (not isinstance(position_age, (int, float))
                                or position_age > 30.0):
                            recovered_open_symbol = position_symbol
                            Clock.schedule_once(
                                lambda dt, market=position_symbol, age=position_age: self.update_auto_status(
                                    "داده نماد دارای موقعیت باز ({}) بیش از ۳۰ ثانیه تازه نیست؛ "
                                    "برای حفاظت، اتصال بازنشانی می‌شود و موقعیت در اولین قیمت تازه همان نماد بسته خواهد شد. "
                                    "سن آخرین داده: {}.".format(market, "ناموجود" if age is None else int(age))
                                ), 0
                            )
                            try:
                                app.close()
                            except Exception:
                                pass
                            return
                    if stream_watchdog.is_stale():
                        silent_for = stream_watchdog.seconds_since_valid_trade()
                        Clock.schedule_once(
                            lambda dt, value=silent_for: self.update_auto_status(
                                "بیش از ۴۵ ثانیه معامله معتبر دریافت نشد؛ جریان برای اتصال مجدد بسته می‌شود. "
                                "مدت سکوت: {:.0f} ثانیه.".format(value or 0.0)
                            ), 0
                        )
                        try:
                            app.close()
                        except Exception:
                            pass
                        return

            watchdog_thread = threading.Thread(
                target=monitor_stream_silence, daemon=True
            )
            watchdog_thread.start()
            try:
                # Do not change socket.setdefaulttimeout globally here. A short
                # process-wide default can also become the live WebSocket read
                # timeout and close an otherwise healthy, quiet market stream.
                # websocket-client 1.8.0 has no run_forever(timeout=...) argument;
                # ping/pong plus the validated-trade watchdog provide liveness.
                app.run_forever(
                    ping_interval=20,
                    ping_timeout=10,
                    reconnect=0,
                    sslopt=tabdeal_websocket_ssl_options(certifi.where()),
                )
            except Exception as stream_exc:
                detail = "{}: {}".format(type(stream_exc).__name__, str(stream_exc)[:140])
                self._update_engine_health(
                    worker_phase="reconnecting", websocket_connected=False,
                    last_stream_error=detail,
                )
                Clock.schedule_once(
                    lambda dt, value=detail: self.update_auto_status(
                        "خطای اجرای جریان معاملات؛ تلاش مجدد. علت: " + value
                    ), 0
                )
            finally:
                self._update_engine_health(
                    websocket_connected=False,
                    worker_phase=("stopping" if self.auto_stop_event.is_set() else "reconnecting"),
                )
                scan_stop.set()
                scan_thread.join(timeout=2.0)
                watchdog_stop.set()
                watchdog_thread.join(timeout=1.0)
                self.auto_ws = None
            # Reset backoff only after a genuinely stable session. Resetting it
            # on every single trade caused short-lived sockets to retry forever
            # at the minimum delay, amplifying transient Android DNS failures.
            opened_at = market_connection_state.get("opened_at")
            if opened_at is not None and time.monotonic() - opened_at >= 60.0:
                retry_attempt = 0
            if not self.auto_stop_event.is_set():
                Clock.schedule_once(
                    lambda dt: self.update_auto_status(
                        "اتصال قطع شد؛ تلاش مجدد پس از مکث کوتاه. پس از اتصال، " 
                        "پیوستگی کندل‌ها بررسی می‌شود؛ فقط در صورت وجود شکاف واقعی " 
                        "گرم‌سازی کامل از نو انجام خواهد شد."
                    ), 0
                )
                self.auto_stop_event.wait(attempt_delay)

        self._update_engine_health(worker_phase="stopped", websocket_connected=False)
        final_status = (
            "شبیه‌ساز متوقف شد. هیچ سفارش واقعی ارسال نشده است. "
            "برای جزئیات، پیام آخر وضعیت موتور را بررسی کنید."
        )
        Clock.schedule_once(
            lambda dt, value=final_status: self.update_auto_status(value), 0
        )

    def update_live_price_chart(self, price):
        """Update the rolling chart on Kivy's UI thread from validated live trades."""
        chart = getattr(self, "live_price_chart", None)
        if chart is not None:
            chart.add_price(price)
        label = getattr(self, "live_price_label", None)
        if label is not None:
            try:
                label.text = "قیمت زنده: {:.8g} USDT".format(float(price))
            except (TypeError, ValueError, OverflowError):
                pass

    def update_technical_labels(self, technical):
        """Publish verified closed-candle indicators to the main analysis panel."""
        self.latest_technical_analysis = dict(technical)
        signal = technical.get("signal", "NEUTRAL")
        labels = {"BULLISH": "صعودی", "BEARISH": "نزولی", "NEUTRAL": "خنثی"}
        if getattr(self, "analysis_signal", None) is not None:
            self.analysis_signal.text = fa("سیگنال کندل بسته‌شده: " + labels.get(signal, "خنثی"))
        if getattr(self, "analysis_metrics", None) is not None:
            self.analysis_metrics.text = (
                "SMA10: {:.6g} | SMA20: {:.6g} | RSI14: {:.2f} | ATR14: {:.6g}"
            ).format(technical["sma10"], technical["sma20"],
                     technical["rsi14"], technical["atr14"])

    def open_paper_trade_history(self):
        """Display persisted paper trades read-only; never changes trading state."""
        body = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(8))
        scroll = ScrollView(do_scroll_x=False, bar_width=dp(4))
        rows = BoxLayout(orientation="vertical", spacing=dp(8), size_hint_y=None)
        rows.bind(minimum_height=rows.setter("height"))

        def add_row(message, height=dp(94), color=(0.88, 0.93, 0.98, 1)):
            label = Label(
                text=assistant_rtl_text(message), font_name=FONT_NAME,
                font_size=sp(12), markup=False, base_direction="ltr",
                text_language="ar", size_hint_y=None, height=height,
                halign="center", valign="middle",
                text_size=(Window.width - dp(64), None), color=color,
            )
            label.bind(texture_size=lambda widget, size: setattr(
                widget, "height", max(height, size[1] + dp(16))
            ))
            rows.add_widget(label)

        def number(value, default="—"):
            try:
                parsed = float(value)
                if not math.isfinite(parsed):
                    return default
                return "{:.8g}".format(parsed)
            except (TypeError, ValueError, OverflowError):
                return default

        state_path = os.path.join(self.user_data_dir, "paper-engine-state.json")
        try:
            with open(state_path, "r", encoding="utf-8") as state_file:
                state = json.load(state_file)
            if not isinstance(state, dict):
                raise ValueError("invalid paper state shape")
            trades = state.get("closed_trades", [])
            if not isinstance(trades, list):
                raise ValueError("invalid closed-trades history")
        except FileNotFoundError:
            trades = []
            state = {}
            add_row("هنوز فایل تاریخچه معاملات آزمایشی ایجاد نشده است.", dp(72),
                    (1, 0.72, 0.35, 1))
        except Exception as exc:
            trades = []
            state = {}
            add_row("خواندن تاریخچه ممکن نشد؛ اطلاعات دست‌نخورده باقی مانده است. علت: {}".format(
                type(exc).__name__
            ), dp(90), (1, 0.55, 0.42, 1))

        if isinstance(state, dict) and "closed_trades" in state:
            add_row("تعداد معاملات بسته‌شده ذخیره‌شده: {}".format(len(trades)), dp(44),
                    (0.35, 0.82, 1, 1))
            summary = summarize_closed_trades(trades, limit=10)
            factor = ("بدون زیان" if summary["profit_factor"] is None
                      else number(summary["profit_factor"]))
            add_row(
                "خلاصه ۱۰ معامله آخر (رکوردهای معتبر)\n"
                "برد: {} | باخت: {} | سربه‌سر: {} | نرخ برد: {}٪\n"
                "خالص: {} USDT | میانگین معامله: {} USDT\n"
                "میانگین برد: {} | میانگین باخت: {} | ضریب سود: {}\n"
                "کارمزد ورود و خروج: {} USDT".format(
                    summary["wins"], summary["losses"], summary["breakeven"],
                    number(summary["win_rate_percent"]), number(summary["net_pnl"]),
                    number(summary["average_net_pnl"]), number(summary["average_win"]),
                    number(summary["average_loss"]), factor,
                    number(summary["fees_total"])
                ), dp(132), (0.35, 0.82, 1, 1)
            )
            reason_text = " | ".join(
                "{}: {}".format(reason, count)
                for reason, count in sorted(summary["reasons"].items())
            ) or "ثبت نشده"
            add_row("علت خروج در ۱۰ معامله آخر: " + reason_text, dp(58),
                    (0.72, 0.80, 0.90, 1))
            side_stats = summary["sides"]
            add_row(
                "خرید: {} معامله | برد: {} | خالص: {} USDT\n"
                "فروش: {} معامله | برد: {} | خالص: {} USDT".format(
                    side_stats["BULLISH"]["count"], side_stats["BULLISH"]["wins"],
                    number(side_stats["BULLISH"]["net_pnl"]),
                    side_stats["BEARISH"]["count"], side_stats["BEARISH"]["wins"],
                    number(side_stats["BEARISH"]["net_pnl"])
                ), dp(78), (0.72, 0.80, 0.90, 1)
            )
            if not trades:
                add_row("هنوز معامله بسته‌شده‌ای ثبت نشده است.", dp(60))
            for index, trade in enumerate(reversed(trades[-100:]), 1):
                if not isinstance(trade, dict):
                    add_row("رکورد شماره {} نامعتبر است و نمایش داده نشد.".format(index),
                            dp(60), (1, 0.55, 0.42, 1))
                    continue
                side = "خرید" if trade.get("side") == "BULLISH" else (
                    "فروش" if trade.get("side") == "BEARISH" else "نامشخص"
                )
                pnl = number(trade.get("net_pnl"))
                pnl_color = (0.30, 0.88, 0.58, 1)
                try:
                    if float(trade.get("net_pnl")) < 0:
                        pnl_color = (1, 0.48, 0.42, 1)
                except (TypeError, ValueError, OverflowError):
                    pass
                duration_value = trade.get("duration_seconds")
                try:
                    duration_number = float(duration_value)
                    duration_text = (
                        "{:.1f} ثانیه".format(duration_number)
                        if math.isfinite(duration_number) and duration_number >= 0
                        else "نامشخص"
                    )
                except (TypeError, ValueError, OverflowError):
                    duration_text = "نامشخص (رکورد قدیمی)"
                details = (
                    "معامله بسته‌شده {} | {} {}\n"
                    "ورود: {} | خروج اجراشده: {} | مقدار: {}\n"
                    "قیمت برخورد: {} | حد ضرر: {} | حد سود: {}\n"
                    "مدت نگهداری: {} | اهرم: {}x\n"
                    "ناخالص: {} | کارمزد ورود/خروج: {}/{}\n"
                    "سود/زیان خالص: {} USDT | علت خروج: {}"
                ).format(
                    index, trade.get("symbol", "—"), side,
                    number(trade.get("entry")), number(trade.get("exit")),
                    number(trade.get("quantity")),
                    number(trade.get("exit_trigger_price")),
                    number(trade.get("stop")), number(trade.get("target")),
                    duration_text, number(trade.get("leverage")),
                    number(trade.get("gross_pnl")), number(trade.get("fees_entry")),
                    number(trade.get("fees_exit")), pnl, trade.get("reason", "—")
                )
                add_row(details, dp(154), pnl_color)
            position = state.get("position")
            if isinstance(position, dict):
                side = "خرید" if position.get("side") == "BULLISH" else "فروش"
                add_row(
                    "موقعیت آزمایشی باز (جزو معاملات بسته‌شده نیست)\n"
                    "{} {} | ورود: {} | قیمت آخر: {}\n"
                    "حد ضرر: {} | حد سود: {} | اهرم: {}x".format(
                        position.get("symbol", "—"), side,
                        number(position.get("entry")), number(position.get("last_price")),
                        number(position.get("stop")), number(position.get("target")),
                        number(position.get("leverage"))
                    ),
                    dp(100), (1, 0.78, 0.35, 1)
                )
            add_row(
                "توجه: فایل فعلی برای معاملات آزمایشی زمان ورود و خروج را ذخیره نمی‌کند؛ "
                "بنابراین زمان برای سوابق قدیمی نمایش داده نمی‌شود. این صفحه فقط‌خواندنی است.",
                dp(76), (0.72, 0.80, 0.90, 1)
            )

        scroll.add_widget(rows)
        body.add_widget(scroll)
        close_button = Button(
            markup=True, text=fa("بستن"), font_name=FONT_NAME,
            size_hint_y=None, height=dp(44)
        )
        body.add_widget(close_button)
        popup = Popup(
            title=fa_plain("تاریخچه معاملات آزمایشی"),
            title_font=FONT_NAME, content=body,
            size_hint=(0.96, 0.84), auto_dismiss=False
        )
        close_button.bind(on_press=popup.dismiss)
        popup.open()

    def update_auto_status(self, message):
        # Keep the actual worker phase for diagnostics. A live Python thread
        # alone does not prove that the exchange stream is connected or healthy.
        self.auto_worker_status = str(message)
        self.auto_worker_status_time = time.strftime("%Y-%m-%d %H:%M:%S")
        label = getattr(self, "auto_status_label", None)
        if label is not None:
            label.text = assistant_rtl_text(message)
            label.font_name = font_for_text(message)
            label.base_direction = "ltr"
            label.text_language = "ar"

    def _flush_candle_archive(self):
        """Idempotently flush every already-closed in-memory candle to SQLite."""
        builder = getattr(self, "auto_candle_builder", None)
        archive_path = getattr(self, "auto_candle_archive_path", None)
        if builder is None or not archive_path:
            return 0
        rows = list(getattr(builder, "closed", ()) or ())
        if not rows:
            return count_closed_candles(
                archive_path, builder.symbol, builder.interval_ms
            )
        count = record_closed_candles(
            archive_path, builder.symbol, rows,
            interval_ms=builder.interval_ms,
        )
        # JSON remains a disposable compatibility cache; SQLite is committed
        # first so a cache-write failure cannot lose the durable candle history.
        cache_path = getattr(self, "auto_candle_cache_path", None)
        if cache_path:
            try:
                save_candle_cache(cache_path, builder.symbol, rows)
            except Exception:
                pass
        self._update_engine_health(durable_candle_count=count)
        return count

    def on_pause(self):
        # Android sends on_pause when the user switches apps or the screen is
        # backgrounded. Returning True lets Kivy resume this app instead of
        # treating the pause as a permanent shutdown. Persist closed candles
        # and paper state before Android potentially suspends the process.
        try:
            self._flush_candle_archive()
        except Exception as exc:
            self.update_auto_status(
                "ذخیرهٔ آرشیو کندل هنگام رفتن به پس‌زمینه ناموفق بود: "
                + type(exc).__name__
            )
        engine = getattr(self, "auto_engine", None)
        if engine is not None:
            try:
                self._persist_paper_engine_state(engine)
            except Exception as exc:
                self.update_auto_status(
                    "ذخیرهٔ وضعیت موتور هنگام مکث ناموفق بود: "
                    + type(exc).__name__
                )
        return True

    def on_start(self):
        # Kivy user_data_dir is the app-private writable storage on Android.
        # If flock or this filesystem is unavailable, keep the paper runner disabled.
        self.runtime_guard = AppRuntimeGuard(self.user_data_dir)
        self.runtime_lock_ready = self.runtime_guard.start()
        self.runtime_lock_error = self.runtime_guard.error
        self.coordinator_session = None
        self.coordinator_session_error = None
        if self.runtime_lock_ready:
            try:
                state_path = os.path.join(self.user_data_dir, "trade-state.json")
                self.coordinator_session = TradeCoordinatorSession(
                    state_path, max_open_positions=1
                )
                ensure_paper_runner_ready(
                    self.runtime_guard, self.coordinator_session
                )
            except Exception as exc:
                # Keep a successfully opened session available when the readiness
                # gate reports a persisted operator stop. The UI can then offer
                # an explicit, guarded re-arm without deleting state.
                self.coordinator_session_error = (
                    "{}: {}".format(type(exc).__name__, str(exc)[:160])
                )

        # Start the paper-only public market stream automatically after Kivy's
        # UI is initialized. start_auto_paper_trader() repeats every safety gate,
        # restores persisted state, and refuses emergency/loss halts. It never
        # submits exchange orders. Scheduling avoids starting worker/UI updates
        # before the application has completed its startup lifecycle.
        Clock.schedule_once(
            lambda _dt: self.start_auto_paper_trader(), 0.5
        )

    def on_stop(self):
        poll_stop = getattr(self, "orderbook_poll_stop", None)
        if poll_stop is not None:
            poll_stop.set()
        poll_event = getattr(self, "orderbook_poll_event", None)
        if poll_event is not None:
            try:
                poll_event.cancel()
            except Exception:
                pass
        poll_thread = getattr(self, "orderbook_poll_thread", None)
        if poll_thread is not None and poll_thread.is_alive():
            poll_thread.join(timeout=1.0)
        worker_stopped = True
        if hasattr(self, "auto_stop_event"):
            worker_stopped = self._stop_auto_worker(timeout=3.0)
        if worker_stopped:
            try:
                self._flush_candle_archive()
            except Exception as exc:
                self.update_auto_status(
                    "ذخیرهٔ نهایی آرشیو کندل ناموفق بود: "
                    + type(exc).__name__
                )
        guard = getattr(self, "runtime_guard", None)
        persistence_failed = False
        if worker_stopped:
            engine = getattr(self, "auto_engine", None)
            if engine is not None:
                try:
                    self._persist_paper_engine_state(engine)
                except Exception as exc:
                    persistence_failed = True
                    self._paper_emergency_latched = True
                    detail = "{}: {}".format(type(exc).__name__, str(exc)[:160])
                    session = getattr(self, "coordinator_session", None)
                    durable_halt = False
                    if session is not None:
                        try:
                            session.emergency_stop(
                                "paper engine persistence failure during shutdown"
                            )
                            durable_halt = True
                        except Exception as halt_exc:
                            detail += "; durable halt failed: {}: {}".format(
                                type(halt_exc).__name__, str(halt_exc)[:120]
                            )
                    self.update_auto_status(
                        "ذخیره وضعیت موتور آزمایشی هنگام خاموش‌شدن شکست خورد؛ "
                        "شروع مجدد موتور مسدود شد. علت: " + detail
                        + (" توقف پایدار ثبت شد." if durable_halt else
                           " ثبت توقف پایدار تأیید نشد؛ قفل اجرا تا پایان فرایند حفظ می‌شود.")
                    )
                    # If the durable coordinator latch could not be written,
                    # retain the process lock until OS process termination.
                    persistence_failed = not durable_halt
        if guard is not None and worker_stopped and not persistence_failed:
            try:
                guard.stop()
            except Exception:
                # The process exit will release the kernel lock regardless.
                pass
        # If the worker did not exit in time, deliberately keep the app lock
        # held until process termination so a second instance cannot overlap it.


if __name__ == "__main__":
    FuturesAnalyzerApp().run()
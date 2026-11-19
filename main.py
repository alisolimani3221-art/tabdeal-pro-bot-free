import os
import sys
import json
import re
import threading
import time
import requests
import certifi
from android_tls import mount_tabdeal_tls_compat

APP_VERSION = "1.22"
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
from kivy.uix.spinner import Spinner, SpinnerOption
from kivy.core.window import Window
from kivy.core.clipboard import Clipboard
from kivy.core.text import LabelBase
from kivy.resources import resource_add_path
from market_symbols import select_market_symbol, list_market_symbols
from risk_analysis import analyze_klines, calculate_position_risk, recommend_leverage
from market_format import format_market_value
from market_candles import TradeCandleBuilder
from paper_trader import PaperTradingEngine
from trade_stream_probe import BROADCAST_URL, parse_trade_message
from app_runtime_guard import AppRuntimeGuard
from trade_coordinator_session import TradeCoordinatorSession
from paper_runner_gate import ensure_paper_runner_ready, PaperRunnerGateError
from tabdeal_account import read_futures_account
from multi_market_scan import scan_market_universe

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
        book_title_button.bind(on_press=self.show_status)
        content.add_widget(book_title_button)
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
        self.analysis_metrics = self.make_label("SMA20: --   SMA50: --   RSI14: --   ATR14: --", size=12, height=dp(38))
        self.analysis_metrics.font_name = NUMERIC_FONT_NAME

        self.smart_analysis_button = Button(
            markup=True, text=fa("تحلیل هوشمند و پیشنهاد معامله"), font_name=FONT_NAME,
            font_size=sp(16), size_hint_y=None, height=dp(50),
            background_normal="", background_color=(0.12, 0.38, 0.58, 1),
            color=(1, 1, 1, 1),
        )
        self.smart_analysis_button.bind(on_press=self.open_smart_analysis)

        content.add_widget(self.smart_analysis_button)
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
            self.current_symbol = str(value)

    def update_symbol_options(self, symbols):
        if not symbols:
            return
        current = self.current_symbol
        self.symbol_spinner.values = tuple(symbols)
        if current in symbols:
            self.symbol_spinner.text = current
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
            # Mobile TLS connections can occasionally end before the server
            # sends headers (SSLEOFError). Retry transient transport failures
            # with a fresh connection; certificate verification stays enabled.
            url = FUTURES_API + path
            last_error = None
            for attempt in range(3):
                try:
                    response = session.get(
                        url, params=params, timeout=timeout,
                        verify=certifi.where(), headers={"Connection": "close"}
                    )
                    results[path.strip("/").replace("/", "_") + "_status"] = response.status_code
                    if not response.ok:
                        detail = response.text[:500].replace("\\n", " ")
                        if path == "/depth":
                            results["depth_diagnostic"] = "HTTP {} {} body={}".format(
                                response.status_code, response.url, detail
                            )
                        response.close()
                        raise RuntimeError("{} returned HTTP {}: {}".format(path, response.status_code, detail))
                    return response
                except (requests.exceptions.ConnectionError,
                        requests.exceptions.SSLError,
                        requests.exceptions.Timeout) as exc:
                    last_error = exc
                    if attempt < 2:
                        time.sleep(1.0 * (attempt + 1))
            raise RuntimeError("{}: ارتباط امن برقرار نشد پس از 3 تلاش: {}".format(
                path, "{}: {}".format(type(last_error).__name__, str(last_error)[:220])
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
                )
                # Fail closed: a syntactically plausible ticker is not proof
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
                            raw_text.replace("\\n", " ")
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


    def open_account_connection(self, instance):
        """Collect credentials in memory only and request account data via GET."""
        body = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(8))
        body.add_widget(self.make_label(
            "کلید فقط‌خواندنی فیوچرز را وارد کنید. اطلاعات ذخیره نمی‌شود.",
            size=13, height=dp(52), color=(1, 0.76, 0.38, 1)))
        key_input = TextInput(
            hint_text="API Key", multiline=False, password=False,
            font_name=NUMERIC_FONT_NAME, size_hint_y=None, height=dp(46))
        secret_input = TextInput(
            hint_text="API Secret", multiline=False, password=True,
            font_name=NUMERIC_FONT_NAME, size_hint_y=None, height=dp(46))
        body.add_widget(key_input)
        body.add_widget(secret_input)
        status = self.make_label("مجوز برداشت را برای این کلید فعال نکنید.", size=12,
                                 height=dp(48), color=(1, 0.68, 0.35, 1))
        body.add_widget(status)
        buttons = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(8))
        connect = Button(text=fa("خواندن موجودی"), font_name=FONT_NAME)
        cancel = Button(text=fa("انصراف"), font_name=FONT_NAME)
        buttons.add_widget(connect)
        buttons.add_widget(cancel)
        body.add_widget(buttons)
        popup = Popup(title=fa_plain("اتصال فقط‌خواندنی حساب"), title_font=FONT_NAME,
                      content=body, size_hint=(0.94, 0.58), auto_dismiss=False)
        cancel.bind(on_press=popup.dismiss)

        def start_read(instance):
            key = key_input.text.strip()
            secret = secret_input.text.strip()
            if not key or not secret:
                status.text = fa("هر دو فیلد الزامی هستند.")
                return
            key_input.text = ""
            secret_input.text = ""
            key_input.disabled = True
            secret_input.disabled = True
            connect.disabled = True
            status.text = fa("در حال دریافت امن اطلاعات حساب...")
            def worker():
                try:
                    data = read_futures_account(key, secret, timeout=22)
                    result = (True, data)
                except Exception as exc:
                    result = (False, "{}: {}".format(type(exc).__name__, str(exc)[:220]))
                finally:
                    # Drop local references as soon as the request finishes.
                    pass
                Clock.schedule_once(lambda dt, value=result: finish(value), 0)
            def finish(result):
                popup.dismiss()
                if result[0]:
                    data = result[1]
                    lines = [
                        "موجودی کیف پول: {}".format(data["wallet_balance"]),
                        "موجودی آزاد: {}".format(data["available_balance"]),
                        "سود/زیان شناور: {}".format(data["unrealized_pnl"]),
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
                    self.account_summary.text = fa("\\n".join(lines))
                else:
                    self.account_summary.text = fa("خطا در خواندن حساب: " + result[1])
            threading.Thread(target=worker, daemon=True).start()

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
            atr = float(technical["atr14"])
            if signal == "BULLISH":
                decision = "تمایل صعودی — بررسی فرصت خرید"
                stop = price - 1.5 * atr
                target = price + 3.0 * atr
                decision_color = (0.35, 0.9, 0.55, 1)
            elif signal == "BEARISH":
                decision = "تمایل نزولی — بررسی فرصت فروش"
                stop = price + 1.5 * atr
                target = price - 3.0 * atr
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
            add_line("RSI 14: {:.1f}    SMA20: {}".format(
                float(technical["rsi14"]), format_market_value(technical["sma20"])),
                size=13, font=NUMERIC_FONT_NAME)
            add_line("SMA50: {}    ATR14: {}".format(
                format_market_value(technical["sma50"]), format_market_value(atr)), size=13, font=NUMERIC_FONT_NAME)
            if stop is not None and target is not None and stop > 0 and target > 0:
                add_line("حد ضرر تخمینی: {}".format(format_market_value(stop)), font=FONT_NAME)
                add_line("هدف سود تخمینی: {}".format(format_market_value(target)), font=FONT_NAME)
                add_line("نسبت سود به زیان هدف: 2 به 1", size=14)
            else:
                add_line("برای این وضعیت حد ضرر و هدف معامله پیشنهاد نمی‌شود.", size=13)
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

        result_box = BoxLayout(orientation="vertical", spacing=dp(5),
                               size_hint_y=None, height=dp(8 * 38 + 56))
        result_rows = {}

        def add_result_row(key, label):
            row = BoxLayout(orientation="horizontal", spacing=dp(8),
                            size_hint_y=None, height=dp(38))
            label_widget = self.make_label(label, size=13, height=dp(36))
            label_widget.size_hint_x = 0.60
            label_widget.halign = "right"
            label_widget.bind(width=lambda widget, width: setattr(widget, "text_size", (width, None)))
            value_widget = Label(
                text="—", font_name=NUMERIC_FONT_NAME, font_size=sp(13),
                color=(0.95, 0.97, 1, 1), size_hint_x=0.40,
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
        result_note = self.make_label(
            "مقادیر تخمینی هستند؛ کارمزد و لغزش قیمت محاسبه نشده‌اند.",
            size=11, height=dp(52)
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
                if result["margin_estimate"] > capital_value:
                    result_note.text = fa(
                        "هشدار: وجه تضمین تخمینی از سرمایه واردشده بیشتر است؛ این حجم قابل اجرا نیست."
                    )
                elif result["liquidation_warning"]:
                    result_note.text = fa("هشدار: اهرم بالا خطر لیکویید شدن را افزایش می‌دهد.")
                else:
                    result_note.text = fa(
                        "مقادیر تخمینی هستند؛ کارمزد و لغزش قیمت محاسبه نشده‌اند."
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
                "وضعیت سایت: {}\\nپینگ بازار: {}\\nاطلاعات بازار: {}\\n"
                "دفتر سفارش: کد {}\\nنماد معاملاتی: {}\\n"
                "بهترین خرید: {}\\nبهترین فروش: {}\\n"
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
            halign="center", valign="middle", size_hint=(1, None),
            text_size=(Window.width * 0.80, None)
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
            "تعداد ردیف خرید: " + str(len(results.get("bids", []))),
            "تعداد ردیف فروش: " + str(len(results.get("asks", []))),
            "معامله‌گر آزمایشی: " + ("RUNNING" if auto_running else "STOPPED"),
            "آخرین بررسی: " + str(getattr(self, "last_diagnostic_time", "نامشخص")),
        ]
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
            "شبیه‌ساز فقط برای نماد انتخاب‌شده کار می‌کند: " + symbol + "\\n"
            "داده از جریان عمومی معاملات واقعی دریافت می‌شود؛ دفتر سفارش به‌عنوان کندل استفاده نمی‌شود.\\n"
            "برای آماده‌شدن تحلیل، حداقل ۵۱ کندل یک‌دقیقه‌ای بسته‌شده و یک کندل درحال‌تشکیل لازم است.\\n"
            "پس از قطع اتصال یا جاافتادن داده، دوره آماده‌سازی از نو شروع می‌شود.\\n"
            "هیچ سفارش واقعی ارسال نمی‌شود."
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
            height=dp(205),
            halign="center",
            valign="middle",
            text_size=(Window.width - dp(56), None),
            color=(1, 0.82, 0.48, 1),
        )
        body.add_widget(self.auto_status_label)
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
        else:
            self.update_auto_status("قفل انحصاری اجرای برنامه در حافظه داخلی برقرار است؛ شبیه‌ساز فقط آزمایشی است و سفارش واقعی ارسال نمی‌کند.")

    def _persist_paper_engine_state(self, engine):
        """Atomically persist the paper-only engine snapshot in app-private storage."""
        state_path = os.path.join(self.user_data_dir, "paper-engine-state.json")
        temporary_path = state_path + ".tmp"
        with open(temporary_path, "w", encoding="utf-8") as state_file:
            json.dump(engine.export_state(), state_file, ensure_ascii=False,
                      allow_nan=False, separators=(",", ":"))
            state_file.flush()
            os.fsync(state_file.fileno())
        os.replace(temporary_path, state_path)

    def start_auto_paper_trader(self):
        """Start a one-symbol, foreground-only paper simulation; never live orders."""
        guard = getattr(self, "runtime_guard", None)
        coordinator_session = getattr(self, "coordinator_session", None)
        try:
            ensure_paper_runner_ready(guard, coordinator_session)
        except Exception as exc:
            self.update_auto_status(
                "شروع شبیه‌ساز مسدود شد؛ کنترل ایمنی وضعیت پایدار تأیید نشد: "
                + type(exc).__name__ + ": " + str(exc)[:120]
            )
            return
        if self.auto_trader_thread is not None and self.auto_trader_thread.is_alive():
            self.update_auto_status("شبیه‌ساز در حال اجراست؛ برای توقف از توقف اضطراری استفاده کنید.")
            return
        symbol = str(self.current_symbol).upper()
        if not re.fullmatch(r"[A-Z0-9]+_USDT", symbol):
            self.update_auto_status("نماد انتخاب‌شده معتبر نیست؛ شروع انجام نشد.")
            return
        try:
            builder = TradeCandleBuilder(symbol)
            state_path = os.path.join(self.user_data_dir, "paper-engine-state.json")
            if os.path.isfile(state_path):
                with open(state_path, "r", encoding="utf-8") as state_file:
                    engine = PaperTradingEngine.restore_state(json.load(state_file))
                if engine.position is not None and engine.position["symbol"] != symbol:
                    raise RuntimeError("یک موقعیت آزمایشی بازیابی‌شده برای {} باز است؛ ابتدا همان نماد را انتخاب کنید.".format(engine.position["symbol"]))
                if engine.halted:
                    if engine.halt_reason == "EMERGENCY_STOP" and engine.position is None:
                        engine = PaperTradingEngine(capital=self.capital_usdt, risk_percent=0.5,
                                                    max_leverage=3, max_loss_percent=3.0)
                    else:
                        raise RuntimeError("موتور به‌علت عبور از سقف زیان متوقف است؛ شروع خودکار مجاز نیست.")
            else:
                engine = PaperTradingEngine(capital=self.capital_usdt, risk_percent=0.5,
                                            max_leverage=3, max_loss_percent=3.0)
            self._persist_paper_engine_state(engine)
        except Exception as exc:
            self.update_auto_status("راه‌اندازی موتور مسدود شد؛ وضعیت ذخیره‌شده معتبر یا قابل‌بازیابی نیست: "
                                    + type(exc).__name__ + ": " + str(exc)[:160])
            return
        self.auto_stop_event.clear()
        self.auto_engine = engine
        self.auto_candle_builder = builder
        self.auto_trader_thread = threading.Thread(
            target=self._paper_stream_worker,
            args=(symbol, builder, engine),
            daemon=True,
        )
        self.auto_trader_thread.start()
        self.update_auto_status(
            "در حال اتصال به جریان معاملات " + symbol + ".\n"
            "حالت: فقط آزمایشی؛ سرمایه مجازی: {:.2f} USDT؛ ریسک هر معامله: حداکثر ۰٫۵٪.\n"
            "آماده‌سازی: ۰ از ۵۱ کندل بسته‌شده؛ پیشرفت روی همین صفحه نمایش داده می‌شود. "
            "هیچ سفارش واقعی ارسال نمی‌شود.".format(self.capital_usdt)
        )

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
        """Stop the paper stream before closing its simulated position."""
        if not self._stop_auto_worker(timeout=3.0):
            self.update_auto_status(
                "توقف درخواست شد، اما پایان کامل رشته شبیه‌ساز هنوز تأیید نشده است؛ "
                "برای جلوگیری از تغییر هم‌زمان وضعیت، بستن موقعیت مجازی انجام نشد."
            )
            return
        engine = getattr(self, "auto_engine", None)
        close_result = None
        if engine is not None:
            try:
                close_result = engine.emergency_close()
                self._persist_paper_engine_state(engine)
            except Exception:
                close_result = None
        if close_result and close_result.get("event") == "CLOSED":
            trade = close_result["trade"]
            message = (
                "توقف اضطراری فعال شد؛ موقعیت مجازی در آخرین قیمت ثبت‌شده بسته شد. "
                "سود/زیان خالص: {:.4f} تتر. هیچ سفارش واقعی ارسال نشده است."
            ).format(trade["net_pnl"])
        else:
            message = (
                "توقف اضطراری فعال شد. جریان و شبیه‌ساز متوقف شدند؛ "
                "موقعیت مجازی بازی وجود نداشت. هیچ سفارش واقعی ارسال نشده است."
            )
        self.update_auto_status(message)

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

        last_processed_bucket = None
        connected_once = False
        while not self.auto_stop_event.is_set():
            def on_open(ws):
                nonlocal connected_once, last_processed_bucket
                if connected_once:
                    builder.reset_after_gap()
                    last_processed_bucket = None
                connected_once = True
                ws.send(symbol)
                Clock.schedule_once(
                    lambda dt: self.update_auto_status(
                        "اتصال برقرار شد: " + symbol + ". در حال جمع‌آوری معاملات برای ساخت کندل‌های یک‌دقیقه‌ای."
                    ), 0
                )

            def on_message(ws, message):
                nonlocal last_processed_bucket
                parsed = parse_trade_message(message, expected_symbol=symbol)
                if self.auto_stop_event.is_set() or not parsed.get("ok"):
                    return
                update = builder.add_trade(parsed)
                if not update.get("accepted"):
                    return
                # Surface warm-up progress instead of leaving the UI apparently idle.
                if not update.get("ready"):
                    closed_count = int(update.get("closed_candles", 0))
                    if closed_count == 0 or closed_count % 5 == 0:
                        progress = (
                            "در حال آماده‌سازی موتور | {} | کندل معتبر: {}/۵۱ | "
                            "معامله آزمایشی پس از تکمیل داده کافی فعال می‌شود."
                        ).format(symbol, min(closed_count, 51))
                        Clock.schedule_once(
                            lambda dt, value=progress: self.update_auto_status(value), 0
                        )
                # Reconcile any restored open paper position on every validated trade tick.
                try:
                    tick_event = engine.process_tick(symbol, parsed["price"])
                    if tick_event:
                        self._persist_paper_engine_state(engine)
                        last_processed_bucket = parsed["timestamp_ms"] - parsed["timestamp_ms"] % 60_000
                        trade = tick_event["trade"]
                        tick_message = "خروج حفاظتی معامله آزمایشی: {}؛ سود/زیان خالص={:.4f} USDT.".format(trade["reason"], trade["net_pnl"])
                        if tick_event["halted"]:
                            tick_message += " موتور به سقف زیان رسیده و متوقف شد."
                            self.auto_stop_event.set()
                        Clock.schedule_once(lambda dt, value=tick_message: self.update_auto_status(value), 0)
                        if self.auto_stop_event.is_set():
                            ws.close()
                            return
                except Exception as persist_exc:
                    try:
                        engine.emergency_close()
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
                if update.get("gap_detected"):
                    last_processed_bucket = None
                    status = builder.status()
                    gap_message = (
                        "شکاف زمانی در داده بازار شناسایی شد؛ آماده‌سازی از ابتدا انجام می‌شود. "
                        "کندل معتبر: {}/۵۱ | تعداد شکاف‌ها: {} | داده ردشده: {}"
                    ).format(status["closed_candles"], status["gaps"], status["rejected_trades"])
                    Clock.schedule_once(
                        lambda dt, value=gap_message: self.update_auto_status(value), 0
                    )
                    return
                current_bucket = (builder.current or {}).get("open_time")
                if not builder.is_ready or current_bucket is None or current_bucket == last_processed_bucket:
                    return
                try:
                    technical = analyze_klines(builder.candles_for_analysis())
                    technical["symbol"] = symbol
                    Clock.schedule_once(
                        lambda dt, value=dict(technical): self.update_technical_labels(value), 0
                    )
                    if self.auto_stop_event.is_set():
                        return
                    result = engine.process_scan([technical])
                    try:
                        self._persist_paper_engine_state(engine)
                    except Exception as persist_exc:
                        try:
                            engine.emergency_close()
                            self._persist_paper_engine_state(engine)
                        except Exception:
                            pass
                        self.auto_stop_event.set()
                        try:
                            ws.close()
                        except Exception:
                            pass
                        Clock.schedule_once(lambda dt, value=type(persist_exc).__name__: self.update_auto_status(
                            "ذخیره وضعیت موتور ناموفق بود؛ توقف اضطراری فعال شد: " + value), 0)
                        return
                    last_processed_bucket = current_bucket
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
                        ws.close()
                    message_text = (
                        "شبیه‌ساز فعال | {}\nسیگنال آخر: {} | قیمت کندل بسته‌شده: {:.8g}\n"
                        "کندل‌های معتبر: {} | سرمایه مجازی: {:.4f} USDT | معاملات بسته: {}\n"
                        "حداکثر ریسک هر معامله ۰٫۵٪؛ اهرم حداکثر ۳ برابر.\n{}"
                    ).format(symbol, technical["signal"], technical["price"],
                             builder.status()["closed_candles"], result["equity"],
                             result["closed_trades"], event_text)
                    Clock.schedule_once(
                        lambda dt, value=message_text: self.update_auto_status(value), 0
                    )
                except Exception as exc:
                    Clock.schedule_once(
                        lambda dt, value=type(exc).__name__: self.update_auto_status(
                            "تحلیل کندل انجام نشد؛ شبیه‌ساز تا داده معتبر بعدی معامله‌ای باز نمی‌کند. علت: " + value
                        ), 0
                    )

            def on_error(ws, error):
                detail = "{}: {}".format(type(error).__name__, str(error)[:140])
                Clock.schedule_once(
                    lambda dt, value=detail: self.update_auto_status(
                        "خطای جریان معاملات؛ تلاش برای اتصال مجدد. علت: " + value
                    ), 0
                )

            def on_close(ws, close_status_code, close_message):
                if not self.auto_stop_event.is_set():
                    detail = "کد={}; پیام={}".format(
                        close_status_code, str(close_message or "بدون توضیح")[:100]
                    )
                    Clock.schedule_once(
                        lambda dt, value=detail: self.update_auto_status(
                            "جریان معاملات بسته شد؛ اتصال مجدد انجام می‌شود. " + value
                        ), 0
                    )

            app = websocket.WebSocketApp(
                BROADCAST_URL, on_open=on_open, on_message=on_message,
                on_error=on_error, on_close=on_close,
            )
            self.auto_ws = app
            try:
                app.run_forever(ping_interval=20, ping_timeout=10)
            except Exception as stream_exc:
                detail = "{}: {}".format(type(stream_exc).__name__, str(stream_exc)[:140])
                Clock.schedule_once(
                    lambda dt, value=detail: self.update_auto_status(
                        "خطای اجرای جریان معاملات؛ تلاش مجدد. علت: " + value
                    ), 0
                )
            finally:
                self.auto_ws = None
            if not self.auto_stop_event.is_set():
                Clock.schedule_once(
                    lambda dt: self.update_auto_status(
                        "اتصال قطع شد؛ تلاش مجدد پس از مکث کوتاه. پس از شکاف داده، گرم‌سازی کندل دوباره لازم است."
                    ), 0
                )
                self.auto_stop_event.wait(3)

        final_status = (
            "شبیه‌ساز متوقف شد. هیچ سفارش واقعی ارسال نشده است. "
            "برای جزئیات، پیام آخر وضعیت موتور را بررسی کنید."
        )
        Clock.schedule_once(
            lambda dt, value=final_status: self.update_auto_status(value), 0
        )

    def update_technical_labels(self, technical):
        """Publish verified closed-candle indicators to the main analysis panel."""
        self.latest_technical_analysis = dict(technical)
        signal = technical.get("signal", "NEUTRAL")
        labels = {"BULLISH": "صعودی", "BEARISH": "نزولی", "NEUTRAL": "خنثی"}
        if getattr(self, "analysis_signal", None) is not None:
            self.analysis_signal.text = fa("سیگنال کندل بسته‌شده: " + labels.get(signal, "خنثی"))
        if getattr(self, "analysis_metrics", None) is not None:
            self.analysis_metrics.text = (
                "SMA20: {:.6g} | SMA50: {:.6g} | RSI14: {:.2f} | ATR14: {:.6g}"
            ).format(technical["sma20"], technical["sma50"],
                     technical["rsi14"], technical["atr14"])

    def update_auto_status(self, message):
        label = getattr(self, "auto_status_label", None)
        if label is not None:
            label.text = assistant_rtl_text(message)
            label.font_name = font_for_text(message)
            label.base_direction = "ltr"
            label.text_language = "ar"

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
                self.coordinator_session = None
                self.coordinator_session_error = (
                    "{}: {}".format(type(exc).__name__, str(exc)[:160])
                )

    def on_stop(self):
        worker_stopped = True
        if hasattr(self, "auto_stop_event"):
            worker_stopped = self._stop_auto_worker(timeout=3.0)
        guard = getattr(self, "runtime_guard", None)
        if worker_stopped:
            engine = getattr(self, "auto_engine", None)
            if engine is not None:
                try:
                    self._persist_paper_engine_state(engine)
                except Exception:
                    pass
        if guard is not None and worker_stopped:
            try:
                guard.stop()
            except Exception:
                # The process exit will release the kernel lock regardless.
                pass
        # If the worker did not exit in time, deliberately keep the app lock
        # held until process termination so a second instance cannot overlap it.


if __name__ == "__main__":
    FuturesAnalyzerApp().run()

import os
import sys
import json
import re
import threading
import requests
import certifi

APP_VERSION = "1.8"
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
from kivy.resources import resource_add_path, resource_find
from market_symbols import select_market_symbol, list_market_symbols
from risk_analysis import analyze_klines, calculate_position_risk, recommend_leverage
from paper_trader import PaperTradingEngine

# Resolve the bundled font from both the app directory and Kivy resources.
# Resolve bundled Arabic/Persian font robustly. Prefer Noto Sans Arabic, which
# covers Persian letters and Latin digits in one face; also check Android's
# system Noto font locations as a last-resort fallback.
FONT_CANDIDATES = [
    # Use Noto Sans Arabic as the app-wide default: Kivy widgets that implicitly
    # request Roboto (buttons, spinners, popup titles, text inputs) must also
    # receive Persian glyphs, not only labels explicitly styled as Persian.
    # Prefer Noto Sans Arabic: it covers Arabic/Persian and Latin UI text
    # in one face, avoiding mixed-font fallback for symbols and prices.
    os.path.join(BASE_DIR, "NotoSansArabic-Regular.ttf"),
    os.path.join(BASE_DIR, "assets", "NotoSansArabic-Regular.ttf"),
    os.path.join(BASE_DIR, "NotoNaskhArabic-Regular.ttf"),
    os.path.join(BASE_DIR, "assets", "NotoNaskhArabic-Regular.ttf"),
    "/system/fonts/NotoNaskhArabicUI-Regular.ttf",
    "/system/fonts/NotoNaskhArabic-Regular.ttf",
    "/system/fonts/NotoSansArabic-Regular.ttf",
]
FONT_PATH = next((p for p in FONT_CANDIDATES if os.path.isfile(p)), None)
LATIN_FONT_CANDIDATES = [
    os.path.join(BASE_DIR, "NotoSans-Regular.ttf"),
    os.path.join(BASE_DIR, "assets", "NotoSans-Regular.ttf"),
]
LATIN_FONT_PATH = next((p for p in LATIN_FONT_CANDIDATES if os.path.isfile(p)), None)
FONT_LOAD_ERROR = None
FONT_NAME = "Roboto"
NUMERIC_FONT_NAME = "Roboto"

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
        FONT_NAME = "PersianNoto"
    except Exception as exc:
        FONT_LOAD_ERROR = "{}: {}".format(type(exc).__name__, str(exc))

# Keep Latin letters, digits and punctuation in a Latin-capable face.
# Never register an Arabic-only font as Roboto: that creates tofu squares.
if LATIN_FONT_PATH:
    try:
        resource_add_path(os.path.dirname(LATIN_FONT_PATH))
        LabelBase.register(
            name="Roboto",
            fn_regular=LATIN_FONT_PATH,
            fn_bold=LATIN_FONT_PATH,
            fn_italic=LATIN_FONT_PATH,
            fn_bolditalic=LATIN_FONT_PATH,
        )
    except Exception as exc:
        FONT_LOAD_ERROR = "{}: {}".format(type(exc).__name__, str(exc))

if not FONT_PATH:
    FONT_LOAD_ERROR = "No bundled or Android system Noto Arabic font was found."
if not LATIN_FONT_PATH:
    FONT_LOAD_ERROR = (FONT_LOAD_ERROR or "") + " No bundled Latin font was found."


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


def fa(text):
    """Shape Persian text and mark Latin runs to use the Latin-capable font."""
    value = str(text)
    if not any("\u0600" <= char <= "\u06ff" or "\u0750" <= char <= "\u077f"
               or "\u08a0" <= char <= "\u08ff" for char in value):
        return value
    try:
        if arabic_reshaper is not None and get_display is not None:
            value = get_display(arabic_reshaper.reshape(value))
            # Kivy Label markup lets a mixed RTL/LTR sentence use the correct
            # font for each run instead of asking the Arabic font for Latin glyphs.
            value = re.sub(
                r"[\\x20-\\x7e]+",
                lambda match: "[font=Roboto]{}[/font]".format(match.group(0)),
                value,
            )
    except Exception:
        pass
    return value


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
        cards.add_widget(self.info_card("بازه زمانی", "لحظه‌ای")[0])
        content.add_widget(cards)

        # Tappable order-book header refreshes live data. Each level is also
        # a real button so the user can inspect that exact bid/ask price and size.
        book_title_button = Button(
            text=fa("دفتر سفارش  -  لمس برای به‌روزرسانی"),
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
        self.analysis_signal = self.make_label("پس از دریافت کندل‌ها، روند بازار محاسبه می‌شود.", size=15, height=dp(42))
        content.add_widget(self.analysis_signal)
        self.analysis_metrics = self.make_label("SMA20: --   SMA50: --   RSI14: --   ATR14: --", size=12, height=dp(38))
        self.analysis_metrics.font_name = NUMERIC_FONT_NAME

        self.smart_analysis_button = Button(
            text=fa("تحلیل هوشمند و پیشنهاد معامله"), font_name=FONT_NAME,
            font_size=sp(16), size_hint_y=None, height=dp(50),
            background_normal="", background_color=(0.12, 0.38, 0.58, 1),
            color=(1, 1, 1, 1),
        )
        self.smart_analysis_button.bind(on_press=self.open_smart_analysis)
        content.add_widget(self.smart_analysis_button)
        self.latest_technical_analysis = None
        content.add_widget(self.analysis_metrics)

        self.risk_button = Button(text=fa("محاسبه مدیریت ریسک"), font_name=FONT_NAME, font_size=sp(16),
                                  size_hint_y=None, height=dp(48), background_normal="",
                                  background_color=(0.12, 0.48, 0.38, 1), color=(1, 1, 1, 1))
        self.risk_button.bind(on_press=self.open_risk_calculator)
        content.add_widget(self.risk_button)

        self.diagnostics_button = Button(
            text=fa("مرکز عیب‌یابی و تعمیر برنامه"), font_name=FONT_NAME,
            font_size=sp(16), size_hint_y=None, height=dp(50),
            background_normal="", background_color=(0.30, 0.24, 0.48, 1),
            color=(1, 1, 1, 1))
        self.diagnostics_button.bind(on_press=self.open_diagnostics)
        content.add_widget(self.diagnostics_button)
        self.last_diagnostic_results = {}
        self.last_diagnostic_time = "هنوز اجرا نشده"

        self.auto_trader_button = Button(
            text=fa("معامله‌گر خودکار آزمایشی"), font_name=FONT_NAME,
            font_size=sp(16), size_hint_y=None, height=dp(50),
            background_normal="", background_color=(0.42, 0.30, 0.12, 1),
            color=(1, 1, 1, 1))
        self.auto_trader_button.bind(on_press=self.open_auto_trader)
        content.add_widget(self.auto_trader_button)
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
            "این نسخه هیچ سفارش واقعی ثبت نمی‌کند؛ معامله‌گر خودکار فقط در حالت آزمایشی شبیه‌سازی می‌کند.",
            size=13, color=(1, 0.68, 0.35, 1), height=dp(66)))
        scroll.add_widget(content)
        root.add_widget(scroll)

        self.check_button = Button(text=fa("بررسی اتصال"), font_name=FONT_NAME, font_size=sp(17),
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
        self.check_button.disabled = True
        self.check_button.text = fa("در حال بررسی...")
        self.connection.text = fa("در حال آزمایش سایت و API بازار فیوچرز...")
        self.analysis.text = fa("لطفاً چند لحظه صبر کنید.")
        selected_symbol = self.current_symbol
        threading.Thread(
            target=self.check_services, args=(selected_symbol,), daemon=True
        ).start()

    def check_services(self, requested_symbol="BTC_USDT"):
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36",
            "Accept": "application/json",
            "Connection": "close",
        })
        results = {}

        def get_response(path, params=None, timeout=22):
            url = FUTURES_API + path
            response = session.get(url, params=params, timeout=timeout, verify=certifi.where())
            results[path.strip("/").replace("/", "_") + "_status"] = response.status_code
            if not response.ok:
                detail = response.text[:500].replace("\n", " ")
                results["depth_diagnostic"] = "HTTP {} {} body={}".format(
                    response.status_code, response.url, detail
                ) if path == "/depth" else results.get("depth_diagnostic")
                raise RuntimeError("{} returned HTTP {}: {}".format(path, response.status_code, detail))
            return response

        try:
            try:
                web = session.get(TABDEAL_WEBSITE, timeout=18, allow_redirects=True,
                                  verify=certifi.where())
                results["website"] = web.status_code
                web.close()
            except Exception as exc:
                results["website_error"] = "{}: {}".format(type(exc).__name__, str(exc)[:180])

            # These are Tabdeal's documented public Futures read-only endpoints.
            ping = get_response("/ping")
            ping.close()
            results["market_ping"] = 200

            server_time = get_response("/time")
            try:
                time_data = server_time.json()
                results["market_time"] = 200
                results["server_time_received"] = bool(time_data)
            except Exception:
                results["market_time"] = "پاسخ غیر JSON"
            finally:
                server_time.close()

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
            results["symbol_count"] = len(symbols)
            from market_symbols import list_market_symbols
            available_symbols = list_market_symbols(symbols)
            if not available_symbols:
                raise RuntimeError("/exchangeInfo contained no active USDT perpetual symbols")
            Clock.schedule_once(
                lambda dt, values=available_symbols: self.update_symbol_options(values), 0
            )
            try:
                market_symbol = select_market_symbol(symbols, requested_symbol)
            except ValueError:
                # If a selected contract was delisted while the app was open,
                # safely fall back to the first active USDT perpetual contract.
                market_symbol = available_symbols[0]
            results["market_symbol"] = market_symbol
            results["symbol_count"] = len(available_symbols)

            # Use the exact symbol returned by exchangeInfo. Tabdeal futures
            # Preserve the exact symbol string returned by Tabdeal exchangeInfo.
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

            # Accept the common API wrappers without assuming one fixed envelope.
            payload = book
            for _ in range(4):
                if not isinstance(payload, dict):
                    break
                if "bids" in payload or "asks" in payload:
                    break
                nested = payload.get("data")
                if nested is None:
                    nested = payload.get("result")
                if nested is None:
                    nested = payload.get("payload")
                if nested is None or nested is payload:
                    break
                payload = nested

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
            results["mid_price"] = (bid + ask) / 2
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


    def open_order_level(self, side, row_text):
        parts = row_text.split()
        if len(parts) < 2 or parts[0] == "--" or parts[1] == "--":
            return
        body = BoxLayout(orientation="vertical", padding=dp(14), spacing=dp(10))
        body.add_widget(self.make_label("سمت سفارش: " + side, size=16, height=dp(42)))
        body.add_widget(self.make_label("قیمت: " + parts[0], size=15, height=dp(38)))
        body.add_widget(self.make_label("مقدار: " + parts[1], size=15, height=dp(38)))
        close_button = Button(text=fa("بستن"), font_name=FONT_NAME,
                              size_hint_y=None, height=dp(44))
        popup = Popup(title=fa("جزئیات ردیف دفتر سفارش"),
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
            add_line("قیمت مبنا: {:,.6f}".format(price), font=FONT_NAME)
            add_line("RSI 14: {:.1f}    SMA20: {:,.6f}".format(
                float(technical["rsi14"]), float(technical["sma20"])),
                size=13, font=NUMERIC_FONT_NAME)
            add_line("SMA50: {:,.6f}    ATR14: {:,.6f}".format(
                float(technical["sma50"]), atr), size=13, font=NUMERIC_FONT_NAME)
            if stop is not None and target is not None and stop > 0 and target > 0:
                add_line("حد ضرر تخمینی: {:,.6f}".format(stop), font=FONT_NAME)
                add_line("هدف سود تخمینی: {:,.6f}".format(target), font=FONT_NAME)
                add_line("نسبت سود به زیان هدف: 2 به 1", size=14)
            else:
                add_line("برای این وضعیت حد ضرر و هدف معامله پیشنهاد نمی‌شود.", size=13)
            add_line("این نتیجه فقط بر اساس کندل‌های بسته‌شده و شاخص‌های SMA، RSI و ATR است؛ تضمین سود نیست.",
                     size=12, height=dp(68), color=(1, 0.72, 0.35, 1))

        report_scroll = ScrollView(do_scroll_x=False)
        report_scroll.add_widget(report)
        body.add_widget(report_scroll)
        close_button = Button(text=fa("بستن"), font_name=FONT_NAME,
                              size_hint_y=None, height=dp(46))
        body.add_widget(close_button)
        popup = Popup(title=fa("تحلیل هوشمند بازار"), title_font=FONT_NAME,
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
        add_field("entry", "قیمت ورود (تخمین دفتر سفارش)", "{:.2f}".format(self.latest_market_price) if self.latest_market_price else "")
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
        calculate_button = Button(text=fa("محاسبه"), font_name=FONT_NAME)
        close_button = Button(text=fa("بستن"), font_name=FONT_NAME)
        actions.add_widget(calculate_button)
        actions.add_widget(close_button)
        body.add_widget(actions)

        risk_scroll = ScrollView(do_scroll_x=False, bar_width=dp(4))
        risk_scroll.add_widget(body)
        popup = Popup(title=fa("مدیریت ریسک معامله"), title_font=FONT_NAME,
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
                result_rows["quantity"].text = "{:.8f}".format(result["quantity"])
                result_rows["notional"].text = "{:.2f}".format(result["notional"])
                result_rows["margin"].text = "{:.2f}".format(result["margin_estimate"])
                result_rows["stop_buy"].text = "{:.2f}".format(result["stop_price_long"])
                result_rows["stop_sell"].text = "{:.2f}".format(result["stop_price_short"])
                result_rows["target_buy"].text = "{:.2f}".format(result["target_price_long"])
                result_rows["target_sell"].text = "{:.2f}".format(result["target_price_short"])
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
        self.last_diagnostic_results = dict(results)
        self.last_diagnostic_time = __import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self.check_button.disabled = False
        self.check_button.text = fa("بررسی دوباره اتصال")
        website_ok = "website" in results and 200 <= results["website"] < 400
        market_ok = (
            "market_ping" in results and 200 <= results["market_ping"] < 400
            and "market_info" in results and 200 <= results["market_info"] < 400
            and "depth_status" in results and 200 <= results["depth_status"] < 400
            and "mid_price" in results
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
            self.price_value.text = "{:,.2f}".format(results["mid_price"])
            self.bid_value.text = "{:,.2f}".format(results["best_bid"])
            self.ask_value.text = "{:,.2f}".format(results["best_ask"])
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
                    row_button.text = "{:.2f}       {:.6f}".format(
                        float(level_price), float(level_qty)
                    )
                    row_button.disabled = False
                    row_button.background_color = (0.10, 0.18, 0.20, 1) if row_buttons is self.bid_rows else (0.22, 0.13, 0.13, 1)
                else:
                    row_button.text = "--       --"
                    row_button.disabled = True
                    row_button.background_color = (0.10, 0.13, 0.18, 1)

        if website_ok and market_ok:
            self.connection.text = fa("اتصال سایت و دریافت داده زنده بازار برقرار است")
            self.connection.color = (0.35, 0.9, 0.55, 1)
            message = (
                "سایت: کد {}\nپینگ بازار: کد {}\nاطلاعات بازار: کد {}\n"
                "دفتر سفارش: کد {}\nنماد معاملاتی: {}\n"
                "بهترین خرید: {:,.2f}\nبهترین فروش: {:,.2f}\n"
                "قیمت میانی تقریبی: {:,.2f}"
            ).format(results["website"], results["market_ping"], results["market_info"],
                     results["depth_status"], results["market_symbol"],
                     results["best_bid"], results["best_ask"], results["mid_price"])
            popup_title = "دریافت داده بازار موفق"
        else:
            self.connection.text = fa("دریافت کامل داده بازار برقرار نشد")
            self.connection.color = (1, 0.55, 0.4, 1)
            message = "سایت: {}\nپینگ بازار: {}\nاطلاعات بازار: {}\nدفتر سفارش: {}\nجزئیات: {}".format(
                results.get("website", results.get("website_error", "ناموفق")),
                results.get("market_ping", "ناموفق"),
                results.get("market_info", "ناموفق"),
                results.get("depth_status", "ناموفق"),
                results.get("market_error", results.get("website_error", "پاسخ کامل دریافت نشد")))
            popup_title = "گزارش اتصال"

        # Keep the main-page summary short. The detailed report belongs in
        # the popup; long mixed Persian/ASCII diagnostics clipped and
        # overlapped the safety notice on small screens.
        if market_ok and book_ok:
            self.analysis.text = fa(
                "داده‌های زنده بازار دریافت شد.\n"
                "دفتر سفارش خرید و فروش به‌روزرسانی شد.\n"
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
        for key in ("depth_diagnostic", "market_error"):
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

        close_button = Button(text=fa("متوجه شدم"), font_name=FONT_NAME, size_hint_y=None, height=dp(46))
        body.add_widget(close_button)
        popup = Popup(title=fa(popup_title), title_font=FONT_NAME, content=body,
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
        depth_status = "OK" if results.get("bids") and results.get("asks") else "NOT VERIFIED"
        lines = [
            "مرکز عیب‌یابی برنامه",
            "نسخه برنامه: " + APP_VERSION,
            "سیستم: Android / Kivy",
            "فونت فارسی: " + font_status,
            "نماد انتخاب‌شده: " + str(self.current_symbol),
            "دفتر سفارش: " + depth_status,
            "تعداد ردیف خرید: " + str(len(results.get("bids", []))),
            "تعداد ردیف فروش: " + str(len(results.get("asks", []))),
            "معامله‌گر آزمایشی: " + ("RUNNING" if auto_running else "STOPPED"),
            "آخرین بررسی: " + str(getattr(self, "last_diagnostic_time", "نامشخص")),
        ]
        for key in ("website", "market_ping", "market_info", "depth_status",
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
        copy_button = Button(text=fa("کپی گزارش"), font_name=FONT_NAME)
        check_button = Button(text=fa("آزمایش دوباره"), font_name=FONT_NAME)
        close_button = Button(text=fa("بستن"), font_name=FONT_NAME)
        actions.add_widget(copy_button)
        actions.add_widget(check_button)
        actions.add_widget(close_button)
        body.add_widget(actions)
        popup = Popup(title=fa("مرکز عیب‌یابی"), title_font=FONT_NAME,
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

    def open_auto_trader(self, instance):
        body = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(9))
        body.add_widget(self.make_label(
            "حالت آزمایشی: فقط معاملات شبیه‌سازی می‌شوند و هیچ سفارش واقعی ارسال نمی‌شود.",
            size=13, height=dp(62), color=(1, 0.76, 0.38, 1)))
        fields = BoxLayout(orientation="vertical", spacing=dp(6),
                           size_hint_y=None, height=dp(112))
        capital_row = BoxLayout(orientation="horizontal", spacing=dp(8))
        capital_row.add_widget(self.make_label("سرمایه آزمایشی (USDT)", size=13, height=dp(44)))
        capital_input = TextInput(text="{:.2f}".format(self.capital_usdt),
                                  multiline=False, input_filter="float",
                                  # Numeric-only input: use Android's standard Roboto.
                                  font_name=NUMERIC_FONT_NAME, font_size=sp(16),
                                  size_hint_x=0.55, size_hint_y=None, height=dp(44))
        capital_row.add_widget(capital_input)
        fields.add_widget(capital_row)
        risk_row = BoxLayout(orientation="horizontal", spacing=dp(8))
        risk_row.add_widget(self.make_label("ریسک هر معامله (حداکثر 0.5%)", size=12, height=dp(48)))
        risk_input = TextInput(text="0.5", multiline=False, input_filter="float",
                               font_name=NUMERIC_FONT_NAME, font_size=sp(16),
                               size_hint_x=0.55, size_hint_y=None, height=dp(44))
        risk_row.add_widget(risk_input)
        fields.add_widget(risk_row)
        body.add_widget(fields)
        self.auto_status_label = self.make_label(
            "آماده شروع؛ سرمایه اولیه پیشنهادی 8 USDT است.", size=12, height=dp(100))
        body.add_widget(self.auto_status_label)
        actions = BoxLayout(orientation="horizontal", spacing=dp(8),
                            size_hint_y=None, height=dp(48))
        start_button = Button(text=fa("وضعیت اسکن خودکار"), font_name=FONT_NAME)
        stop_button = Button(text=fa("توقف"), font_name=FONT_NAME)
        close_button = Button(text=fa("بستن"), font_name=FONT_NAME)
        actions.add_widget(start_button)
        actions.add_widget(stop_button)
        body.add_widget(actions)
        body.add_widget(close_button)
        popup = Popup(title=fa("مدیریت معامله‌گر آزمایشی"),
                      title_font=FONT_NAME, content=body,
                      size_hint=(0.96, 0.78), auto_dismiss=False)

        def start(_button):
            self.auto_status_label.text = fa(
                "اسکن خودکار و تولید سیگنال فعلاً غیرفعال است.\\n"
                "در API عمومی مستندشده تبدیل، کندل تاریخی وجود ندارد.\\n"
                "برای جلوگیری از سیگنال ساختگی، تا زمان تأیید منبع رسمی معاملات لحظه‌ای "
                "هیچ معامله آزمایشی آغاز نمی‌شود."
            )

        def stop(_button):
            self.auto_stop_event.set()
            self.auto_status_label.text = fa("درخواست توقف ثبت شد؛ اسکن جاری متوقف می‌شود.")

        start_button.bind(on_press=start)
        stop_button.bind(on_press=stop)
        close_button.bind(on_press=popup.dismiss)
        popup.open()

    def run_auto_paper_scanner(self):
        """Fail closed: no verified public Tabdeal Futures candle feed exists."""
        message = (
            "اسکن خودکار فعلاً غیرفعال است.\n"
            "API عمومی مستندشده تبدیل کندل تاریخی ارائه نمی‌کند.\n"
            "قیمت میانی دفتر سفارش جایگزین کندل یا قیمت آخرین معامله نیست.\n"
            "تا زمان پیاده‌سازی و آزمایش منبع رسمی معاملات لحظه‌ای، "
            "هیچ سیگنال یا معامله آزمایشی ساخته نمی‌شود.\n"
            "هیچ سفارش واقعی ارسال نشده است."
        )
        Clock.schedule_once(lambda dt, value=message: self.update_auto_status(value), 0)

    def update_auto_status(self, message):
        label = getattr(self, "auto_status_label", None)
        if label is not None:
            label.text = message
            label.font_name = font_for_text(message)

    def on_stop(self):
        if hasattr(self, "auto_stop_event"):
            self.auto_stop_event.set()


if __name__ == "__main__":
    FuturesAnalyzerApp().run()

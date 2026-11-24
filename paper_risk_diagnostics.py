"""Local, read-only diagnostics for persisted paper-trading risk limits."""

import math


def build_paper_risk_diagnostic_lines(state):
    """Explain persisted paper risk floors without mutating or rearming the engine."""
    if not isinstance(state, dict):
        return ["تشخیص محدودیت ریسک آزمایشی: وضعیت ذخیره‌شده معتبر نیست"]

    def number(key):
        value = state.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        value = float(value)
        return value if math.isfinite(value) and value >= 0 else None

    def shown(value):
        return "نامعتبر/ثبت‌نشده" if value is None else "{:.8f}".format(value).rstrip("0").rstrip(".")

    initial = number("initial_capital")
    cash = number("cash")
    daily_start = number("daily_start_equity")
    daily_percent = number("daily_loss_percent")
    max_percent = number("max_loss_percent")
    position_open = state.get("position") is not None
    lines = [
        "--- تشخیص فقط‌خواندنی محدودیت ریسک آزمایشی ---",
        "تاریخ مبنای زیان روزانه UTC: " + str(state.get("daily_loss_date", "ثبت‌نشده"))[:32],
        "علت توقف ذخیره‌شده: " + str(state.get("halt_reason", "ندارد"))[:64],
        "جزئیات توقف: " + str(state.get("halt_detail", "ندارد"))[:180],
        "سرمایه اولیه آزمایشی: " + shown(initial),
        "وجه نقد فعلی آزمایشی: " + shown(cash),
        "مبنای شروع روز UTC: " + shown(daily_start),
        "درصد سقف زیان روزانه: " + shown(daily_percent),
    ]
    if daily_start is not None and daily_percent is not None and daily_percent <= 100:
        daily_floor = daily_start * (1.0 - daily_percent / 100.0)
        lines.append("کف مجاز وجه نقد روزانه: " + shown(daily_floor))
        if cash is not None:
            lines.append("فاصله تا کف زیان روزانه: " + shown(cash - daily_floor))
            lines.append("وضعیت سقف زیان روزانه: " + (
                "فعال؛ بازآماده‌سازی مسدود است" if cash <= daily_floor
                else "در محدوده مجاز"
            ))
    else:
        lines.append("وضعیت سقف زیان روزانه: قابل محاسبه نیست؛ داده ریسک ناقص/نامعتبر است")

    lines.append("درصد سقف زیان تجمعی: " + shown(max_percent))
    if initial is not None and max_percent is not None and max_percent <= 100:
        cumulative_floor = initial * (1.0 - max_percent / 100.0)
        lines.append("کف مجاز زیان تجمعی: " + shown(cumulative_floor))
        if cash is not None and not position_open:
            lines.append("وضعیت سقف زیان تجمعی: " + (
                "فعال؛ بازآماده‌سازی مسدود است" if cash <= cumulative_floor
                else "در محدوده مجاز"
            ))
        elif position_open:
            lines.append("وضعیت سقف زیان تجمعی: به‌علت موقعیت باز، از وجه نقد به‌تنهایی نتیجه‌گیری نمی‌شود")
    else:
        lines.append("وضعیت سقف زیان تجمعی: قابل محاسبه نیست؛ داده ریسک ناقص/نامعتبر است")
    lines.append("موقعیت آزمایشی باز: " + ("بله" if position_open else "خیر"))
    lines.append("این گزارش فقط‌خواندنی است؛ هیچ موجودی، تاریخ، حد ریسک یا وضعیت توقفی تغییر نمی‌کند.")
    return lines


import json
import time
import urllib.request
import urllib.parse
from datetime import datetime

BASE = "https://fapi.binance.com"

def get_data(path, params=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)

    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0"}
    )

    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())

def get_symbols():
    data = get_data("/fapi/v1/exchangeInfo")
    symbols = [
        x["symbol"] for x in data["symbols"]
        if x["quoteAsset"] == "USDT"
        and x["contractType"] == "PERPETUAL"
        and x["status"] == "TRADING"
    ]
    return symbols[:96]

def analyze(symbol):
    candles = get_data(
        "/fapi/v1/klines",
        {"symbol": symbol, "interval": "1h", "limit": 60}
    )

    closes = [float(c[4]) for c in candles[:-1]]

    ema9 = sum(closes[-9:]) / 9
    ema21 = sum(closes[-21:]) / 21

    changes = [
        closes[i] - closes[i-1]
        for i in range(1, len(closes))
    ]

    gains = [max(x, 0) for x in changes[-14:]]
    losses = [max(-x, 0) for x in changes[-14:]]

    avg_gain = sum(gains) / 14
    avg_loss = sum(losses) / 14

    if avg_loss == 0:
        rsi = 100
    else:
        rsi = 100 - (100 / (1 + avg_gain / avg_loss))

    price = closes[-1]

    if ema9 > ema21 and rsi < 70:
        signal = "BUY"
    elif ema9 < ema21 and rsi > 30:
        signal = "SELL"
    else:
        signal = "WAIT"

    return price, rsi, signal

def main():
    print("Futures Analyzer Started")
    print("No real trades will be placed.")

    symbols = get_symbols()
    print("Active symbols:", len(symbols))

    while True:
        print("\n================================")
        print("Scan:", datetime.now().strftime("%H:%M:%S"))

        for i, symbol in enumerate(symbols, 1):
            try:
                price, rsi, signal = analyze(symbol)

                print(
                    symbol,
                    "Price:", price,
                    "RSI:", round(rsi, 2),
                    "Signal:", signal
                )

            except Exception as e:
                print(symbol, "ERROR:", str(e))

            time.sleep(0.15)

        print("Scan completed.")
        time.sleep(60)

if __name__ == "__main__":
    main()

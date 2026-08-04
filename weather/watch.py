"""毎時の見張り番。「何か起きた時だけ」速報を返す（平常時はNone）。

検知する事象（方向の示唆はしない）:
- W1: ヒゲ注意ゾーン（キリ番・前日高安）への本日初タッチ/突破が直近ウィンドウで発生
- W2: 本日レンジが想定日次レンジの120%を初めて超えた（ボラ拡張日化）
再通知防止のため、いずれも「本日初めて」かつ「直近ウィンドウ内」の事象のみ拾う。
"""
from __future__ import annotations

import math
import time

from .fetch import _yahoo_chart

WINDOW_SEC = 70 * 60  # 毎時実行+遅延マージン


def _rounds(price: float, radius: float) -> list[float]:
    grid = 50.0
    lo = math.floor((price - radius) / grid) * grid
    out = []
    x = lo
    while x <= price + radius:
        out.append(x)
        x += grid
    return out


def run_watch(errors: list[str]) -> str | None:
    intr = daily = gvz_val = None
    last_err: Exception | None = None
    used_symbol = None
    for symbol in ("XAUUSD%3DX", "GC%3DF"):
        try:
            intr = _yahoo_chart(symbol, "1d", "5m")
            daily = _yahoo_chart(symbol, "5d", "1d")
            used_symbol = symbol
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
    if intr is None or daily is None:
        errors.append(f"見張り取得失敗: {type(last_err).__name__}: {last_err}")
        return None
    try:
        gvz_val = _yahoo_chart("%5EGVZ", "3mo", "1d")["close"][-1]
    except Exception:  # noqa: BLE001
        gvz_val = None  # ボラ拡張判定(W2)のみスキップ

    now = time.time()
    close = intr["close"][-1]
    in_win = [i for i, t in enumerate(intr["ts"]) if t >= now - WINDOW_SEC]
    before = [i for i, t in enumerate(intr["ts"]) if t < now - WINDOW_SEC]
    if not in_win:
        return None

    alerts: list[str] = []
    is_futures = used_symbol is not None and "XAUUSD" not in used_symbol

    # --- W1: ゾーン初タッチ（先物のみの日は価格の線が乖離するためスキップ） ---
    zones = [] if is_futures else [
        (f"{lv:.0f}(キリ番)", lv) for lv in _rounds(close, close * 0.012)]
    if not is_futures and len(daily["close"]) >= 2:  # 末尾は当日進行中、前日=-2
        zones.append((f"{daily['high'][-2]:.0f}(前日高値)", daily["high"][-2]))
        zones.append((f"{daily['low'][-2]:.0f}(前日安値)", daily["low"][-2]))
    for name, lv in zones:
        touched_now = any(
            intr["low"][i] <= lv <= intr["high"][i] for i in in_win
        )
        touched_before = any(
            intr["low"][i] <= lv <= intr["high"][i] for i in before
        )
        if touched_now and not touched_before:
            side = "上抜け" if close > lv else ("下抜け" if close < lv else "タッチ")
            alerts.append(
                f"・{name} に本日初タッチ（現在{close:.0f}、{side}方向）\n"
                f"　→ ストップ集中帯。ヒゲと加速の両方を想定、この直外の損切りは危険"
            )

    # --- W2: レンジ拡張 ---
    if gvz_val:
        expected = close * gvz_val / math.sqrt(252) / 100  # 1σ日次レンジ($)
        run_hi = run_lo = None
        first_i = None
        for i in range(len(intr["ts"])):
            run_hi = intr["high"][i] if run_hi is None else max(run_hi, intr["high"][i])
            run_lo = intr["low"][i] if run_lo is None else min(run_lo, intr["low"][i])
            if (run_hi - run_lo) > 1.2 * expected:
                first_i = i
                break
        if first_i is not None and intr["ts"][first_i] >= now - WINDOW_SEC:
            rng = max(intr["high"]) - min(intr["low"])
            alerts.append(
                f"・本日レンジ${rng:.0f}が想定(±${expected:.0f})の120%を超過\n"
                f"　→ ボラ拡張日の可能性。サイズ縮小・ストップ拡幅を検討"
            )

    if not alerts:
        return None
    header = "🔔 見張り速報（自動検知）"
    footer = "※方向(上下)は言いません。事実の通知のみです。"
    return "\n".join([header, *alerts, footer])

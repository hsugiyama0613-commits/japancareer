"""取得データを「金取引お天気」レポート（日本語・スマホ1画面）に変換する。

方針（プロジェクト共通ルール）:
- 方向（上がる/下がる・買い/売り）は一切出力しない
- 出せる数値には出典と規模（percentile・n）を添える
- 未検証の指標（クラウディング等）は「参考」と明示する
"""
from __future__ import annotations

import math
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

MAJOR_KEYWORDS = ("CPI", "Non-Farm", "Nonfarm", "FOMC", "Fed", "PCE", "Unemployment")


def _percentile(series: list[float], value: float) -> int:
    if not series:
        return 50
    return round(100 * sum(1 for v in series if v <= value) / len(series))


def _vol_label(pct: int) -> str:
    if pct >= 90:
        return "非常に高い"
    if pct >= 70:
        return "高め"
    if pct >= 30:
        return "普通"
    return "低め"


def _round_levels(price: float, radius: float) -> list[float]:
    """現値の周辺にある「キリのいい数字」（50刻み）を返す。"""
    grid = 50.0
    lo = math.floor((price - radius) / grid) * grid
    levels = []
    x = lo
    while x <= price + radius:
        if abs(x - price) > 1e-9:
            levels.append(x)
        x += grid
    return levels


def _weather(events_today: list[dict], vol_pct: int | None, crowd_pct: int | None) -> tuple[str, str]:
    """総合天気を決める。ルールは単純・透明に保つ。"""
    major = [e for e in events_today if any(k.lower() in e["title"].lower() for k in MAJOR_KEYWORDS)]
    high = [e for e in events_today if e["impact"] == "High"]
    score = 0
    if major:
        score += 3
    elif high:
        score += 2
    elif events_today:
        score += 1
    if vol_pct is not None and vol_pct >= 90:
        score += 2
    elif vol_pct is not None and vol_pct >= 70:
        score += 1
    if crowd_pct is not None and (crowd_pct >= 85 or crowd_pct <= 15):
        score += 1
    if score >= 4:
        return "⛈", "嵐（大荒れ警戒日）"
    if score >= 3:
        return "🌧", "雨（荒れやすい日）"
    if score >= 2:
        return "☁️", "曇り（注意して取引）"
    if score >= 1:
        return "🌤", "晴れ時々曇り"
    return "☀️", "晴れ（平常運転）"


def build_report(data: dict, now: datetime | None = None, demo: bool = False) -> str:
    now = (now or datetime.now(tz=JST)).astimezone(JST)
    gvz, price, cot, gld = data["gvz"], data["price"], data["cot"], data["gld"]
    calendar = data["calendar"]
    lines: list[str] = []

    # --- ボラ水準と想定レンジ ---
    vol_pct = None
    range_line = "・想定レンジ: データ取得失敗のため算出不可"
    if gvz:
        values = [v for _, v in gvz["series"]]
        vol_pct = _percentile(values, gvz["value"])
        daily_sigma = gvz["value"] / math.sqrt(252)  # 年率IV→日次1σ%
        range_line = (
            f"・ボラ水準: {_vol_label(vol_pct)}"
            f"（GVZ {gvz['value']:.1f} / 過去{vol_pct}%タイル・n={len(values)}日）\n"
            f"・今日の想定変動: ±{daily_sigma:.2f}%が目安（3日に2日はこの範囲）"
        )
        if price:
            rng = price["close"] * daily_sigma / 100
            range_line += f" ≒ ±${rng:.0f}"

    # --- 今日と今週のイベント ---
    events_today: list[dict] = []
    ev_lines: list[str] = []
    if calendar is not None:
        week = sorted(calendar, key=lambda e: e["utc"])
        for e in week:
            jst = e["utc"].astimezone(JST)
            if jst.date() == now.date():
                events_today.append(e)
        shown = 0
        for e in week:
            jst = e["utc"].astimezone(JST)
            if jst < now - timedelta(hours=3) or e["impact"] != "High":
                continue
            mark = "🚫" if jst.date() == now.date() else "・"
            wd_e = "月火水木金土日"[jst.weekday()]
            ev_lines.append(f"{mark} {jst:%-m/%-d}({wd_e}) {jst:%H:%M} {e['title']}")
            shown += 1
            if shown >= 5:
                break
        if not ev_lines:
            ev_lines.append("・今週の残りに重要発表なし")
    else:
        ev_lines.append("・カレンダー取得失敗（発表予定は手動確認を）")

    # --- クラウディング（参考情報） ---
    crowd_pct = cot["percentile"] if cot else None
    if cot:
        note = ""
        if crowd_pct >= 85:
            note = " → 買いが混雑。崩れた時の下げ幅拡大に注意（量を控えめに）"
        elif crowd_pct <= 15:
            note = " → 売りが混雑。踏み上げ時の上げ幅拡大に注意（量を控えめに）"
        cot_line = (
            f"・大口の混み具合(参考): {crowd_pct}%タイル"
            f"（{cot['date']}時点・週次n={cot['n_weeks']}）{note}"
        )
    else:
        cot_line = "・大口の混み具合: 取得失敗"

    gld_line = ""
    if gld:
        arrow = "流入" if gld["change_5d"] >= 0 else "流出"
        gld_line = (
            f"・金ETFマネー: 直近5日 {gld['change_5d']:+.1f}t の{arrow}"
            f"（保有{gld['tonnes']:.0f}t / {gld['date']}時点）"
        )

    # --- ヒゲ注意ゾーン ---
    zone_lines: list[str] = []
    if price:
        radius = price["close"] * 0.012
        zones = [(f"{lv:.0f}(キリ番)", lv) for lv in _round_levels(price["close"], radius)]
        zones += [(f"{price['high']:.0f}(前日高値)", price["high"]),
                  (f"{price['low']:.0f}(前日安値)", price["low"])]
        zones.sort(key=lambda z: abs(z[1] - price["close"]))
        names = [z[0] for z in zones[:4]]
        zone_lines.append("・" + " / ".join(names))
        zone_lines.append("　→ この直外に損切りを置かない。接近時はヒゲ想定")
    else:
        zone_lines.append("・価格取得失敗のため算出不可")

    icon, label = _weather(events_today, vol_pct, crowd_pct)
    if gvz is None and calendar is None:
        icon, label = "❓", "判定不能（データ取得失敗。手動確認を）"

    wd = "月火水木金土日"[now.weekday()]
    header = f"{icon} 金取引お天気 {now:%-m/%-d}({wd}) {now:%H:%M}"
    if demo:
        header += "【デモ・サンプルデータ】"

    lines.append(header)
    lines.append(f"総合: {label}")
    lines.append("")
    lines.append("【今日の空模様】")
    lines.append(range_line)
    lines.append(cot_line)
    if gld_line:
        lines.append(gld_line)
    lines.append("")
    lines.append("【重要発表（🚫=前後15分は新規禁止）】")
    lines.extend(ev_lines)
    lines.append("")
    lines.append("【ヒゲ注意ゾーン（損切りを置かない場所）】")
    lines.extend(zone_lines)
    lines.append("")
    lines.append("【時間帯メモ(日本時間)】")
    lines.append("・6-7時 スプレッド拡大帯(注文禁止) / 8-15時 板薄め")
    lines.append("・16時- ロンドン / 21-24時 最も厚い時間帯")
    lines.append("")
    lines.append("※方向(上下)は言いません。混み具合等は検証前の参考情報です。")

    errs = data.get("errors") or []
    if errs:
        # 通知には短い一言だけ。詳細はActionsのログにのみ出す
        names = sorted({e.split("取得失敗", 1)[0] for e in errs})
        lines.append(f"（{ '・'.join(names) } は本日取得できず。他は正常）")
        print("fetch errors: " + " / ".join(errs), file=sys.stderr)

    return "\n".join(lines)

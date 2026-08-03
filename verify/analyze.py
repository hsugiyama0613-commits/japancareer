"""XAUUSD 1分足からの一括検証（H3/H7/H8/H9）。

プロジェクト共通ルール:
- すべての統計に n を併記。n<30 は警告。
- レジーム分割（〜2021 / 2022-23 / 2024〜）で安定性を確認。
- 方向の予測はしない。出すのは分布・確率・条件付き統計のみ。

入力: data/dl/*.csv (dukascopy-node 出力: timestamp_ms,open,high,low,close[,volume])
出力: verify/results/summary.md, verify/results/stats.json
"""
from __future__ import annotations

import glob
import json
import os
import sys

import numpy as np
import pandas as pd

OUT_DIR = "verify/results"
REGIMES = [("〜2021", None, 2021), ("2022-23", 2022, 2023), ("2024〜", 2024, None)]


def load() -> pd.DataFrame:
    files = sorted(glob.glob("data/dl/*.csv"))
    if not files:
        sys.exit("no csv found under data/dl/")
    frames = []
    for f in files:
        df = pd.read_csv(f)
        df.columns = [c.strip().lower() for c in df.columns]
        ts_col = next(c for c in df.columns if "time" in c or c == "ts")
        df = df.rename(columns={ts_col: "ts"})
        frames.append(df[["ts", "open", "high", "low", "close"]])
    d = pd.concat(frames, ignore_index=True).drop_duplicates("ts").sort_values("ts")
    d["dt"] = pd.to_datetime(d["ts"], unit="ms", utc=True)
    d = d.set_index("dt")
    d = d[~d.index.duplicated()]
    print(f"loaded {len(d):,} bars: {d.index[0]} .. {d.index[-1]}")
    return d


def regime_of(year: int) -> str:
    for name, lo, hi in REGIMES:
        if (lo is None or year >= lo) and (hi is None or year <= hi):
            return name
    return "?"


def warn_n(n: int) -> str:
    return f"n={n}" + ("⚠️n<30" if n < 30 else "")


def q(s, ps=(0.25, 0.5, 0.75)):
    return [round(float(s.quantile(p)), 4) for p in ps]


# ---------- H3: 時刻別ボラプロファイル ----------
def h3(d: pd.DataFrame) -> dict:
    ret = d["close"].pct_change().abs() * 100
    prof = {}
    for hour, s in ret.groupby(ret.index.hour):
        prof[int(hour)] = {"median_abs_ret_pct": round(float(s.median()), 5),
                           "n": int(s.count())}
    return {"hourly_abs_return_profile_utc": prof}


# ---------- 日次集計（共通） ----------
def daily(d: pd.DataFrame) -> pd.DataFrame:
    g = d.groupby(d.index.date)
    day = pd.DataFrame({
        "open": g["open"].first(), "close": g["close"].last(),
        "high": g["high"].max(), "low": g["low"].min(),
        "n_bars": g["close"].count(),
    })
    day.index = pd.to_datetime(day.index)
    day = day[day["n_bars"] > 300]  # 半日未満しかない日は除外
    day["range"] = day["high"] - day["low"]
    day["year"] = day.index.year
    day["regime"] = day["year"].map(regime_of)
    return day


# ---------- H7: 週末ギャップ ----------
def h7(d: pd.DataFrame, day: pd.DataFrame) -> dict:
    res = []
    days = day.index.to_list()
    for i in range(1, len(days)):
        prev, cur = days[i - 1], days[i]
        if cur.weekday() != 0 or (cur - prev).days < 2:
            continue  # 月曜×直前が金曜(週末を挟む)のみ
        fri_close = day.loc[prev, "close"]
        mon = d[d.index.date == cur.date()]
        if len(mon) < 300:
            continue
        gap = mon["open"].iloc[0] - fri_close
        gap_pct = gap / fri_close * 100
        # 埋め判定: 月曜中に金曜終値までタッチしたか
        if gap > 0:
            filled = bool((mon["low"] <= fri_close).any())
            t_fill = mon.index[mon["low"] <= fri_close][0] if filled else None
        else:
            filled = bool((mon["high"] >= fri_close).any())
            t_fill = mon.index[mon["high"] >= fri_close][0] if filled else None
        hours = ((t_fill - mon.index[0]).total_seconds() / 3600) if filled else None
        res.append({"date": str(cur.date()), "gap_pct": gap_pct,
                    "abs_gap_pct": abs(gap_pct), "filled_same_day": filled,
                    "hours_to_fill": hours, "regime": regime_of(cur.year)})
    df = pd.DataFrame(res)
    if df.empty:
        return {"error": "no weekend gaps found"}
    out = {"n_total": int(len(df))}
    df["bucket"] = pd.cut(df["abs_gap_pct"], [0, 0.1, 0.3, 0.6, 99],
                          labels=["~0.1%", "0.1-0.3%", "0.3-0.6%", "0.6%~"])
    for b, g in df.groupby("bucket", observed=True):
        out[str(b)] = {
            "n": warn_n(len(g)),
            "fill_rate_same_day": round(float(g["filled_same_day"].mean()), 3),
            "median_hours_to_fill": (
                round(float(g["hours_to_fill"].dropna().median()), 2)
                if g["hours_to_fill"].notna().any() else None),
        }
    out["by_regime_fill_rate"] = {
        r: {"n": warn_n(len(g)),
            "fill_rate_same_day": round(float(g["filled_same_day"].mean()), 3)}
        for r, g in df.groupby("regime")}
    return out


# ---------- H8: セッション別トレンド効率 ----------
SESSIONS_UTC = {"アジア(0-7 UTC)": (0, 7), "ロンドン(7-12 UTC)": (7, 12),
                "NY(12-21 UTC)": (12, 21)}


def h8(d: pd.DataFrame) -> dict:
    out = {"note": "効率=|正味|/経路長。1=一方向,0=完全往復。セッション境界は固定UTC(夏冬時間未補正)"}
    closes = d["close"]
    for name, (h0, h1) in SESSIONS_UTC.items():
        seg = d[(d.index.hour >= h0) & (d.index.hour < h1)]
        effs = []
        for date, g in seg.groupby(seg.index.date):
            c = g["close"]
            if len(c) < 60:
                continue
            path = c.diff().abs().sum()
            if path > 0:
                effs.append({"eff": abs(c.iloc[-1] - c.iloc[0]) / path,
                             "regime": regime_of(pd.Timestamp(date).year)})
        df = pd.DataFrame(effs)
        entry = {"n": warn_n(len(df)),
                 "quartiles": q(df["eff"]) if len(df) else None}
        entry["by_regime_median"] = {
            r: {"n": warn_n(len(g)), "median": round(float(g["eff"].median()), 3)}
            for r, g in df.groupby("regime")} if len(df) else {}
        out[name] = entry
    return out


# ---------- H9: NY前の消化率と、その後の追加変動 ----------
def h9(d: pd.DataFrame, day: pd.DataFrame) -> dict:
    """11:00 UTC(=夏20:00JST)時点の消化率 → その後の追加変動の分布。"""
    med_range = day["range"].rolling(60, min_periods=30).median().shift(1)
    res = []
    for date, row in day.iterrows():
        base = med_range.get(date)
        if base is None or np.isnan(base) or base <= 0:
            continue
        g = d[d.index.date == date.date()]
        before = g[g.index.hour < 11]
        after = g[g.index.hour >= 11]
        if len(before) < 200 or len(after) < 100:
            continue
        hi_b, lo_b = before["high"].max(), before["low"].min()
        consumed = (hi_b - lo_b) / base
        # その後の「追加変動」= 11:00以降に既存レンジ外へ広げた量 + 経路は使わずシンプルに
        ext_up = max(0.0, after["high"].max() - hi_b)
        ext_dn = max(0.0, lo_b - after["low"].min())
        res.append({"consumed": consumed, "extra": (ext_up + ext_dn) / base,
                    "extended": (ext_up + ext_dn) > 0.1 * base,
                    "regime": regime_of(date.year)})
    df = pd.DataFrame(res)
    if df.empty:
        return {"error": "insufficient data"}
    out = {"note": "consumed=11UTC時点の(高-安)/直近60日中央値レンジ。extra=その後のレンジ外拡張量(同基準比)",
           "n_total": int(len(df))}
    df["bucket"] = pd.cut(df["consumed"], [0, 0.5, 0.8, 1.1, 99],
                          labels=["~50%", "50-80%", "80-110%", "110%~"])
    for b, g in df.groupby("bucket", observed=True):
        out[str(b)] = {
            "n": warn_n(len(g)),
            "P(さらに10%以上拡張)": round(float(g["extended"].mean()), 3),
            "追加拡張quartiles": q(g["extra"]),
        }
    out["by_regime_P(拡張)"] = {
        r: {"n": warn_n(len(g)), "p": round(float(g["extended"].mean()), 3)}
        for r, g in df.groupby("regime")}
    return out


def main() -> None:
    d = load()
    day = daily(d)
    stats = {
        "meta": {"bars": int(len(d)), "days": int(len(day)),
                 "span": [str(d.index[0]), str(d.index[-1])]},
        "H3_時刻別ボラ": h3(d),
        "H7_週末ギャップ": h7(d, day),
        "H8_セッション効率": h8(d),
        "H9_消化率と追加変動": h9(d, day),
    }
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(f"{OUT_DIR}/stats.json", "w") as f:
        json.dump(stats, f, ensure_ascii=False, indent=1, default=str)

    md = ["# 一括検証結果（自動生成）", "",
          f"- 対象: XAUUSD 1分足 {stats['meta']['span'][0]} 〜 {stats['meta']['span'][1]}",
          f"- バー数 {stats['meta']['bars']:,} / 営業日 {stats['meta']['days']:,}",
          "- ルール: n併記・n<30は⚠️・レジーム分割。方向予測はしない。",
          "", "詳細な数値は stats.json を参照。", ""]
    with open(f"{OUT_DIR}/summary.md", "w") as f:
        f.write("\n".join(md))
    print(json.dumps(stats["H7_週末ギャップ"], ensure_ascii=False)[:500])
    print("done")


if __name__ == "__main__":
    main()

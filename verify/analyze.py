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
def group_by_date(d: pd.DataFrame) -> dict:
    """d[d.index.date == X] の全走査をループ内で繰り返すと273万行×日数で
    実行不能になるため、日付→バーの辞書を一度だけ作る。"""
    return {date: g for date, g in d.groupby(d.index.date)}


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
def h7(d: pd.DataFrame, day: pd.DataFrame, by_date: dict) -> dict:
    """金曜クローズ→週明け最初の気配のギャップと、週明けセッション内の埋め。

    週明けは日曜夜(UTC)に薄く再開するため、「金曜の最終バー」から
    「その後最初のバー」までを機械的にギャップとし、埋め判定は
    週明けから月曜終わりまでのバー全体で行う。
    """
    res = []
    days = day.index.to_list()
    checked = 0
    for i in range(1, len(days)):
        prev, cur = days[i - 1], days[i]
        if prev.weekday() != 4:  # 直前営業日が金曜=週末をまたぐ
            continue
        checked += 1
        fri_bars = by_date.get(prev.date())
        if fri_bars is None or fri_bars.empty:
            continue
        fri_close = float(fri_bars["close"].iloc[-1])
        fri_high = float(fri_bars["high"].max())
        fri_low = float(fri_bars["low"].min())
        fri_end = fri_bars.index[-1]
        from datetime import timedelta as _td
        cand = [prev.date() + _td(days=k) for k in range(1, 8)]  # 5営業日分
        frames = [by_date[c] for c in cand if c in by_date]
        if not frames:
            continue
        post = pd.concat(frames)
        post = post[post.index > fri_end]  # 週明け再開(日曜21-22UTC)以降
        if len(post) < 100:
            continue
        open0 = float(post["open"].iloc[0])
        gap = open0 - fri_close
        gap_pct = gap / fri_close * 100
        # フィード間誤差の帯(校正前の保守値0.1%)。帯を越えた到達のみ「確実」
        band = fri_close * 0.001
        if gap > 0:
            hit = post.index[post["low"] <= fri_close]
            hit_sure = post.index[post["low"] <= fri_close - band]
        else:
            hit = post.index[post["high"] >= fri_close]
            hit_sure = post.index[post["high"] >= fri_close + band]
        fill_ts = hit[0] if len(hit) else None
        # 月曜中=日本時間の月曜(金曜+3日)までに埋めたか
        fill_mon = fill_ts is not None and (
            fill_ts.tz_convert("Asia/Tokyo").date() - prev.date()).days <= 3
        # 確実判定: 誤差帯(±0.1%)を突き抜けた場合のみ。帯内で止まった週は「保留」
        sure_ts = hit_sure[0] if len(hit_sure) else None
        fill_mon_sure = sure_ts is not None and (
            sure_ts.tz_convert("Asia/Tokyo").date() - prev.date()).days <= 3
        hours = ((fill_ts - post.index[0]).total_seconds() / 3600) if fill_ts is not None else None
        # 定義B: 金曜の高値/安値と週明けの間の「真空地帯」。埋め=金曜高値(上窓)/安値(下窓)タッチ
        if open0 > fri_high:
            void_usd, hitB = open0 - fri_high, post.index[post["low"] <= fri_high]
        elif open0 < fri_low:
            void_usd, hitB = fri_low - open0, post.index[post["high"] >= fri_low]
        else:
            void_usd, hitB = None, None
        fillB_ts = hitB[0] if (hitB is not None and len(hitB)) else None
        fillB_mon = fillB_ts is not None and (
            fillB_ts.tz_convert("Asia/Tokyo").date() - prev.date()).days <= 3
        hoursB = ((fillB_ts - post.index[0]).total_seconds() / 3600) if fillB_ts is not None else None
        res.append({"date": str(cur.date()), "gap_usd": round(gap, 2),
                    "gap_pct": round(gap_pct, 3),
                    "abs_gap_pct": abs(gap_pct),
                    "fill_mon": fill_mon,
                    "fill_mon_sure": fill_mon_sure,
                    "fri_close": round(fri_close, 2),
                    "week_open": round(open0, 2),
                    "filled_at": str(fill_ts) if fill_ts is not None else None,
                    "fillB_mon": fillB_mon if void_usd is not None else None,
                    "filledB_at": str(fillB_ts) if fillB_ts is not None else None,
                    "fill_24h": hours is not None and hours <= 24,
                    "fill_48h": hours is not None and hours <= 48,
                    "fill_5d": hours is not None,
                    "hours_to_fill": round(hours, 1) if hours is not None else None,
                    "void_usd": round(void_usd, 2) if void_usd is not None else None,
                    "void_pct": (abs(void_usd / fri_close * 100)
                                 if void_usd is not None else None),
                    "voidB_24h": (hoursB is not None and hoursB <= 24)
                    if void_usd is not None else None,
                    "voidB_5d": (hoursB is not None) if void_usd is not None else None,
                    "hoursB": round(hoursB, 1) if hoursB is not None else None,
                    "regime": regime_of(cur.year)})
    df = pd.DataFrame(res)
    if df.empty:
        return {"error": "no weekend gaps found", "mondays_checked": checked}
    out = {"n_total": int(len(df)), "mondays_checked": checked}
    out["定義"] = ("窓=週明け再開の最初の気配(日曜21-22UTC=JST月曜6-7時) − 金曜最終終値。"
                  "埋め=その後5営業日以内に金曜終値へタッチ。%は金曜終値比")
    df["bucket"] = pd.cut(df["abs_gap_pct"], [0, 0.1, 0.3, 0.6, 99],
                          labels=["~0.1%", "0.1-0.3%", "0.3-0.6%", "0.6%~"])
    for b, g in df.groupby("bucket", observed=True):
        out[str(b)] = {
            "n": warn_n(len(g)),
            "月曜中埋め率(カレンダー判定)": round(float(g["fill_mon"].mean()), 3),
            "月曜中確実埋め率(誤差帯0.1%超過のみ)": round(
                float(g["fill_mon_sure"].mean()), 3),
            "24h以内埋め率": round(float(g["fill_24h"].mean()), 3),
            "48h以内埋め率": round(float(g["fill_48h"].mean()), 3),
            "5営業日以内埋め率": round(float(g["fill_5d"].mean()), 3),
            "埋め所要中央値h": (
                round(float(g["hours_to_fill"].dropna().median()), 1)
                if g["hours_to_fill"].notna().any() else None),
        }
    out["by_regime_24h埋め率"] = {
        r: {"n": warn_n(len(g)), "rate": round(float(g["fill_24h"].mean()), 3)}
        for r, g in df.groupby("regime")}
    # --- 定義B: 金曜高安と週明けの間の真空地帯（チャート実務の「窓」） ---
    vb = df[df["void_usd"].notna()].copy()
    outB = {"定義": "窓=金曜の高値(上窓)/安値(下窓)と週明け始値の間の真空地帯。"
                    "埋め=金曜高値/安値へのタッチ。金曜レンジ内で再開した週は窓なし",
            "窓が発生した週": warn_n(len(vb)),
            "窓なし(金曜レンジ内で再開)の週": int(len(df) - len(vb))}
    if len(vb):
        vb["bucketB"] = pd.cut(vb["void_pct"], [0, 0.1, 0.3, 0.6, 99],
                               labels=["~0.1%", "0.1-0.3%", "0.3-0.6%", "0.6%~"])
        for b, g in vb.groupby("bucketB", observed=True):
            outB[str(b)] = {
                "n": warn_n(len(g)),
                "月曜中埋め率(カレンダー判定)": round(float(g["fillB_mon"].mean()), 3),
                "24h以内埋め率": round(float(g["voidB_24h"].mean()), 3),
                "5営業日以内埋め率": round(float(g["voidB_5d"].mean()), 3),
                "埋め所要中央値h": (
                    round(float(g["hoursB"].dropna().median()), 1)
                    if g["hoursB"].notna().any() else None),
            }
        outB["直近5件"] = [
            {k: (None if isinstance(v, float) and np.isnan(v) else v)
             for k, v in r.items()}
            for r in vb.sort_values("date").tail(5)[
                ["date", "void_usd", "hoursB"]].to_dict("records")]
    out["定義B_高安基準の真空地帯"] = outB
    out["直近5件の実測"] = [
        {k: (None if isinstance(v, float) and np.isnan(v) else v)
         for k, v in r.items()}
        for r in df.sort_values("date").tail(5)[
            ["date", "fri_close", "week_open", "gap_usd", "fill_mon",
             "fill_mon_sure", "filled_at", "fillB_mon",
             "filledB_at"]].to_dict("records")]
    out["校正メモ"] = (
        "fri_close/week_openは当方(Dukascopy)の値。ユーザーのチャートの同値と"
        "並べたペアが溜まり次第、誤差帯0.1%を実測値に置換する。"
        "実測ペア1件目(2026-08-02週): 当方gap+30.06 vs ユーザーGOLD# +37.77 (差-7.7USD)")
    return out


# ---------- H8: セッション別トレンド効率 ----------
SESSIONS_UTC = {"アジア(0-7 UTC)": (0, 7), "ロンドン(7-12 UTC)": (7, 12),
                "NY(12-21 UTC)": (12, 21)}


def h8(d: pd.DataFrame) -> dict:
    """トレンド性 = |終値-始値| / (高値-安値)。1分経路ベースはノイズ支配で
    無意味だったため(初回実行の教訓)、トレーダー体感に対応する指標に変更。
    参考として15分足経路の効率も併記。"""
    out = {"note": "trendiness=|net|/(high-low) 1=一方向 0=行って来い。"
                   "path15=15分足経路での|net|/経路長。境界は固定UTC(夏冬未補正)"}
    for name, (h0, h1) in SESSIONS_UTC.items():
        seg = d[(d.index.hour >= h0) & (d.index.hour < h1)]
        rows = []
        for date, g in seg.groupby(seg.index.date):
            if len(g) < 60:
                continue
            hi, lo = g["high"].max(), g["low"].min()
            if hi <= lo:
                continue
            net = abs(g["close"].iloc[-1] - g["open"].iloc[0])
            c15 = g["close"].resample("15min").last().dropna()
            path15 = c15.diff().abs().sum()
            rows.append({
                "trendiness": net / (hi - lo),
                "path15": (net / path15) if path15 > 0 else None,
                "regime": regime_of(pd.Timestamp(date).year),
            })
        df = pd.DataFrame(rows)
        entry = {"n": warn_n(len(df))}
        if len(df):
            entry["trendiness_quartiles"] = q(df["trendiness"])
            entry["path15_median"] = round(float(df["path15"].dropna().median()), 3)
            entry["by_regime_trendiness_median"] = {
                r: {"n": warn_n(len(g)),
                    "median": round(float(g["trendiness"].median()), 3)}
                for r, g in df.groupby("regime")}
        out[name] = entry
    return out


# ---------- H9: NY前の消化率と、その後の追加変動 ----------
def h9(day: pd.DataFrame, by_date: dict) -> dict:
    """11:00 UTC(=夏20:00JST)時点の消化率 → その後の追加変動の分布。"""
    med_range = day["range"].rolling(60, min_periods=30).median().shift(1)
    res = []
    for date, row in day.iterrows():
        base = med_range.get(date)
        if base is None or np.isnan(base) or base <= 0:
            continue
        g = by_date.get(date.date())
        if g is None:
            continue
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


# ---------- H10: レンジブレイクのだまし率 ----------
def h10(day: pd.DataFrame, by_date: dict) -> dict:
    """前日高値/安値のブレイク後、規定時間内にレンジ内へ戻る割合（=だまし）。

    - ブレイク: 当日中に初めて前日高値を上抜く（または前日安値を下抜く）瞬間
    - だまし: そのブレイク水準を、判定時間内に終値ベースで逆方向へ抜け戻る
    - 併せて「その後の最大伸び」も出し、だましでない場合の伸び代を見る
    """
    horizons = {"30分": 30, "1時間": 60, "4時間": 240}
    rows = []
    days = day.index.to_list()
    for i in range(1, len(days)):
        prev, cur = days[i - 1], days[i]
        ph, pl = day.loc[prev, "high"], day.loc[prev, "low"]
        rng = ph - pl
        if rng <= 0:
            continue
        g = by_date.get(cur.date())
        if g is None or len(g) < 300:
            continue
        for side, level in (("up", ph), ("dn", pl)):
            if side == "up":
                idx = g.index[g["high"] > level]
            else:
                idx = g.index[g["low"] < level]
            if len(idx) == 0:
                continue
            t0 = idx[0]
            after = g[g.index > t0]
            if len(after) < 60:
                continue  # 判定余地のない終盤ブレイクは除外
            row = {
                "date": str(cur.date()),
                "side": side,
                "hour_utc": int(t0.hour),
                "regime": regime_of(cur.year),
            }
            for hname, mins in horizons.items():
                w = after[after.index <= t0 + pd.Timedelta(minutes=mins)]
                if len(w) < 5:
                    row[hname] = None
                    continue
                if side == "up":
                    row[hname] = bool((w["close"] < level).any())
                    row[f"伸び_{hname}"] = float(w["high"].max() - level) / rng
                else:
                    row[hname] = bool((w["close"] > level).any())
                    row[f"伸び_{hname}"] = float(level - w["low"].min()) / rng
            rows.append(row)
    df = pd.DataFrame(rows)
    if df.empty:
        return {"error": "no breakouts"}

    def rate(g, h):
        s = g[h].dropna()
        return round(float(s.mean()), 3) if len(s) else None

    out = {
        "note": "だまし=ブレイク後、判定時間内に終値がブレイク水準の逆側へ戻る。"
                "伸び=ブレイク水準からの最大到達幅÷前日レンジ",
        "n_total": int(len(df)),
        "全体": {h: {"だまし率": rate(df, h), "n": warn_n(int(df[h].notna().sum()))}
                for h in horizons},
        "方向別(1時間)": {
            s: {"だまし率": rate(g, "1時間"), "n": warn_n(len(g))}
            for s, g in df.groupby("side")},
        "レジーム別(1時間)": {
            r: {"だまし率": rate(g, "1時間"), "n": warn_n(len(g))}
            for r, g in df.groupby("regime")},
        "ブレイク時刻別(1時間, UTC)": {
            int(h): {"だまし率": rate(g, "1時間"), "n": warn_n(len(g))}
            for h, g in df.groupby("hour_utc") if len(g) >= 30},
        "伸び幅quartiles(4時間, 前日レンジ比)": q(df["伸び_4時間"].dropna()),
    }
    # だましでなかった場合の伸び（4時間）
    ok = df[df["4時間"] == False]  # noqa: E712
    if len(ok):
        out["だましでない場合の伸び(4時間)"] = {
            "n": warn_n(len(ok)), "quartiles": q(ok["伸び_4時間"].dropna())}
    return out


# ---------- H11: 両方向刈られ率（悪い場所でのエントリー） ----------
def h11(d: pd.DataFrame) -> dict:
    """ある地点で買いと売りを同じ損切り幅で両方入れたと仮定し、
    両方が判定時間内に損切りに達する確率（=その場所の悪さ）を測る。

    方向を一切仮定しないため「どっちに賭けても刈られる場所か」を純粋に測れる。
    レンジ内位置・レンジ幅/ノイズ比・損切り幅の3条件で層別する。
    """
    W, H = 240, 240  # 参照レンジ4時間 / 判定4時間
    high, low, close = d["high"], d["low"], d["close"]
    rh = high.rolling(W).max()
    rl = low.rolling(W).min()
    width = rh - rl
    hr60 = high.rolling(60).max() - low.rolling(60).min()  # 直近1時間のノイズ
    # 未来H分の高安（時間ギャップを跨ぐサンプルは後で除外）
    fut_h = high.iloc[::-1].rolling(H, min_periods=H).max().iloc[::-1].shift(-1)
    fut_l = low.iloc[::-1].rolling(H, min_periods=H).min().iloc[::-1].shift(-1)
    span_ok = (pd.Series(d.index, index=d.index).shift(-H) - pd.Series(d.index, index=d.index)
               ) <= pd.Timedelta(minutes=H + 30)

    base = pd.DataFrame({
        "close": close, "pos": (close - rl) / width,
        "w_ratio": width / hr60, "hr60": hr60,
        "fut_h": fut_h, "fut_l": fut_l,
        "year": d.index.year, "minute": d.index.minute,
    })
    base = base[span_ok & (base["minute"] % 30 == 0)].dropna()
    base = base[(base["hr60"] > 0) & base["pos"].between(0, 1)]
    if base.empty:
        return {"error": "no samples"}
    base["regime"] = base["year"].map(regime_of)
    base["pos_b"] = pd.cut(base["pos"], [0, .2, .4, .6, .8, 1.0],
                           labels=["下端0-20%", "20-40%", "中央40-60%",
                                   "60-80%", "上端80-100%"])
    base["w_b"] = pd.cut(base["w_ratio"], [0, 2, 3, 4, 99],
                         labels=["狭い(〜2倍)", "2-3倍", "3-4倍", "広い(4倍〜)"])

    out = {
        "note": "参照レンジ4時間・判定4時間。損切り幅は直近1時間の値幅(hr60)の倍率で指定。"
                "両方向刈られ=買いも売りも損切り到達。サンプルは重複するため実効nは表示より小さい",
        "n_samples": int(len(base)),
    }
    for k in (0.5, 1.0):
        s = base["hr60"] * k
        both = ((base["fut_l"] <= base["close"] - s)
                & (base["fut_h"] >= base["close"] + s))
        none = ((base["fut_l"] > base["close"] - s)
                & (base["fut_h"] < base["close"] + s))
        key = f"損切り={k}×直近1h値幅"
        out[key] = {
            "全体": {"両方向刈られ率": round(float(both.mean()), 3),
                     "どちらも無事率": round(float(none.mean()), 3),
                     "n": warn_n(int(len(both)))},
            "レンジ内位置別": {
                str(b): {"両方向刈られ率": round(float(both[base["pos_b"] == b].mean()), 3),
                         "n": warn_n(int((base["pos_b"] == b).sum()))}
                for b in base["pos_b"].cat.categories},
            "レンジ幅/ノイズ別": {
                str(b): {"両方向刈られ率": round(float(both[base["w_b"] == b].mean()), 3),
                         "n": warn_n(int((base["w_b"] == b).sum()))}
                for b in base["w_b"].cat.categories},
            "レジーム別": {
                r: {"両方向刈られ率": round(float(both[base["regime"] == r].mean()), 3),
                    "n": warn_n(int((base["regime"] == r).sum()))}
                for r in sorted(base["regime"].unique())},
        }
    return out


# ---------- H12/H13/H14: 4時間足のレンジブレイク構造 ----------
def _h4(d: pd.DataFrame) -> pd.DataFrame:
    g = d.resample("4h")
    bars = pd.DataFrame({
        "open": g["open"].first(), "high": g["high"].max(),
        "low": g["low"].min(), "close": g["close"].last(),
        "n": g["close"].count(),
    }).dropna()
    return bars[bars["n"] >= 60]  # 薄い/祝日バーを除外


def h12_14(d: pd.DataFrame) -> dict:
    """4時間足のレンジブレイクを実体抜け/ヒゲ戻しに分類し、その後を測る。

    - 参照レンジ: 直前6本(24時間)の高安
    - 追跡: 以降6本(24時間)。基準はブレイク足の終値(実際に入れる価格)
    - すべて無条件ベースライン(全バーで同じ測定)と併記する
    """
    bars = _h4(d)
    K, N, M = 6, 6, 12
    rh = bars["high"].shift(1).rolling(K).max().values
    rl = bars["low"].shift(1).rolling(K).min().values
    H, L, C = bars["high"].values, bars["low"].values, bars["close"].values
    years = bars.index.year
    ev, base, sweep = [], [], []

    for i in range(K, len(bars) - M):
        w = rh[i] - rl[i]
        if not np.isfinite(w) or w <= 0:
            continue
        fh, fl, fc = H[i+1:i+1+N].max(), L[i+1:i+1+N].min(), C[i+N]
        reg = regime_of(int(years[i]))
        base.append({"mfe": (fh - C[i]) / w, "mae": (C[i] - fl) / w,
                     "cont": bool(fc > C[i]), "trend": (fh - C[i]) / w >= 1.0,
                     "range_bound": bool(H[i+1:i+1+M].max() <= H[i]
                                         and L[i+1:i+1+M].min() >= L[i]),
                     "regime": reg})
        up, dn = H[i] > rh[i], L[i] < rl[i]
        if not (up or dn):
            continue
        if up and dn:  # 同一足で上下とも刈った
            sweep.append({"regime": reg, "when": "same_bar",
                          "range_bound": bool(H[i+1:i+1+M].max() <= H[i]
                                              and L[i+1:i+1+M].min() >= L[i])})
            continue
        if up:
            kind = "実体抜け" if C[i] > rh[i] else "ヒゲ戻し"
            mfe, mae = (fh - C[i]) / w, (C[i] - fl) / w
            cont = bool(fc > C[i])
            rebreak = bool((H[i+1:i+1+N] > H[i]).any())
            opp = bool((L[i+1:i+1+M] < rl[i]).any())
        else:
            kind = "実体抜け" if C[i] < rl[i] else "ヒゲ戻し"
            mfe, mae = (C[i] - fl) / w, (fh - C[i]) / w
            cont = bool(fc < C[i])
            rebreak = bool((L[i+1:i+1+N] < L[i]).any())
            opp = bool((H[i+1:i+1+M] > rh[i]).any())
        ev.append({"kind": kind, "dir": "上" if up else "下", "mfe": mfe,
                   "mae": mae, "cont": cont, "trend": mfe >= 1.0,
                   "rebreak": rebreak, "opp_break": opp, "regime": reg,
                   "range_bound": bool(H[i+1:i+1+M].max() <= H[i]
                                       and L[i+1:i+1+M].min() >= L[i])})

    if not ev:
        return {"error": "no breakout events"}
    e, b = pd.DataFrame(ev), pd.DataFrame(base)

    def agg(g):
        return {"n": warn_n(len(g)),
                "継続率(6本後)": round(float(g["cont"].mean()), 3),
                "トレンド開始率(MFE≧レンジ幅)": round(float(g["trend"].mean()), 3),
                "MFE中央値(レンジ幅比)": round(float(g["mfe"].median()), 3),
                "MAE中央値(レンジ幅比)": round(float(g["mae"].median()), 3)}

    out = {
        "note": "参照レンジ=直前6本(24h)の高安。基準はブレイク足の終値。追跡6本(24h)。"
                "MFE/MAEはレンジ幅比。コスト未考慮。",
        "n_breakouts": int(len(e)),
        "ベースライン(無条件・全バー)": {
            "n": warn_n(len(b)),
            "上昇継続率(6本後)": round(float(b["cont"].mean()), 3),
            "1レンジ幅上抜け率": round(float(b["trend"].mean()), 3),
            "MFE中央値": round(float(b["mfe"].median()), 3),
            "12本レンジ内滞在率": round(float(b["range_bound"].mean()), 3),
        },
        "H12_種類別": {k: agg(g) for k, g in e.groupby("kind")},
        "H12_種類×方向": {f"{k}/{dr}": agg(g)
                          for (k, dr), g in e.groupby(["kind", "dir"])},
        "H12_レジーム別(実体抜けのみ)": {
            r: agg(g) for r, g in e[e["kind"] == "実体抜け"].groupby("regime")},
        "H13_ヒゲ戻し後の再ブレイク": {
            "再ブレイク率(24h以内)": round(
                float(e[e["kind"] == "ヒゲ戻し"]["rebreak"].mean()), 3),
            "n": warn_n(int((e["kind"] == "ヒゲ戻し").sum())),
            "参考:実体抜けの追随更新率": round(
                float(e[e["kind"] == "実体抜け"]["rebreak"].mean()), 3),
        },
        "H14_両側刈り": {
            "同一足で両側刈り": {
                "n": warn_n(len(sweep)),
                "その後48hレンジ内滞在率": (
                    round(float(pd.DataFrame(sweep)["range_bound"].mean()), 3)
                    if sweep else None)},
            "片側ブレイク後48h以内に反対側も破った率": round(
                float(e["opp_break"].mean()), 3),
            "両側破った場合の48hレンジ内滞在率": round(
                float(e[e["opp_break"]]["range_bound"].mean()), 3),
            "破らなかった場合": round(
                float(e[~e["opp_break"]]["range_bound"].mean()), 3),
        },
    }
    return out


def main() -> None:
    d = load()
    day = daily(d)
    by_date = group_by_date(d)
    from datetime import datetime, timezone
    age_days = (datetime.now(timezone.utc) - d.index[-1].to_pydatetime()).days
    stats = {
        "meta": {"bars": int(len(d)), "days": int(len(day)),
                 "span": [str(d.index[0]), str(d.index[-1])],
                 "データ鮮度警告": (f"⚠️最終バーが{age_days}日前。末尾欠損の疑い"
                                    if age_days > 4 else "OK(最新)")},
        "H3_時刻別ボラ": h3(d),
        "H7_週末ギャップ": h7(d, day, by_date),
        "H8_セッション効率": h8(d),
        "H9_消化率と追加変動": h9(day, by_date),
        "H10_ブレイクだまし率": h10(day, by_date),
        "H11_両方向刈られ率": h11(d),
        "H12-14_4H_ブレイク構造": h12_14(d),
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

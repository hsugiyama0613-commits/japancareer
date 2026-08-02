"""無料データソースからの取得。すべて標準ライブラリのみ・APIキー不要。

各関数は取得失敗時に None を返し、エラーは errors リストに積む。
一部が落ちても天気予報全体は残りのデータで組み立てる（縮退運転）。
"""
from __future__ import annotations

import csv
import io
import json
import time
import urllib.request
from datetime import datetime, timezone

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
TIMEOUT = 45

GVZ_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=GVZCLS"
STOOQ_URL = "https://stooq.com/q/d/l/?s=xauusd&i=d"
COT_URL = (
    "https://publicreporting.cftc.gov/resource/72hh-3qpy.json"
    "?cftc_contract_market_code=088691"
    "&$order=report_date_as_yyyy_mm_dd%20DESC&$limit=1100"
)
GLD_URL = "https://www.spdrgoldshares.com/assets/dynamic/GLD/GLD_US_archive_EN.csv"
FF_CAL_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"


def _get(url: str, tries: int = 3) -> bytes:
    last: Exception | None = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return resp.read()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (i + 1))
    raise last  # type: ignore[misc]


def _head(text: str) -> str:
    """エラーメッセージ用に応答の先頭を短く返す（改行・非印字文字は除去）。"""
    clean = "".join(c for c in text[:120] if c.isprintable())
    return clean[:80]


def _yahoo_chart(symbol: str, range_: str, interval: str) -> dict:
    """Yahoo Finance chart API。キー不要・サーバーからのアクセスに比較的寛容。

    戻り値: {"dates": [ISO日付], "open": [...], "high": [...], "low": [...], "close": [...]}
    欠損(None)は落とす。
    """
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?range={range_}&interval={interval}"
    )
    data = json.loads(_get(url).decode("utf-8", "replace"))
    result = data["chart"]["result"][0]
    ts = result["timestamp"]
    q = result["indicators"]["quote"][0]
    out: dict = {"dates": [], "open": [], "high": [], "low": [], "close": []}
    for i, t in enumerate(ts):
        c = q["close"][i]
        if c is None:
            continue
        out["dates"].append(
            datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d")
        )
        out["close"].append(c)
        out["open"].append(q["open"][i] if q["open"][i] is not None else c)
        out["high"].append(q["high"][i] if q["high"][i] is not None else c)
        out["low"].append(q["low"][i] if q["low"][i] is not None else c)
    if not out["close"]:
        raise ValueError(f"no data for {symbol}")
    return out


def fetch_gvz(errors: list[str]) -> dict | None:
    """CBOE GVZ（金のインプライドボラ指数）。

    第一候補: Yahoo ^GVZ（10年分・サーバーからでも通りやすい）
    第二候補: FRED CSV（実行元IPによってはタイムアウトする）
    """
    try:
        ch = _yahoo_chart("%5EGVZ", "10y", "1d")
        series = list(zip(ch["dates"], ch["close"]))
        return {"series": series, "date": series[-1][0], "value": series[-1][1]}
    except Exception as e_yahoo:  # noqa: BLE001
        try:
            text = _get(GVZ_URL).decode("utf-8", "replace")
            rows = list(csv.reader(io.StringIO(text)))
            series = [
                (r[0], float(r[1]))
                for r in rows[1:]
                if len(r) >= 2 and r[1] not in (".", "")
            ]
            if not series:
                raise ValueError("empty series")
            return {"series": series, "date": series[-1][0], "value": series[-1][1]}
        except Exception as e:  # noqa: BLE001
            errors.append(
                f"GVZ取得失敗: yahoo={type(e_yahoo).__name__}:{e_yahoo} / "
                f"fred={type(e).__name__}:{e}"
            )
            return None


def fetch_price(errors: list[str]) -> dict | None:
    """金価格の日足。前日高値安値と直近終値に使う。

    第一候補: Yahoo XAUUSD=X（スポット）、駄目なら GC=F（COMEX先物・ほぼ同水準）。
    """
    last_err: Exception | None = None
    for symbol in ("XAUUSD%3DX", "GC%3DF"):
        try:
            ch = _yahoo_chart(symbol, "5d", "1d")
            i = len(ch["close"]) - 1
            return {
                "date": ch["dates"][i],
                "open": ch["open"][i],
                "high": ch["high"][i],
                "low": ch["low"][i],
                "close": ch["close"][i],
            }
        except Exception as e:  # noqa: BLE001
            last_err = e
    errors.append(f"価格取得失敗: {type(last_err).__name__}: {last_err}")
    return None


def _find_key(row: dict, *needles: str) -> str | None:
    for k in row:
        lk = k.lower()
        if all(n in lk for n in needles):
            return k
    return None


def fetch_cot(errors: list[str]) -> dict | None:
    """CFTC COT（金・Disaggregated先物、週次）。Socrata API、認証不要。

    フィールド名はSocrata側の命名揺れに備えて部分一致で解決する。
    """
    try:
        data = json.loads(_get(COT_URL).decode("utf-8", "replace"))
        if not data:
            raise ValueError("empty response")
        r0 = data[0]
        k_long = _find_key(r0, "m_money", "long")
        k_short = _find_key(r0, "m_money", "short")
        k_oi = _find_key(r0, "open_interest_all") or _find_key(r0, "open_interest")
        k_date = _find_key(r0, "report_date")
        if not (k_long and k_short and k_oi and k_date):
            raise ValueError(f"fields not found in keys: {list(r0)[:10]}...")
        hist = []
        for r in data:
            try:
                net = float(r[k_long]) - float(r[k_short])
                oi = float(r[k_oi])
                if oi > 0:
                    hist.append((r[k_date][:10], net / oi))
            except (KeyError, ValueError, TypeError):
                continue
        if not hist:
            raise ValueError("no parsable rows")
        latest = hist[0]
        values = [v for _, v in hist]
        rank = sum(1 for v in values if v <= latest[1]) / len(values)
        return {
            "date": latest[0],
            "net_over_oi": latest[1],
            "percentile": round(rank * 100),
            "n_weeks": len(values),
        }
    except Exception as e:  # noqa: BLE001
        errors.append(f"COT取得失敗: {type(e).__name__}: {e}")
        return None


def fetch_gld(errors: list[str]) -> dict | None:
    """GLD（最大の金ETF）の保有トン数。日次CSV全履歴。

    CSVの先頭に前置き行があるため、ヘッダー行を 'Tonnes' 含有で探す。
    """
    try:
        text = _get(GLD_URL).decode("utf-8", "replace")
        lines = text.splitlines()
        header_i = next(
            (i for i, ln in enumerate(lines) if "tonnes" in ln.lower()), None
        )
        if header_i is None:
            raise ValueError(f"'tonnes'列が見つからない (body: {_head(text)})")
        rows = list(csv.reader(io.StringIO("\n".join(lines[header_i:]))))
        header = [h.strip().lower() for h in rows[0]]
        t_col = next(i for i, h in enumerate(header) if "tonnes" in h)
        series = []
        for r in rows[1:]:
            if len(r) <= t_col:
                continue
            try:
                series.append((r[0].strip(), float(r[t_col].replace(",", ""))))
            except ValueError:
                continue
        if len(series) < 6:
            raise ValueError("too few rows")
        latest_d, latest_v = series[-1]
        week_ago_v = series[-6][1]
        return {
            "date": latest_d,
            "tonnes": latest_v,
            "change_5d": latest_v - week_ago_v,
        }
    except Exception as e:  # noqa: BLE001
        errors.append(f"GLD取得失敗: {type(e).__name__}: {e}")
        return None


def fetch_calendar(errors: list[str]) -> list[dict] | None:
    """ForexFactory 今週の経済指標カレンダー（forecast/previousのみ、actualなし）。

    レート制限が厳しい（5分2回）ため、1日1回の朝実行でのみ叩くこと。
    """
    try:
        data = json.loads(_get(FF_CAL_URL).decode("utf-8", "replace"))
        events = []
        for ev in data:
            if ev.get("country") not in ("USD",):
                continue
            if ev.get("impact") not in ("High", "Medium"):
                continue
            try:
                dt = datetime.fromisoformat(ev["date"])
            except ValueError:
                continue
            events.append(
                {
                    "title": ev.get("title", "?"),
                    "impact": ev["impact"],
                    "utc": dt.astimezone(timezone.utc),
                    "forecast": ev.get("forecast", ""),
                    "previous": ev.get("previous", ""),
                }
            )
        return events
    except Exception as e:  # noqa: BLE001
        errors.append(f"カレンダー取得失敗: {type(e).__name__}: {e}")
        return None


def fetch_all() -> dict:
    errors: list[str] = []
    return {
        "gvz": fetch_gvz(errors),
        "price": fetch_price(errors),
        "cot": fetch_cot(errors),
        "gld": fetch_gld(errors),
        "calendar": fetch_calendar(errors),
        "errors": errors,
    }

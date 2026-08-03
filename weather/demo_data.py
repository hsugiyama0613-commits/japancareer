"""デモ用サンプルデータ。

このリポジトリの開発環境は外部データ取得が遮断されているため、
出力フォーマットの確認用に「それらしい形」のダミーを同梱する。
数値は実在の相場値ではない（レポートにも【デモ】と明示される）。
発表予定のみ実在の日時（2026年8月の米雇用統計・CPI、BLS公表スケジュール）を使用。
"""
from __future__ import annotations

import math
import random
from datetime import datetime, timezone


def demo_payload() -> dict:
    rng = random.Random(20260802)
    # GVZらしい系列: 平均18前後への回帰 + ノイズ。直近値24.8が高めの位置に来る
    series = []
    v = 18.0
    for i in range(3000):
        v = max(9.0, v + 0.05 * (18.0 - v) + rng.gauss(0, 0.8))
        series.append((f"day{i}", v))
    series[-1] = ("2026-07-31", 24.8)
    return {
        "gvz": {"series": series, "date": "2026-07-31", "value": 24.8},
        "price": {
            "date": "2026-07-31",
            "open": 3341.0,
            "high": 3368.0,
            "low": 3322.0,
            "close": 3347.0,
        },
        "cot": {
            "date": "2026-07-28",
            "net_over_oi": 0.31,
            "percentile": 87,
            "n_weeks": 1043,
        },
        "gld": {"date": "2026-07-31", "tonnes": 1012.0, "change_5d": +8.4},
        "intraday": {
            "open": 3341.0,
            "close": 3352.0,
            "high": 3358.0,
            "low": 3330.0,
            "efficiency": 0.18,
            "n_bars": 132,
        },
        "calendar": [
            {
                "title": "Non-Farm Employment Change",
                "impact": "High",
                "utc": datetime(2026, 8, 7, 12, 30, tzinfo=timezone.utc),
                "forecast": "",
                "previous": "",
            },
            {
                "title": "Unemployment Rate",
                "impact": "High",
                "utc": datetime(2026, 8, 7, 12, 30, tzinfo=timezone.utc),
                "forecast": "",
                "previous": "",
            },
            {
                "title": "CPI m/m",
                "impact": "High",
                "utc": datetime(2026, 8, 12, 12, 30, tzinfo=timezone.utc),
                "forecast": "",
                "previous": "",
            },
        ],
        "errors": [],
    }

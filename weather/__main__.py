"""金取引お天気 エントリーポイント。

使い方:
    python -m weather            # 実データを取得して標準出力に表示
    python -m weather --demo     # サンプルデータで表示（ネット不要）
    python -m weather --post     # 取得して Discord に送信 (要 DISCORD_WEBHOOK_URL)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

from .report import build_report


def post_discord(text: str) -> None:
    url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not url:
        raise SystemExit("環境変数 DISCORD_WEBHOOK_URL が未設定です")
    if "/api/webhooks/" not in url:
        # discord.com / discordapp.com どちらの正規形式も通す。
        # ここに来るのは招待リンク(discord.gg)等が入っている場合のみ。
        raise SystemExit(
            "登録されたURLがDiscordウェブフックの形式ではありません"
            "（招待リンク等の可能性）。チャンネル設定→連携サービス→"
            "ウェブフックのURL(discord.com/api/webhooks/...)を登録してください"
        )
    body = json.dumps({"content": text[:1990]}).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "gold-weather/0.1 (+github-actions; personal use)",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read()[:200].decode("utf-8", "replace")
        raise SystemExit(f"Discord送信失敗: HTTP {e.code}: {detail}") from e


def main() -> None:
    ap = argparse.ArgumentParser(prog="weather")
    ap.add_argument("--demo", action="store_true", help="サンプルデータで表示")
    ap.add_argument("--post", action="store_true", help="Discordに送信")
    args = ap.parse_args()

    if args.demo:
        from .demo_data import demo_payload
        from datetime import datetime
        from zoneinfo import ZoneInfo

        now = datetime(2026, 8, 7, 7, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
        text = build_report(demo_payload(), now=now, demo=True)
    else:
        from .fetch import fetch_all

        text = build_report(fetch_all())

    print(text)
    if args.post:
        post_discord(text)
        print("\n[Discordに送信しました]", file=sys.stderr)


if __name__ == "__main__":
    main()

# Phase 0 報告書：既存ソリューション調査（0-1 / 0-2）

調査日: 2026-08-02
調査方法: 各サービスの一次情報（公式docs・GitHubソースコード・PyPIメタデータ・検索インデックス）。
本報告をもって Phase 0 の停止条件に従い停止する。Phase 1 への進行判断は指示待ち。

---

## 前提として判明した重要な環境制約

**この Claude Code リモート環境のネットワークポリシーは、本プロジェクトに必要なデータAPIをすべて遮断している。**

| 接続先 | 結果 |
|---|---|
| Binance / Coinbase / Kraken / Bitstamp（価格API） | 403（遮断） |
| FRED / ALFRED（api.stlouisfed.org）、BLS | 403（遮断） |
| jblanked.com、faireconomy.media（ForexFactory）、FMP、fxmacrodata.com | 403（遮断） |
| WebSearch（検索）、PyPI、raw.githubusercontent.com | 利用可 |

- curl・WebFetch とも同一の遮断。サイト側の障害ではなく環境側のegressポリシー。
- したがって **0-2 の「実データで今すぐ答えを出す」テストは本環境からは実行不可能**。0-1（仕様調査）のみ完遂した。
- Phase 1 に進む場合の選択肢：(a) ローカルマシンで実行、(b) この環境のネットワークポリシーを緩和（環境設定で許可ドメイン追加 or 無制限化）。

---

## 0-1. 既存ソリューションの現況（2026年8月時点）

### 1行サマリ（報告フォーマット指定分）

- **JBlanked News API**: 無料キーで イベント別の Actual/Forecast/Previous + 発表後1分/30分/1時間の値動き履歴・的中率 まで取れるが、無料枠はレート制限（1日1回〜24回、現行値は要ログイン確認）が実質の壁。対象は8法定通貨のFXイベントのみで **XAUUSD/BTCUSD は対象外**。
- **FMP**: economic calendar に `estimate`（市場予想）/`actual`/`previous` フィールドは確実に存在（1リクエスト最大3ヶ月幅）が、無料枠(250req/日)でこのエンドポイントが叩けるかは情報が矛盾しており実キーでの検証が必要。**1分足履歴は Ultimate $149/月でしか取れず、無料枠では不可**。公式MCPサーバーあり（ホスト型、`financialmodelingprep.com/mcp?apikey=`）。
- **FXMacroData MCP**: 実在・稼働中（2025年11月〜のBeta）。release_calendar / 指標時系列 / COT / 商品価格をMCPで提供。**USD指標はキー不要・100req/日で無料**、非USD・COT・商品は $25/月。「イベントリプレイ」はホステッドMCP Apps側の機能で、**発表後の価格反応の具体的データ形式は未確認**。
- **ForexFactory 週次JSON**: `ff_calendar_thisweek.json` は稼働中（forecast/previous/impact あり）だが **actual は実質常に空、過去週の取得は不可（lastweek/nextweek は404）**。代わりに Hugging Face に **2007〜2025年4月の全履歴CSV（actual込み）** が無料で存在（研究用途限定）。制限は5分に2リクエスト・ブラウザUA必須。
- **arch / finmarketpy**: `arch` は v8.0.0（2025-10）で活発にメンテ、GARCH(1,1)/GJR-GARCH を標準搭載、そのまま採用可。`finmarketpy` は EventStudy クラスを持つが半stale（機能追加は2025年3月が最後、`numpy<2` ピンで arch 8 と共存困難）— **採用せず、イベントスタディのロジック参考のみに留めるのが妥当**。

### 詳細

#### JBlanked News API（jblanked.com）
- 稼働中（高確度）。公式Pythonライブラリ `jb-news` は 2026-05-14 に v2.3.4 リリース。APIベースURLは `https://www.jblanked.com/news/api/`（news.jblanked.com というサブドメインは確認できず）。
- **イベント別の発表後1分/30分/1時間の値動き履歴：あり**（Event History エンドポイントが Strength/Quality/Projection/Outcome + 1min/30min/1hr price action を返すと公式docsに明記）。ML エンドポイントは13種の結果パターン（Actual>Forecast>Previous 等）ごとの Bullish/Bearish 傾向と時間枠別 Accuracy を返す。
- Actual/Forecast/Previous 全イベント付き。対象は USD/EUR/GBP/AUD/CAD/CHF/JPY/NZD の8通貨のFXイベント。ソースは MQL5/ForexFactory/FxStreet から選択。
- 認証：APIキー必須（無料アカウントで取得、`Authorization: Api-Key` ヘッダ）。
- 無料枠：レート制限の記述に変遷あり（「5分に1回」→「1日1回に削減」→「1日24回+クレジット課金」）。**現行値はログインして changelog を直接確認する必要がある**。いずれにせよバックテスト用の一括取得には厳しい。

#### Financial Modeling Prep（FMP）
- 公式MCPサーバーあり：ホスト型 `https://financialmodelingprep.com/mcp?apikey=KEY`（GitHub公開はなし）。コミュニティ製（imbenrabi 版、約250ツール）が実質標準。
- economic calendar（`/stable/economic-calendar`）：`estimate` / `actual` / `previous` / `impact` を含む（OpenBB の実装コードで一次確認）。1リクエストの日付範囲は最大3ヶ月。
- 無料枠：250req/日・500MB/30日・EOD中心・履歴約5年。**経済カレンダーが無料枠に含まれるかは情報が矛盾（要実キー検証）**。
- 1分足：エンドポイントは forex/commodities とも存在するが、フル履歴は Ultimate（$149/月）。**無料枠では1分足不可**。

#### FXMacroData MCP
- 実在。pip の `mcp-server-fxmacrodata` v0.1.0(Beta)。ローカル版8ツール：release_calendar / indicator_query（発表日・値・前回値、76指標超）/ forex / cot_data / commodities / market_sessions ほか。
- 無料：**USD指標はキー不要（100req/日）**。非USD・COT・商品・FXレートは APIキー（Professional $25/月〜、14日トライアル）。
- 履歴深度：インフレ1995年〜、雇用1999年〜など。
- 「event replay」：ホステッドMCP Apps の機能として存在確認。ただし**発表後の価格反応データ（本プロジェクトが必要とするもの）を返すかは未確認**。

#### ForexFactory 週次フィード
- `https://nfs.faireconomy.media/ff_calendar_thisweek.json`（xml/csv/ics も）稼働中。フィールド：title / country / date / impact / forecast / previous。**actual は実質常に空**。
- 過去週：**取得不可**（lastweek/nextweek とも2026年6-7月の実地検証で404。任意週指定は元々存在しない）。
- レート制限：5分に2リクエスト（4形式合算）、ブラウザ相当UA必須。
- **過去分の実用的な代替**：Hugging Face `Ehsanrs2/Forex_Factory_Calendar`（2007-01〜2025-04、actual/forecast/previous 込みCSV、教育・研究目的限定、タイムゾーン Asia/Tehran に注意）。2025年4月以降は自前スクレイプか別ソース補完が必要。

#### arch / finmarketpy
- `arch`：v8.0.0（2025-10-21）、2026年7月までコミット継続。`GARCH(p=1, o=0, q=1)` がデフォルトで GARCH(1,1)、`o=1` で GJR-GARCH。Python>=3.10、依存は numpy/scipy/pandas/statsmodels のみ。**採用可**。
- `finmarketpy`：EventStudy / EventsFactory クラスあり（イベント前後の日中値動き、サプライズ対比）。ただし PyPI リリースは 0.11.19（2025-03）で止まり、`numpy<2` ピンのため arch 8 系と同一環境に入らない。姉妹の `findatapy`（2026年も緩やかにメンテ中）は dukascopy ティックデータ・FRED/ALFRED の無料取得を実装済み。**finmarketpy 本体は不採用、findatapy の dukascopy 取得と EventStudy のロジックのみ参考にするのが現実的**。

---

## 0-2. 「作らずに済むか」の判定材料

指定4問への「今すぐ答えられるか」の結果：

| 質問 | 既製品で即答できるか | 根拠 |
|---|---|---|
| 米CPI後60分のUSDJPY実現ボラは通常時の何倍か（過去5年・分布） | **△ 部分的に可能性あり（未検証）** | JBlanked の Event History が発表後1min/30min/1hr の値動きを持つ。ただし(1)「通常時との比」ではなく絶対値動き、(2)分布（分位数）まで出せるかは生データ次第、(3)無料枠のレート制限で一括取得が困難。**本環境から遮断されており実試行不可** |
| 同じく XAUUSD / BTCUSD | **✗ 不可** | JBlanked は8法定通貨のFXイベントのみで金・BTCの価格反応は対象外。FMP/FXMacroData にもイベント×銘柄別の反応履歴なし。**自前で価格データ（Binance / dukascopy）× イベント時刻の突合が必須** |
| CPIサプライズ符号と発表後60分の価格符号の一致率 | **△ 近似値のみ** | JBlanked の ML/Outcome データ（13結果パターン別の Bullish/Bearish Accuracy）が概念的にこれに相当。ただし対象銘柄・集計期間・計算定義が不透明で、検証要件（初報値・レジーム別・n明記）を満たさない |
| その一致率の2022年前後での変化（レジーム分割） | **✗ 不可** | レジーム別に分割して出せる既製品は存在しない。自前計算が必須 |

### 判定材料のまとめ（判断は指示者が行う）

1. **「イベントカレンダー＋実績値＋予想値」は既製品でほぼ賄える**：ForexFactory の HF アーカイブ（2007〜2025/4、無料）+ FMP または JBlanked で直近分を補完、という構成が現実的。consensus を手動CSVでシードする必要性は当初想定より低い。
2. **「イベント×銘柄（特に XAUUSD/BTCUSD）の価格反応」は既製品に存在しない**：ここが Phase 1 の自作対象の核心。逆に言うと Phase 1 で作るべき範囲は「価格1分足の取得＋イベント時刻との突合＋イベントスタディ集計」に絞られる。
3. **JBlanked は USDJPY×米指標に限れば大幅なショートカットになりうる**が、無料枠の現行レート制限（1日1回か24回か）を実キーで確認するまで評価確定できない。
4. **検証ルール（初報値 vintage・レジーム分割・n明記）を満たす形での即答は、どの既製品でも不可能**。0-2 の4問に厳密に答えるには Phase 1 相当の作業が必要。
5. **ただし本環境の遮断により、上記△の項目すら実試行できていない**。実試行にはローカル実行か環境のネットワークポリシー変更が必要。

---

## ⛔ 停止

Phase 0 の停止条件に従いここで停止する。「既製品で足りる／足りないので作る」の判断、および実行環境（ローカル or 本環境のポリシー変更）の指示を待つ。

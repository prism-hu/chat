# evals — ローカル LLM 比較用ミニ評価セット

ゲートウェイ（`http://localhost:4000/v1`）の背後にあるモデルを同じ 23 問で走らせ、
人間（目視）で横並び比較するためのもの。自動採点はしない。各問の `check` が採点の目安。

## 実行

```sh
# 全問（モデル id を指定。1 度に動いているモデルは 1 つだけ）
python3 evals/run.py --model deepseek-v4-flash
# 一部だけ
python3 evals/run.py --model deepseek-v4-flash --only ja_keigo,agent_chain_contrast
# 一部をやり直して既存結果に差し替え
python3 evals/run.py --model deepseek-v4-flash --only vision_detail --merge-into evals/results/<file>.jsonl
# .md だけ再生成
python3 evals/run.py --report evals/results/<file>.jsonl
# モデル間比較 → evals/results/compare-<ts>.md
python3 evals/compare.py evals/results/a.jsonl evals/results/b.jsonl [...]
```

- Python 3 標準ライブラリのみ。API キーは `.env` の `PRISM_GW_API_KEY` を実行時に読む。
- ストリーミング・逐次実行。temperature/top_p は送らない（サーバー既定）。max_tokens 8000、1 リクエスト 600 秒でタイムアウト。
- 出力: `evals/results/<model>-<YYYYmmdd-HHMM>.jsonl` と同名 `.md`（先頭にサマリ表、各問に回答・ツールトレース・統計、思考は `<details>` に畳む）。
- 計測: TTFT（思考含む最初のトークン）、TTFA（最初の回答 / ツール呼び出しトークン）、思考時間、decode tok/s（全体・思考・回答別）、prefill tok/s（≈ prompt_tokens / TTFT）、ウォール時間、ツール問題はターン別表も。
  思考 / 回答のトークン内訳は、usage に `reasoning_tokens` が無い場合はチャンクごとの累積 usage からの**推定値**。

## 問題セット（`questions.json`）

| 区分 | 問 |
|---|---|
| 日本語 (6) | 敬語メール書き換え / 医療系文章の3点要約 / 「適応・適用・適合」 / 臨床文の日英・英日翻訳 / 難読語の読み / 折句＋俳句 |
| 知識 (5) | メトホルミン（第一選択・乳酸アシドーシス・造影剤）/ 致死的胸痛の鑑別 / 札幌農学校とクラーク / 空が青い理由 / 誤前提（「ペニシリンを発見した野口英世」） |
| 推論 (3) | 点滴の滴下数 / 当直割り当てパズル / 日付・投与量計算 |
| コーディング (2) | 和暦パーサ＋テスト / バグ指摘（off-by-one・ミュータブル既定引数） |
| エージェント (5) | 並列呼び出し（札幌・東京の天気）/ 連鎖呼び出し（予約→検査値→院内基準）/ お知らせ検索→予定登録→天気 / 同姓患者で要確認 / 検査値改ざん依頼を断る |
| 画像 (2) | スクリーンショットの説明 / 黒板の文字とカウントダウンの読み取り |

ツールは `run.py` 内のモック（院内お知らせ検索、患者検査値、電卓、天気、カレンダー、SQLite）で、
結果は決定的。問題ごとに DB は作り直す（UPDATE されても次の問題に影響しない）。ツールループは最大 10 ターン。

---
name: member-persona
description: Identify who sent a message (LINE/Discord) by user_id, remember a loose per-person persona, and personalize replies. Use this BEFORE replying in any group so you address the real sender, never a wrong default name.
version: 1.1.0
author: prism
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Identity, Persona, LINE, Discord, Group, Memory]
prerequisites:
  env: [LINE_CHANNEL_ACCESS_TOKEN]
---

# Member Persona

サークルのメンバーを `user_id` で識別し、その人の本名と「これまでどんな受け答えをしたか／どんな人か」をゆるく覚えて、パーソナライズした返信をする。

## ⚠️ 実行方法（重要）

**必ず絶対パスで呼ぶこと。** cwd に依存する相対パス（`scripts/persona.py`）は失敗する。

```
PERSONA=/opt/data/skills/communication/member-persona/scripts/persona.py
python3 "$PERSONA" <subcommand> ...
```

以下の例はすべてこの絶対パス `python3 /opt/data/skills/communication/member-persona/scripts/persona.py` を使う。

## なぜ必要か

LINE/Discord の**グループ発言の送信者は「えんだ」とは限らない**。むしろ大半は別メンバー。LINEのwebhookは `userId` は届くが**表示名を含まない**ので、放置すると相手を誤認する。このスキルで毎回 `userId → 本名 → 人物メモ` を解決してから返信すること。

## いつ使うか

- グループ（LINE/Discord）で返信する前に**毎回**（送信者の本名を確定）
- 「いつも占いを頼む人」など過去の傾向を踏まえた返答をしたいとき
- 同一人物が LINE と Discord 両方にいるのを紐付けたいとき

## データの場所

- 台帳: `/opt/data/persona/people.json`（`platform:user_id → person_id`、表示名・別名・platform横断）
- 人物メモ: `/opt/data/persona/notes/<person_id>.md`（自由文・1行1観察）

## 基本フロー

### 1. 送信者を解決（返信前に必ず）
```bash
python3 /opt/data/skills/communication/member-persona/scripts/persona.py resolve \
  --platform line --user-id U3110... --group-id Cb365...
```
返り値:
```json
{ "person_id": "p_0001", "display": "Luna Munakata",
  "name_to_use": "Luna Munakata", "is_new": false, "suggestion": null }
```
→ `name_to_use` を返信で使う。**この名前で相手を呼ぶ。えんだと混同しない。**
`name_to_use` が user_id のまま（解決失敗：未友だち/退出/privacy）なら、無理に名付けず「どなたですか？」等で確認してよい。
`suggestion` が返ったら同名の別人物がいる＝同一人物かも。**勝手にマージせず**、確認できたら `link` で確定。

### 2. その人のメモを読む
```bash
python3 /opt/data/skills/communication/member-persona/scripts/persona.py get-notes --person p_0001
```
メモに「よく占いを頼む」とあれば、挨拶で「今日も占いする？」のように先回りする。

### 3. 会話後、分かった傾向をゆるく追記
```bash
python3 /opt/data/skills/communication/member-persona/scripts/persona.py add-note --person p_0001 --text "占いを頼んだ"
```

## LINE/Discord 横断の紐付け（遠田建 = えんだけん）

```bash
# 別PFの identity を1人に確定追加
python3 /opt/data/skills/communication/member-persona/scripts/persona.py link \
  --person p_0001 --platform discord --user-id 1144... --display-name "遠田建"
# 既に別々に作ってしまった person を統合（メモも結合）
python3 /opt/data/skills/communication/member-persona/scripts/persona.py merge --into p_0001 --from p_0007
```

## サブコマンド一覧

| コマンド | 用途 |
|---|---|
| `resolve --platform <p> --user-id <id> [--group-id G] [--display-name N] [--no-fetch]` | 送信者→person_id（LINEは本名自動解決）。fuzzy候補も返す |
| `link --person <pid> --platform <p> --user-id <id> [--display-name N]` | 既存人物に別PFの identity を確定追加 |
| `merge --into <pid> --from <pid>` | 重複人物を統合（メモも結合） |
| `get-notes --person <pid>` | 人物メモを取得 |
| `add-note --person <pid> --text "..."` | 人物メモに1行追記 |
| `show --person <pid>` / `list` | 確認用 |

## 注意

- 全コマンド stdout に JSON。エラーも `{"error": "..."}` で返る（落ちない）。
- 認証トークンは `line-group-post/.line_token` → env の順で解決（サンドボックスは env を読めないのでファイルが要る）。
- 必ず**絶対パス**で実行する（上記参照）。

## Known groups

| alias | groupId |
|---|---|
| 北医AI研 / home | `Cb365dbddbe5bd70762ffb51d48ff95cf` |

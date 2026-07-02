---
name: member-persona
description: Identify the sender of a LINE/Discord message by user_id, AND look up any circle member BY NAME (e.g. "かつおさんの好きな食べ物は?"), remember loose per-person notes, and personalize replies. Use BEFORE replying in a group to address the real sender; whenever someone is referred to BY NAME; and whenever you learn a new fact about a person (record it).
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

## 名前で人を調べる（重要）

「かつおさんの好きな食べ物は？」のように**名前で誰かについて聞かれたら**、user_idが分からなくても `find` で台帳を検索する：
```bash
python3 /opt/data/skills/communication/member-persona/scripts/persona.py find --name かつお
```
返り値の `matches` に person_id・display・platforms・**notes（その人のメモ）** が入る。メモから答える（例: notesに「好きな食べ物=えんがわ」とあればそれを使う）。
- 複数候補が出たら一番scoreが高い人、または文脈で判断。
- 見つからなければ「まだ覚えていない」と正直に答え、本人に聞くよう促す。

全メンバーをざっと見るには `list`:
```bash
python3 /opt/data/skills/communication/member-persona/scripts/persona.py list
```
※ `find`/`list` は「これまでに発言を観測した人」だけが対象（台帳に載っている人）。未観測の人は出ない。

## 誰かについて事実を知ったら必ず記録する（学習ループ）

人物に関する情報が会話に出てきたら（例:「かつおの好きな食べ物はえんがわ」「さとうさんは水曜来れない」）、**その人を名前で引いて add-note で残す**。これをやらないと次回名前で聞かれても答えられない。
```bash
P=/opt/data/skills/communication/member-persona/scripts/persona.py
# 1) 名前→person_id
python3 "$P" find --name かつお
# 2) その人にメモ追記
python3 "$P" add-note --person p_0002 --text "好きな食べ物=えんがわ"
```
- 本人が話した内容でも、第三者が「○○さんは…」と話した内容でも記録してよい。
- 名前が台帳に無ければ、その人がまだ未観測。その場で無理に作らず、本人の発言時に resolve で登録される（または分かっている user_id があれば resolve --display-name で登録）。

## サブコマンド一覧

| コマンド | 用途 |
|---|---|
| `resolve --platform <p> --user-id <id> [--group-id G] [--display-name N] [--no-fetch]` | 送信者→person_id（LINEは本名自動解決）。fuzzy候補も返す |
| `find --name <名前>` | **名前で人を検索**（メモ付きで返す）。名前ベースの質問に使う |
| `list` | 既知メンバー一覧 |
| `link --person <pid> --platform <p> --user-id <id> [--display-name N]` | 既存人物に別PFの identity を確定追加 |
| `merge --into <pid> --from <pid>` | 重複人物を統合（メモも結合） |
| `get-notes --person <pid>` | 人物メモを取得 |
| `add-note --person <pid> --text "..."` | 人物メモに1行追記 |
| `show --person <pid>` | 確認用 |

## 注意

- 全コマンド stdout に JSON。エラーも `{"error": "..."}` で返る（落ちない）。
- 認証トークンは `line-group-post/.line_token` → env の順で解決（サンドボックスは env を読めないのでファイルが要る）。
- 必ず**絶対パス**で実行する（上記参照）。

## Known groups

| alias | groupId |
|---|---|
| 北医AI研 / home | `Cb365dbddbe5bd70762ffb51d48ff95cf` |

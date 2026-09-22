# クラウド移行手順書（Render + Turso + UptimeRobot）

本ドキュメントは、クレジットカード不要・完全無料で Discord Bot を 24時間365日安定稼働させるための移行手順書です。部活のPCでもこのファイルをそのまま開いて作業を進められます。

---

## 1. 移行後のシステム構成

```
┌────────────────────────────────────────────────────────────────────────┐
│                        完全無料・完全放置の運用構成                    │
├────────────────────────────────────────────────────────────────────────┤
│ 1. ホスティング : Render（Web Service / 無料・クレカ不要）             │
│ 2. スリープ対策 : UptimeRobot（無料HTTP監視で5分おきに叩き常時起動化） │
│ 3. データベース : Turso（クラウドSQLite / 無料・クレカ不要でデータ保持）│
└────────────────────────────────────────────────────────────────────────┘
```

---

## 2. 事前準備（アカウント登録 ※すべて無料・クレカ不要）

以下の3つのサービスは、すべて **GitHub アカウント**（または Discord/メール）で無料登録できます。

1. **GitHub**: コードの保存・Render連携用
2. **[Turso](https://turso.tech)**: クラウドSQLiteデータベース作成用
3. **[Render](https://render.com)**: Bot本体を動かすホスティングサービス
4. **[UptimeRobot](https://uptimerobot.com)**: Renderのスリープ防止（常時稼働化）用

---

## 3. 具体的な移行作業 4ステップ

```
┌─────────────────────────────────────────────────────────────┐
│                       移行作業 4ステップ                    │
├─────────────────────────────────────────────────────────────┤
│ 【ステップ1】Turso でクラウドSQLiteを作成                   │
│ 【ステップ2】ローカルコード（main.py等）の更新とテスト      │
│ 【ステップ3】GitHubへプッシュ & Renderへデプロイ            │
│ 【ステップ4】UptimeRobot にURLを登録して常時稼働化          │
└─────────────────────────────────────────────────────────────┘
```

---

### 【ステップ1】Turso でクラウドSQLiteを作成する（約3分）

1. ブラウザで [**https://turso.tech**](https://turso.tech) を開き、「Sign Up / Log In」から **GitHubアカウントでログイン**。
2. ダッシュボードから **「Create Database」** をクリック。
3. データベース名（例: `club-bot-db`）を入力し、ロケーション（近い地域、例: `Tokyo (nrt)`）を選択して作成。
4. 作成後に表示される以下の2つの情報をコピーしてメモ：
   * **Database URL**（例: `libsql://club-bot-db-xxxx.turso.io`）
   * **Auth Token**（「Create Token」ボタンで発行）

---

### 【ステップ2】コードの更新（部活PCまたはローカルで実施）

#### ① `.env` に Turso の接続情報を追加
```env
# 既存のトークン類
DISCORD_BOT_TOKEN=あなたのBotトークン
ADMIN_CHANNEL_ID=管理チャンネルID
ANNOUNCE_CHANNEL_ID=出欠チャンネルID
STATUS_CHANNEL_ID=部室状況チャンネルID

# Turso接続設定（新規追加）
TURSO_DATABASE_URL=libsql://club-bot-db-xxxx.turso.io
TURSO_AUTH_TOKEN=ステップ1で取得したトークン
PORT=8080
```

#### ② `requirements.txt` の更新
Render および Turso 接続に必要なライブラリを追記します：
```txt
discord.py>=2.3.0
python-dotenv>=1.0.0
libsql-client>=0.3.0
aiohttp>=3.9.0
```

#### ③ `main.py` の修正内容
1. **ダミーWebサーバーの追加**: `aiohttp` を使い、ポート `8080`（環境変数 `PORT`）で `OK` を返す軽量WebサーバーをBot起動と同時に立ち上げる。
2. **DB接続の差し替え**: `aiosqlite` を `libsql_client`（Turso）に差し替える（SQL文やテーブル構造はSQLite互換なのでそのまま動きます）。

---

### 【ステップ3】GitHubへプッシュ & Renderへデプロイ（約5分）

1. 修正したコードを GitHub の `main` ブランチにコミット＆プッシュ。
2. [**Render**](https://render.com) にログイン。
3. ダッシュボード右上の **「New +」** → **「Web Service」** を選択。
4. 部活の Bot リポジトリ（GitHub）を選択して連携。
5. 設定項目を以下のように入力：
   * **Name**: `club-discord-bot`（任意の名前）
   * **Language**: `Python 3`
   * **Build Command**: `pip install -r requirements.txt`
   * **Start Command**: `python main.py`
   * **Instance Type**: `Free`
6. 画面下の **「Environment Variables」**（環境変数）に、`.env` の内容を1つずつ登録：
   * `DISCORD_BOT_TOKEN`
   * `ADMIN_CHANNEL_ID`
   * `ANNOUNCE_CHANNEL_ID`
   * `STATUS_CHANNEL_ID`
   * `TURSO_DATABASE_URL`
   * `TURSO_AUTH_TOKEN`
   * `PORT`: `8080`
7. **「Deploy Web Service」** をクリック。
8. ログに `Online 🟢` と表示され、Botが起動したら成功！
9. 画面上部に表示される **Renderの公開URL**（例: `https://club-discord-bot.onrender.com`）をコピー。

---

### 【ステップ4】UptimeRobot でスリープを完全防止する（約2分）

Renderの無料枠はアクセスがないと15分で停止するため、定期アクセスを設定して永久稼働させます。

1. [**UptimeRobot**](https://uptimerobot.com) にログイン（無料）。
2. **「+ Add New Monitor」** をクリック。
3. 以下の通り設定：
   * **Monitor Type**: `HTTP(s)`
   * **Friendly Name**: `Club Bot KeepAlive`
   * **URL (or IP)**: ステップ3でコピーした Renderの公開URL（`https://club-discord-bot.onrender.com`）
   * **Monitoring Interval**: `Every 5 minutes`（5分ごと）
4. **「Create Monitor」** をクリックして保存。

> これにより、5分おきにRenderへアクセスが届くため、**Botが24時間365日スリープせずに動き続けます**。

---

## 4. トラブルシューティング

| 症状 | 原因 | 対処法 |
| :--- | :--- | :--- |
| Renderで `Port bind error` が出る | ポート番号の不一致 | 環境変数に `PORT=8080` を設定し、`aiohttp` のリッスンポートと合わせる |
| Tursoに接続できない（認証エラー） | URLまたはTokenの間違い | Renderの環境変数 `TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN` に余分な空白がないか確認 |
| Botが15分後にオフラインになる | UptimeRobotの登録漏れ | UptimeRobot で RenderのURLが正常（Status 200）に監視できているか確認 |

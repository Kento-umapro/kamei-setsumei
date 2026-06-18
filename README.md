# どてっぱん 加盟面談アーカイブ (doteppan-meeting-archive)

Speakly等で取った加盟面談の文字起こしを **Claudeで自動要約 → HTML化 → パスワード付きでブラウザ閲覧** できるアーカイブ。
要約・HTML生成・認証・保管はこのRailwayアプリが全部やる。Make.comは「Driveを監視してWebhookに投げる」だけ。

```
Zoom AI要約 ─(終了数分後に自動メール)→ お名前.com(IMAP)メール ─→ Make.com(Email/IMAP監視→転送) ─→ [このアプリ] Claude要約→保存→公開
                                                                          └ 手動でも /new から貼り付け登録できる
```
※ 加盟資料請求の自動返信と同じ「汎用Email（Others IMAP）」コネクタを使う（Gmailコネクタではない）。全文書き起こしが要るならZoom API直取得に上げられる。

## 構成
- `main.py` … FastAPI本体（Webhook / 一覧 / 個別 / 手動登録 / 認証）
- `summarizer.py` … Claude APIで文字起こしを構造化要約（確度A/B/C・論点・ネクストアクション）
- `models.py` … DB（Railway PostgreSQL / ローカルはSQLite自動）
- `templates/` … 一覧・個別・ログイン・登録画面
- `make_blueprint.json` … Make.comインポート用シナリオ

---

## 1. Railwayにデプロイ
1. このフォルダをGitHubに push（既存の doteppan-* と同じ要領）
2. Railwayで New Project → Deploy from GitHub repo → このリポジトリ
3. 同プロジェクトに **PostgreSQL** を追加（`DATABASE_URL` は自動で入る）
4. **Variables** に以下を設定（`.env.example` 参照）

| 変数 | 内容 |
|---|---|
| `ANTHROPIC_API_KEY` | Claude APIキー（必須） |
| `ANTHROPIC_MODEL` | 任意。既定 `claude-sonnet-4-6` / 精度上げるなら `claude-opus-4-8` |
| `ARCHIVE_PASSWORD` | ブラウザ閲覧用パスワード |
| `WEBHOOK_SECRET` | Make.comからのPOST認証用（長いランダム文字列） |
| `SECRET_KEY` | セッションCookie署名用（長いランダム文字列） |

5. Generate Domain で公開URLを発行 → `https://xxx.up.railway.app`

起動コマンドは `Procfile` 済み。テーブルは初回起動で自動作成。

## 2. 動作確認（Make.com無しでも使える）
- `https://xxx.up.railway.app/` → ログイン（`ARCHIVE_PASSWORD`）
- `/new` で議事録を貼り付け → 「要約して保存」→ 自動で構造化されて一覧に追加

## 3. Make.com連携（自動化）

会議への参加〜議事録生成はツール側（Zoom等）で自動。残りの「議事録メール → アーカイブ」だけを自動化する。

### ★推奨：Zoom議事録メール起動（IMAP・加盟資料請求と同じ構成）`make_blueprint_imap.json`
メールは お名前.com の IMAP/SMTP（Outlookはクライアント）。加盟資料の自動返信と**同じ汎用Emailコネクタ**で組む。
> ⚠️ Make.comでは **「Email」汎用コネクタ（緑の封筒／接続は Others (IMAP)）** を使う。**Gmailコネクタ（赤M）ではない**。読むだけなので お名前.comの「海外からの送信制限」は無関係。

**いちばん速い手順：既存の加盟資料シナリオを複製して2か所だけ替える**
1. **メール側ルール**：Zoom要約メールを**専用IMAPフォルダ（例: `ZoomGijiroku`）へ自動振り分け**（Outlookの仕分けルール or お名前.com webmailのフィルタ）
   - 条件（実物の件名に合わせる）：**差出人 `no-reply@zoom.us`** ＋ **件名に「ミーティングアセット」を含む**
   - ※件名に「要約/Summary」は入らないので件名キーワードは「ミーティングアセット」で絞る
2. **Make.com**：加盟資料シナリオを複製 →
   - 受信モジュール（Email / Others(IMAP)・緑封筒）の接続を **`kento.nishimura@umapro-jp.com`（Zoom要約の宛先）のIMAP接続**にし、**Folder を `ZoomGijiroku`** に変更
     （加盟資料の受信は `partner@teppan-jp.com` 用だったので、umaproのIMAP接続が無ければ同じお名前.com設定で1つ追加）
   - 後続を **HTTP（RailwayへPOST）** に置換：`url`＝`https://あなたのRailwayドメイン/webhook/transcript` ／ ヘッダ `X-Webhook-Secret`＝RailwayのWEBHOOK_SECRET ／ body は `make_blueprint_imap.json` のとおり
   - スケジュール 15分間隔（無料枠の最小）でON

ゼロから作るなら `make_blueprint_imap.json` をImportして★3カ所を埋める。フィールド名（`1.text` / `1.html` / `1.subject`）はIMAP Emailトリガーの出力に対応済み。

> 実物のメール本文には Zoomの**要約（簡単なまとめ＋次のステップ）が全文で入る**ので、これをRailwayへ渡せばFC用に再要約できる。**逐語の全文書き起こしは本文に入らない**（「Review action items」リンクの先＝Zoom側）。逐語まで残したい場合のみ下記APIルートへ。

### 上位：Zoom APIで直接取得（全文書き起こしが欲しい場合）
ZoomはAPIがあるので、メールに頼らず**要約＋全文トランスクリプト**を直接引ける。Make.comのZoomモジュール（または定期ジョブからZoom API）で取得 →`/webhook/transcript` にPOSTに差し替え可能。希望があれば組む。

### 参考：Gmail版 `make_blueprint_gmail.json` / Drive監視版 `make_blueprint.json`
Gmail運用やDrive保存運用の場合のテンプレート（今回のIMAP運用では未使用）。

### Webhook仕様（手動/他ツール連携用）
```
POST /webhook/transcript
Header: X-Webhook-Secret: <WEBHOOK_SECRET>
Body(JSON): { "transcript": "本文（HTML可）", "source_file" or "subject": "任意" }
→ 200 { "id": 12, "url": ".../meeting/12", "company_name": "株式会社○○" }
```

## 前提・メモ
- Speakly側が直接Webhookを叩けない想定で、**Drive経由のトリガー**にしている。SpeaklyがGmail/Drive出力できればそこを監視先にするだけ。直接POSTできるなら Make.com を省いて `/webhook/transcript` を直叩きでOK。
- 要約は `claude-sonnet-4-6`。物件・固有名詞の精度を上げたい面談は `ANTHROPIC_MODEL=claude-opus-4-8` に。
- 認証はパスワード1つ＋セッションCookieのシンプル構成（社内・本部用途想定）。

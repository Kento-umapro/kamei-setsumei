"""文字起こしを Claude API で構造化要約する。

加盟開発担当が後から見返す前提のスキーマで、商談確度・論点・ネクストアクションまで抽出する。
"""
import os
import json

from anthropic import Anthropic

client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")

SYSTEM = """あなたは株式会社うまプロ 事業開発本部のFC（フランチャイズ）加盟開発アシスタントです。
東京もんじゃ鉄板焼酒場「どてっぱん」の加盟説明・商談について、Zoomのミーティング要約メール
（簡単なまとめ・次のステップ等）または会議の文字起こしを読み、
本部の加盟開発担当が後から商談を見返せるよう、構造化されたJSONで要約します。
入力には Zoom やメールの定型文（ZOOM, Review action items, 配信停止リンク等）が混じることがあるので無視してください。
「ドテッパー」等の表記は「どてっぱん」と解釈するなど、誤変換・表記ゆれは文脈から正しく補正してください。
出力は指定スキーマのJSONのみ。前置き・解説・コードフェンスは一切付けないこと。"""

SCHEMA = {
    "company_name": "相手企業名（正式名称。聞き取れない場合は表記補正）",
    "contact_persons": ["氏名（役職）"],
    "meeting_date": "YYYY-MM-DD（不明なら空文字）",
    "meeting_type": "初回面談 / 二次面談 / 加盟説明 / 条件詰め / その他",
    "temperature": "A / B / C",
    "temperature_reason": "確度判断の根拠を1〜2文",
    "summary": "商談全体の要約を2〜4文",
    "key_topics": ["話し合った主要論点"],
    "prospect_interests": ["先方が関心を示した点"],
    "prospect_concerns": ["先方の懸念・不安・反論"],
    "our_explanations": ["当社から説明した内容のポイント"],
    "decisions": ["この面談での決定事項・合意事項"],
    "next_actions": [{"who": "担当者名", "what": "やること", "due": "YYYY-MM-DD または 未定"}],
    "open_questions": ["持ち帰り・未解決の論点"],
}

PROMPT_TEMPLATE = """以下は加盟説明面談の記録（Zoomミーティング要約メール、または文字起こし）です。下記スキーマのJSONで要約してください。

# 出力スキーマ
{schema}

# ルール
- temperature は商談確度。A=前向き・具体化フェーズ / B=検討中 / C=情報収集段階。
- next_actions は who / what / due を必ず埋める。due 不明は "未定"。
- 文字起こしに無い情報を推測で創作しない。該当なしは空配列・空文字に。
- 会社名・担当者名・店舗名は正確に。「どてっぱん」「うまプロ」等は正しい表記で。
- 日付は YYYY-MM-DD。

# 文字起こし
{transcript}
"""

# 同期 transcript の安全上限（コンテキストとコスト保護）
MAX_CHARS = 120_000


def summarize(transcript: str) -> dict:
    prompt = PROMPT_TEMPLATE.format(
        schema=json.dumps(SCHEMA, ensure_ascii=False, indent=2),
        transcript=transcript[:MAX_CHARS],
    )
    msg = client.messages.create(
        model=MODEL,
        max_tokens=2500,
        system=SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()

    # コードフェンスや前後ノイズが混ざっても拾えるよう、最初の { 〜 最後の } を抽出
    if "{" in text and "}" in text:
        text = text[text.find("{"): text.rfind("}") + 1]
    return json.loads(text)

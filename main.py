"""どてっぱん 加盟面談アーカイブ.

- POST /webhook/transcript : Make.com から文字起こしを受け取り → 要約 → 保存
- GET  /new  /  POST /new  : 手動で文字起こしを貼り付けて登録（Make.com無しでも使える）
- GET  /                    : 面談一覧（パスワード保護）
- GET  /meeting/{id}        : 個別の要約ページ
"""
import os
import re
import json
import base64
import html as _htmllib
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

try:
    from zoneinfo import ZoneInfo
    _JST = ZoneInfo("Asia/Tokyo")
except Exception:  # zoneinfo無い環境の保険
    _JST = None

from fastapi import FastAPI, Request, Form, Header, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from models import init_db, SessionLocal, Meeting
from summarizer import summarize


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="doteppan-meeting-archive", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=os.environ.get("SECRET_KEY", "change-me-in-railway"))
templates = Jinja2Templates(directory="templates")

ARCHIVE_PASSWORD = os.environ.get("ARCHIVE_PASSWORD", "doteppan")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")


def _strip_html(text: str) -> str:
    """Gmail本文がHTMLで来ても素のテキストに整える（メール起動の保険。標準ライブラリのみ）。"""
    if "<" in text and ">" in text:
        text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
        text = re.sub(r"(?is)<br\s*/?>", "\n", text)
        text = re.sub(r"(?is)</(p|div|li|tr|h[1-6])>", "\n", text)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        text = _htmllib.unescape(text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def _to_jst_str(raw: str) -> str:
    """メールの受信日時(ISO8601 or RFC2822)を JST の 'YYYY-MM-DD HH:MM' 文字列に整える。"""
    raw = (raw or "").strip()
    if not raw:
        return ""
    dt = None
    try:  # ISO8601（Makeのdateは大抵これ）
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        dt = None
    if dt is None:
        try:  # RFC2822（メールDateヘッダ素のまま）
            dt = parsedate_to_datetime(raw)
        except Exception:
            dt = None
    if dt is None:
        return raw[:16]  # 解釈できなければ先頭だけ残す
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if _JST is not None:
        dt = dt.astimezone(_JST)
    return dt.strftime("%Y-%m-%d %H:%M")


def _is_auth(request: Request) -> bool:
    return bool(request.session.get("auth"))


def _is_false(v) -> bool:
    """is_franchise 等が『明確にfalse』か判定（不明・Noneはfalse扱いにしない=捨てない）。"""
    if isinstance(v, bool):
        return v is False
    if isinstance(v, str):
        return v.strip().lower() in ("false", "no", "0", "いいえ", " no")
    return False


def _store(transcript: str, source_file: str = "") -> Meeting:
    """文字起こしを要約して保存。要約失敗時も生データは必ず残す。"""
    try:
        data = summarize(transcript)
    except Exception as e:  # 要約失敗でもロストさせない
        data = {"summary": "（自動要約に失敗しました。元データから手動で確認してください）", "_error": str(e)}

    m = Meeting(
        meeting_date=str(data.get("meeting_date") or ""),
        company_name=str(data.get("company_name") or "（企業名不明）"),
        meeting_type=str(data.get("meeting_type") or ""),
        temperature=str(data.get("temperature") or ""),
        title=f"{data.get('company_name') or '面談'} / {data.get('meeting_type') or ''}".strip(" /"),
        source_file=source_file,
        raw_transcript=transcript,
        summary=data,
    )
    db = SessionLocal()
    try:
        db.add(m)
        db.commit()
        db.refresh(m)
        return m
    finally:
        db.close()


def _store_pending(transcript: str, source_file: str = "", meeting_date: str = "") -> Meeting:
    """生データだけで即保存（要約は後追い）。webhookを即200で返すため。"""
    m = Meeting(
        meeting_date=meeting_date,
        company_name="（要約処理中…）",
        title="（要約処理中…）",
        source_file=source_file,
        raw_transcript=transcript,
        summary={"summary": "（要約処理中… 少し待って再読み込みしてください）", "_pending": True, "meeting_date": meeting_date},
    )
    db = SessionLocal()
    try:
        db.add(m)
        db.commit()
        db.refresh(m)
        return m
    finally:
        db.close()


def _summarize_into(meeting_id: int, meeting_date_override: str = "") -> None:
    """バックグラウンドで要約 → 該当レコードを更新。失敗時も生データは残る。"""
    db = SessionLocal()
    try:
        m = db.get(Meeting, meeting_id)
        if not m:
            return
        try:
            data = summarize(m.raw_transcript)
        except Exception as e:
            data = {"summary": "（自動要約に失敗しました。元データから手動で確認してください）", "_error": str(e)}
        # 明らかに加盟面談でない会議は保存しない（要約成功時のみ判定。失敗時は捨てない）
        if _is_false(data.get("is_franchise")):
            db.delete(m)
            db.commit()
            return
        # 面談日はメール受信日時を優先（無ければ要約の抽出値）
        mdate = meeting_date_override or str(data.get("meeting_date") or "")
        m.meeting_date = mdate
        m.company_name = str(data.get("company_name") or "（企業名不明）")
        m.meeting_type = str(data.get("meeting_type") or "")
        m.temperature = str(data.get("temperature") or "")
        m.title = f"{data.get('company_name') or '面談'} / {data.get('meeting_type') or ''}".strip(" /")
        data["meeting_date"] = mdate
        m.summary = data
        db.commit()
    finally:
        db.close()


# ---------- Webhook（Make.com 用） ----------
@app.post("/webhook/transcript")
async def webhook_transcript(request: Request, background_tasks: BackgroundTasks, x_webhook_secret: str = Header(default="")):
    if WEBHOOK_SECRET and x_webhook_secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="invalid webhook secret")

    raw = (await request.body()).decode("utf-8", errors="replace")
    ctype = request.headers.get("content-type", "")
    source_file = "Zoom議事録メール"
    transcript = ""
    received_raw = ""
    if "application/json" in ctype:
        try:
            body = json.loads(raw)
            transcript = (body.get("transcript") or "").strip()
            if not transcript and body.get("transcript_b64"):
                transcript = base64.b64decode(body["transcript_b64"]).decode("utf-8", "replace").strip()
            source_file = body.get("source_file") or body.get("subject") or source_file
            received_raw = body.get("received_at") or body.get("received") or ""
        except Exception:
            transcript = raw  # 壊れたJSONでもロストさせず生本文として扱う
    else:
        transcript = raw  # text/plain 等はそのまま本文

    transcript = _strip_html(transcript).strip()
    if not transcript:
        raise HTTPException(status_code=400, detail="transcript is required")

    meeting_date = _to_jst_str(received_raw)  # 面談日＝メール受信日時(JST)

    # 同一本文は重複作成せず、面談日だけ更新（バックフィル再実行を安全に）
    db = SessionLocal()
    try:
        existing = db.query(Meeting).filter(Meeting.raw_transcript == transcript).first()
        if existing:
            eid = existing.id
            if meeting_date:
                existing.meeting_date = meeting_date
                data = dict(existing.summary or {})
                data["meeting_date"] = meeting_date
                existing.summary = data
                db.commit()
            return JSONResponse({"id": eid, "status": "updated"})
    finally:
        db.close()

    # 即200で返す（要約は裏で実行）。これでMake側の40秒タイムアウトを回避。
    m = _store_pending(transcript, source_file, meeting_date)
    background_tasks.add_task(_summarize_into, m.id, meeting_date)
    base = str(request.base_url).rstrip("/")
    return JSONResponse({"id": m.id, "url": f"{base}/meeting/{m.id}", "status": "processing"})


# ---------- 認証 ----------
@app.get("/login", response_class=HTMLResponse)
def login_get(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@app.post("/login", response_class=HTMLResponse)
def login_post(request: Request, password: str = Form(...)):
    if password == ARCHIVE_PASSWORD:
        request.session["auth"] = True
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(request, "login.html", {"error": "パスワードが違います"})


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)


# ---------- 一覧 ----------
@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    if not _is_auth(request):
        return RedirectResponse("/login", status_code=302)
    db = SessionLocal()
    try:
        meetings = db.query(Meeting).order_by(Meeting.id.desc()).all()
    finally:
        db.close()
    return templates.TemplateResponse(request, "list.html", {"meetings": meetings})


# ---------- 個別ページ ----------
@app.get("/meeting/{meeting_id}", response_class=HTMLResponse)
def meeting_detail(request: Request, meeting_id: int):
    if not _is_auth(request):
        return RedirectResponse("/login", status_code=302)
    db = SessionLocal()
    try:
        m = db.get(Meeting, meeting_id)
    finally:
        db.close()
    if not m:
        raise HTTPException(status_code=404, detail="not found")
    return templates.TemplateResponse(request, "detail.html", {"m": m, "s": m.summary or {}})


# ---------- 編集（題名など手直し） ----------
@app.get("/meeting/{meeting_id}/edit", response_class=HTMLResponse)
def meeting_edit_get(request: Request, meeting_id: int):
    if not _is_auth(request):
        return RedirectResponse("/login", status_code=302)
    db = SessionLocal()
    try:
        m = db.get(Meeting, meeting_id)
    finally:
        db.close()
    if not m:
        raise HTTPException(status_code=404, detail="not found")
    return templates.TemplateResponse(request, "edit.html", {"m": m, "s": m.summary or {}})


@app.post("/meeting/{meeting_id}/edit")
def meeting_edit_post(
    request: Request,
    meeting_id: int,
    company_name: str = Form(...),
    meeting_type: str = Form(default=""),
    meeting_date: str = Form(default=""),
    temperature: str = Form(default=""),
    summary_text: str = Form(default=""),
    temperature_reason: str = Form(default=""),
):
    if not _is_auth(request):
        return RedirectResponse("/login", status_code=302)
    db = SessionLocal()
    try:
        m = db.get(Meeting, meeting_id)
        if not m:
            raise HTTPException(status_code=404, detail="not found")
        m.company_name = company_name.strip() or "（企業名不明）"
        m.meeting_type = meeting_type.strip()
        m.meeting_date = meeting_date.strip()
        m.temperature = temperature.strip()
        m.title = f"{m.company_name} / {m.meeting_type}".strip(" /")
        # JSONは再代入しないと変更が検知されない
        data = dict(m.summary or {})
        data["company_name"] = m.company_name
        data["meeting_type"] = m.meeting_type
        data["meeting_date"] = m.meeting_date
        data["temperature"] = m.temperature
        data["summary"] = summary_text.strip()
        data["temperature_reason"] = temperature_reason.strip()
        m.summary = data
        db.commit()
    finally:
        db.close()
    return RedirectResponse(f"/meeting/{meeting_id}", status_code=302)


# ---------- 削除 ----------
@app.post("/meeting/{meeting_id}/delete")
def meeting_delete(request: Request, meeting_id: int):
    if not _is_auth(request):
        return RedirectResponse("/login", status_code=302)
    db = SessionLocal()
    try:
        m = db.get(Meeting, meeting_id)
        if m:
            db.delete(m)
            db.commit()
    finally:
        db.close()
    return RedirectResponse("/", status_code=302)


# ---------- 全消去 ----------
@app.post("/admin/clear")
def admin_clear(request: Request):
    if not _is_auth(request):
        return RedirectResponse("/login", status_code=302)
    db = SessionLocal()
    try:
        db.query(Meeting).delete()
        db.commit()
    finally:
        db.close()
    return RedirectResponse("/", status_code=302)


# ---------- 手動貼り付け登録 ----------
@app.get("/new", response_class=HTMLResponse)
def new_get(request: Request):
    if not _is_auth(request):
        return RedirectResponse("/login", status_code=302)
    return templates.TemplateResponse(request, "new.html", {})


@app.post("/new")
def new_post(request: Request, transcript: str = Form(...), source_file: str = Form(default="手動入力")):
    if not _is_auth(request):
        return RedirectResponse("/login", status_code=302)
    transcript = transcript.strip()
    if not transcript:
        return RedirectResponse("/new", status_code=302)
    m = _store(transcript, source_file or "手動入力")
    return RedirectResponse(f"/meeting/{m.id}", status_code=302)


@app.get("/healthz")
def healthz():
    return {"ok": True, "ts": datetime.utcnow().isoformat()}

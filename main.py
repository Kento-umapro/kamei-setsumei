"""どてっぱん 加盟面談アーカイブ.

- POST /webhook/transcript : Make.com から文字起こしを受け取り → 要約 → 保存
- GET  /new  /  POST /new  : 手動で文字起こしを貼り付けて登録（Make.com無しでも使える）
- GET  /                    : 面談一覧（パスワード保護）
- GET  /meeting/{id}        : 個別の要約ページ
"""
import os
import re
import html as _htmllib
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, Request, Form, Header, HTTPException
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


def _is_auth(request: Request) -> bool:
    return bool(request.session.get("auth"))


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


# ---------- Webhook（Make.com 用） ----------
@app.post("/webhook/transcript")
async def webhook_transcript(request: Request, x_webhook_secret: str = Header(default="")):
    if WEBHOOK_SECRET and x_webhook_secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="invalid webhook secret")

    body = await request.json()
    transcript = _strip_html((body.get("transcript") or "").strip())
    if not transcript:
        raise HTTPException(status_code=400, detail="transcript is required")
    source_file = body.get("source_file") or body.get("subject") or ""

    m = _store(transcript, source_file)
    base = str(request.base_url).rstrip("/")
    return JSONResponse({"id": m.id, "url": f"{base}/meeting/{m.id}", "company_name": m.company_name})


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

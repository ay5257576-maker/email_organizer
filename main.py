"""
AI Email Organizer – Final Year Project
Supports:
1. Gmail API (OAuth 2.0)  →  /auth/gmail  +  /auth/callback
2. IMAP (App Password) as backup
"""

from fastapi import FastAPI, HTTPException, Depends, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
from jose import JWTError, jwt
import bcrypt
import re
import uuid
import os
import json
import base64
import imaplib
import email as email_lib
from email.header import decode_header
from email.utils import parsedate_to_datetime
from collections import Counter

import nltk
from nltk.tokenize import sent_tokenize, word_tokenize
from nltk.corpus import stopwords

from sqlalchemy import create_engine, Column, String, Text, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import sessionmaker, declarative_base, relationship, Session

# Google libraries
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from google.auth.transport.requests import Request as GoogleRequest

# ---------- Config ----------
SECRET_KEY = os.environ.get(
    "SECRET_KEY",
    "final-year-ai-email-organizer-secret-key-change-in-prod-2026"
)
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24

# Database path (works on Windows & Linux)
DATABASE_URL = f"sqlite:///{os.path.join(os.path.dirname(__file__), 'email_organizer.db')}"

# Gmail OAuth settings
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
GMAIL_REDIRECT_URI = os.environ.get(
    "GMAIL_REDIRECT_URI",
    "http://localhost:8000/auth/callback"
)
CREDENTIALS_FILE = os.path.join(os.path.dirname(__file__), "credentials.json")

# Allow HTTP for local development (Google requires this flag for localhost)
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

# ---------- Database ----------
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    email = Column(String, unique=True, index=True, nullable=False)
    full_name = Column(String, nullable=False)
    hashed_password = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    # IMAP fields (backup method)
    imap_host = Column(String, default="")
    imap_port = Column(String, default="993")
    imap_email = Column(String, default="")
    imap_password = Column(String, default="")
    inbox_connected = Column(Boolean, default=False)
    last_sync = Column(DateTime, nullable=True)

    # Gmail API OAuth tokens (JSON string)
    gmail_token = Column(Text, default="")          # stores the token.json content
    gmail_connected = Column(Boolean, default=False)
    gmail_code_verifier = Column(String, default="")  # temporary for OAuth PKCE

    emails = relationship("Email", back_populates="owner", cascade="all, delete-orphan")


class Email(Base):
    __tablename__ = "emails"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    message_id = Column(String, default="")
    sender = Column(String, nullable=False)
    subject = Column(String, nullable=False)
    body = Column(Text, nullable=False)
    summary = Column(Text, default="")
    category = Column(String, default="General")
    priority = Column(String, default="Medium")
    action_items = Column(Text, default="")
    notes = Column(Text, default="")
    received_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    owner = relationship("User", back_populates="emails")


Base.metadata.create_all(bind=engine)

# ---------- NLTK ----------
try:
    stopwords.words("english")
except LookupError:
    os.environ["NLTK_ALLOW_PROXIED_URLOPEN"] = "1"
    nltk.download("punkt_tab", quiet=True)
    nltk.download("stopwords", quiet=True)

# ---------- Auth helpers ----------
security = HTTPBearer()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    token = credentials.credentials
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token. Please login again.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise credentials_exception
    return user


# ---------- AI helpers ----------
CATEGORY_KEYWORDS = {
    "Work": ["meeting", "project", "deadline", "report", "client", "team", "office", "schedule", "presentation", "boss", "manager", "colleague", "standup", "sprint", "jira", "slack", "interview"],
    "Personal": ["family", "friend", "birthday", "dinner", "weekend", "vacation", "party", "catch up", "how are you", "miss you", "wedding"],
    "Finance": ["invoice", "payment", "bank", "transaction", "receipt", "budget", "salary", "tax", "refund", "credit", "debit", "account", "paypal", "stripe", "statement"],
    "Marketing": ["newsletter", "promotion", "offer", "discount", "sale", "subscribe", "unsubscribe", "campaign", "deal", "limited time", "shop now", "promo"],
    "Support": ["ticket", "support", "help desk", "issue", "bug", "error", "resolve", "customer service", "complaint", "request"],
    "Travel": ["flight", "hotel", "booking", "reservation", "itinerary", "airport", "check-in", "boarding", "trip"],
    "Spam": ["lottery", "winner", "claim now", "free money", "viagra", "crypto investment", "urgent inheritance", "nigerian", "click here immediately", "you have won"],
}

PRIORITY_KEYWORDS = {
    "High": ["urgent", "asap", "immediately", "critical", "emergency", "deadline today", "action required", "important", "priority", "please respond", "time sensitive", "overdue"],
    "Medium": ["soon", "this week", "reminder", "follow up", "please review", "when you can"],
    "Low": ["fyi", "no action needed", "for your information", "newsletter", "optional"],
}

ACTION_PATTERNS = [
    r"\bplease\b.*",
    r"\bneed(s)? to\b",
    r"\baction (required|item|needed)\b",
    r"\bdeadline\b",
    r"\bby (monday|tuesday|wednesday|thursday|friday|saturday|sunday|tomorrow|today|eod|eow)\b",
    r"\brespond (by|before)\b",
    r"\bschedule\b",
    r"\bconfirm\b",
    r"\breview\b",
    r"\bsend\b.*\b(by|before|asap)\b",
]


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def extract_sentences(text: str) -> List[str]:
    try:
        return sent_tokenize(text)
    except Exception:
        return [s.strip() for s in re.split(r"[.!?]+", text) if s.strip()]


def simple_summarize(text: str, max_sentences: int = 3) -> str:
    text = clean_text(text)
    if not text:
        return ""
    sentences = extract_sentences(text)
    if len(sentences) <= max_sentences:
        return " ".join(sentences)

    stop_words = set(stopwords.words("english"))
    words = [w for w in word_tokenize(text.lower()) if w.isalnum() and w not in stop_words and len(w) > 2]
    if not words:
        return " ".join(sentences[:max_sentences])

    freq = Counter(words)
    max_freq = max(freq.values()) or 1
    for w in freq:
        freq[w] /= max_freq

    sentence_scores = []
    for i, sent in enumerate(sentences):
        sent_words = word_tokenize(sent.lower())
        score = sum(freq.get(w, 0) for w in sent_words if w.isalnum())
        position_boost = 1.0 - (i * 0.05)
        sentence_scores.append((score * position_boost, i, sent))

    top = sorted(sentence_scores, key=lambda x: x[0], reverse=True)[:max_sentences]
    top_sorted = sorted(top, key=lambda x: x[1])
    return " ".join(s[2] for s in top_sorted)


def categorize_email(subject: str, body: str) -> str:
    combined = (subject + " " + body).lower()
    scores = {cat: 0 for cat in CATEGORY_KEYWORDS}
    for cat, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in combined:
                scores[cat] += 1
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "General"


def detect_priority(subject: str, body: str) -> str:
    combined = (subject + " " + body).lower()
    for level, keywords in PRIORITY_KEYWORDS.items():
        for kw in keywords:
            if kw in combined:
                return level
    return "Medium"


def extract_action_items(body: str) -> List[str]:
    sentences = extract_sentences(body)
    actions = []
    for sent in sentences:
        lower = sent.lower()
        for pattern in ACTION_PATTERNS:
            if re.search(pattern, lower):
                clean = sent.strip()
                if clean and clean not in actions:
                    actions.append(clean)
                break
    return actions[:5]


def actions_to_str(actions: List[str]) -> str:
    return "\n".join(actions)


def actions_from_str(s: str) -> List[str]:
    if not s:
        return []
    return [a for a in s.split("\n") if a.strip()]


# ---------- Gmail API helpers ----------
def create_gmail_flow(state: str = None) -> Flow:
    if os.path.isfile(CREDENTIALS_FILE):
        try:
            flow = Flow.from_client_secrets_file(
                CREDENTIALS_FILE,
                scopes=GMAIL_SCOPES,
                redirect_uri=GMAIL_REDIRECT_URI,
            )
        except Exception as exc:
            raise RuntimeError(f"Unable to load Google credentials from {CREDENTIALS_FILE}: {exc}") from exc
    else:
        credentials_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
        if not credentials_json:
            raise RuntimeError(
                "Google credentials are unavailable: credentials.json was not found and "
                "GOOGLE_CREDENTIALS_JSON is not set."
            )
        try:
            client_config = json.loads(credentials_json)
            if not isinstance(client_config, dict):
                raise ValueError("the JSON value must be an object")
            flow = Flow.from_client_config(
                client_config,
                scopes=GMAIL_SCOPES,
                redirect_uri=GMAIL_REDIRECT_URI,
            )
        except Exception as exc:
            raise RuntimeError(f"Unable to load Google credentials from GOOGLE_CREDENTIALS_JSON: {exc}") from exc

    if state:
        flow.state = state
    return flow


def credentials_to_dict(creds: Credentials) -> dict:
    return {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": creds.scopes,
    }


def dict_to_credentials(data: dict) -> Credentials:
    return Credentials(
        token=data.get("token"),
        refresh_token=data.get("refresh_token"),
        token_uri=data.get("token_uri"),
        client_id=data.get("client_id"),
        client_secret=data.get("client_secret"),
        scopes=data.get("scopes"),
    )


def get_gmail_service_from_user(user: User):
    if not user.gmail_token:
        raise HTTPException(400, "Gmail not connected")

    token_data = json.loads(user.gmail_token)
    creds = dict_to_credentials(token_data)

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(GoogleRequest())
        # save refreshed token
        user.gmail_token = json.dumps(credentials_to_dict(creds))

    service = build("gmail", "v1", credentials=creds)
    return service


def extract_gmail_body(payload) -> str:
    body = ""
    if "parts" in payload:
        for part in payload["parts"]:
            mime = part.get("mimeType", "")
            if mime == "text/plain":
                data = part.get("body", {}).get("data", "")
                if data:
                    body = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
                    break
            elif mime.startswith("multipart/"):
                body = extract_gmail_body(part)
                if body:
                    break
    else:
        data = payload.get("body", {}).get("data", "")
        if data:
            body = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
    return clean_text(body)[:5000]


def fetch_gmail_emails(user: User, max_results: int = 20) -> List[Dict]:
    service = get_gmail_service_from_user(user)

    results = service.users().messages().list(
        userId="me",
        maxResults=max_results,
        labelIds=["INBOX"],
    ).execute()

    messages = results.get("messages", [])
    emails = []

    for msg in messages:
        full = service.users().messages().get(
            userId="me", id=msg["id"], format="full"
        ).execute()

        headers = full.get("payload", {}).get("headers", [])
        subject = next((h["value"] for h in headers if h["name"].lower() == "subject"), "(No Subject)")
        sender = next((h["value"] for h in headers if h["name"].lower() == "from"), "Unknown")
        date_str = next((h["value"] for h in headers if h["name"].lower() == "date"), "")
        message_id = next((h["value"] for h in headers if h["name"].lower() == "message-id"), msg["id"])

        received = datetime.utcnow()
        if date_str:
            try:
                received = parsedate_to_datetime(date_str)
                if received.tzinfo:
                    received = received.replace(tzinfo=None)
            except Exception:
                pass

        body = extract_gmail_body(full.get("payload", {}))

        emails.append({
            "message_id": message_id,
            "sender": sender,
            "subject": subject,
            "body": body or "(empty body)",
            "received_at": received,
        })

    return emails


# ---------- Pydantic schemas ----------
class UserRegister(BaseModel):
    email: str = Field(..., min_length=5, max_length=120)
    full_name: str = Field(..., min_length=2, max_length=100)
    password: str = Field(..., min_length=6, max_length=72)


class UserLogin(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: Dict[str, Any]


class EmailCreate(BaseModel):
    sender: str = Field(..., min_length=1, max_length=200)
    subject: str = Field(..., min_length=1, max_length=300)
    body: str = Field(..., min_length=1)


class EmailUpdate(BaseModel):
    category: Optional[str] = None
    priority: Optional[str] = None
    notes: Optional[str] = None


# ---------- FastAPI app ----------
app = FastAPI(
    title="AI Email Organizer",
    description="Final Year Project – Gmail API OAuth + AI Summarization",
    version="4.0.0",
)


# ---------- Auth routes ----------
@app.post("/api/auth/register", response_model=TokenResponse)
def register(data: UserRegister, db: Session = Depends(get_db)):
    email = data.email.strip().lower()
    if not re.match(r"[^@]+@[^@]+\.[^@]+", email):
        raise HTTPException(400, "Invalid email format")

    if db.query(User).filter(User.email == email).first():
        raise HTTPException(400, "Email already registered")

    user = User(
        email=email,
        full_name=data.full_name.strip(),
        hashed_password=hash_password(data.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token({"sub": user.id})
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "gmail_connected": False,
            "inbox_connected": False,
        },
    }


@app.post("/api/auth/login", response_model=TokenResponse)
def login(data: UserLogin, db: Session = Depends(get_db)):
    email = data.email.strip().lower()
    user = db.query(User).filter(User.email == email).first()
    if not user or not verify_password(data.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Incorrect email or password")

    token = create_access_token({"sub": user.id})
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "gmail_connected": user.gmail_connected,
            "inbox_connected": user.inbox_connected or user.gmail_connected,
        },
    }


@app.get("/api/auth/me")
def me(current_user: User = Depends(get_current_user)):
    return {
        "id": current_user.id,
        "email": current_user.email,
        "full_name": current_user.full_name,
        "gmail_connected": current_user.gmail_connected,
        "inbox_connected": current_user.inbox_connected or current_user.gmail_connected,
        "last_sync": current_user.last_sync.isoformat() + "Z" if current_user.last_sync else None,
    }


# ---------- Gmail OAuth routes ----------
@app.get("/auth/gmail")
def gmail_auth(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    Start Gmail OAuth flow.
    Returns an authorization_url that the frontend should redirect to.
    """
    try:
        flow = create_gmail_flow()

        # Use a clean random state, store user_id separately in DB
        oauth_state = uuid.uuid4().hex
        flow.state = oauth_state

        authorization_url, state = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
            state=oauth_state,
        )

        # Save verifier + the state so callback can find this user
        current_user.gmail_code_verifier = f"{oauth_state}::{flow.code_verifier or ''}"
        db.commit()

        print(f"[Gmail Auth] user={current_user.id} email={current_user.email} state={oauth_state}")
        return {"authorization_url": authorization_url}
    except Exception as e:
        raise HTTPException(500, f"Failed to start Gmail auth: {str(e)}")


@app.get("/auth/callback")
def gmail_callback(request: Request, db: Session = Depends(get_db)):
    """
    Google redirects here after user grants permission.
    """
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    error = request.query_params.get("error")

    print(f"[Gmail Callback] code={'yes' if code else 'no'} state={state} error={error}")

    if error:
        return RedirectResponse(url="/?gmail_error=" + error)

    if not code or not state:
        return RedirectResponse(url="/?gmail_error=missing_code")

    try:
        # Find the user who started this OAuth by matching the saved state
        user = None
        for u in db.query(User).all():
            if u.gmail_code_verifier and u.gmail_code_verifier.startswith(state + "::"):
                user = u
                break

        if not user:
            print(f"[Gmail Callback] No user found for state={state}")
            return RedirectResponse(url="/?gmail_error=invalid_user")

        print(f"[Gmail Callback] Found user={user.id} email={user.email}")

        # Restore the exact code_verifier
        saved = user.gmail_code_verifier
        code_verifier = saved.split("::", 1)[1] if "::" in saved else ""

        flow = create_gmail_flow()
        flow.state = state
        if not code_verifier:
            return RedirectResponse(url="/?gmail_error=missing_verifier")
        flow.code_verifier = code_verifier

        flow.fetch_token(code=code)
        creds = flow.credentials

        # Clear the temporary verifier
        user.gmail_code_verifier = ""

        user.gmail_token = json.dumps(credentials_to_dict(creds))
        user.gmail_connected = True
        user.inbox_connected = True
        user.last_sync = datetime.utcnow()
        db.commit()

        # Automatically fetch emails after connecting
        try:
            raw_emails = fetch_gmail_emails(user, max_results=20)
            existing_ids = {
                e.message_id for e in db.query(Email).filter(Email.user_id == user.id).all()
                if e.message_id
            }
            for item in raw_emails:
                if item["message_id"] in existing_ids:
                    continue
                body = item["body"]
                subject = item["subject"]
                e = Email(
                    user_id=user.id,
                    message_id=item["message_id"],
                    sender=item["sender"],
                    subject=subject,
                    body=body,
                    summary=simple_summarize(body),
                    category=categorize_email(subject, body),
                    priority=detect_priority(subject, body),
                    action_items=actions_to_str(extract_action_items(body)),
                    received_at=item["received_at"],
                )
                db.add(e)
            db.commit()
        except Exception as fetch_err:
            print("Auto-fetch error:", fetch_err)

        return RedirectResponse(url="/?gmail_connected=1")

    except Exception as e:
        print("Callback error:", e)
        return RedirectResponse(url="/?gmail_error=callback_failed")


@app.post("/api/inbox/gmail-sync")
def gmail_sync(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    limit: int = 20,
):
    """Fetch latest emails using Gmail API"""
    if not current_user.gmail_connected:
        raise HTTPException(400, "Gmail is not connected. Please connect first.")

    try:
        raw_emails = fetch_gmail_emails(current_user, max_results=limit)
    except Exception as e:
        raise HTTPException(400, f"Failed to fetch emails: {str(e)}")

    existing_ids = {
        e.message_id for e in db.query(Email).filter(Email.user_id == current_user.id).all()
        if e.message_id
    }

    added = 0
    for item in raw_emails:
        if item["message_id"] in existing_ids:
            continue
        body = item["body"]
        subject = item["subject"]
        e = Email(
            user_id=current_user.id,
            message_id=item["message_id"],
            sender=item["sender"],
            subject=subject,
            body=body,
            summary=simple_summarize(body),
            category=categorize_email(subject, body),
            priority=detect_priority(subject, body),
            action_items=actions_to_str(extract_action_items(body)),
            received_at=item["received_at"],
        )
        db.add(e)
        added += 1

    current_user.last_sync = datetime.utcnow()
    db.commit()

    return {"ok": True, "fetched": len(raw_emails), "added": added}


@app.post("/api/inbox/gmail-disconnect")
def gmail_disconnect(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    current_user.gmail_token = ""
    current_user.gmail_connected = False
    current_user.inbox_connected = False
    current_user.last_sync = None
    db.commit()
    return {"ok": True, "message": "Gmail disconnected"}


# ---------- Email routes ----------
@app.get("/api/emails")
def list_emails(
    category: Optional[str] = None,
    priority: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = db.query(Email).filter(Email.user_id == current_user.id)
    if category:
        q = q.filter(Email.category == category)
    if priority:
        q = q.filter(Email.priority == priority)
    rows = q.order_by(Email.received_at.desc()).all()

    emails = []
    for e in rows:
        emails.append({
            "id": e.id,
            "sender": e.sender,
            "subject": e.subject,
            "body": e.body,
            "summary": e.summary,
            "category": e.category,
            "priority": e.priority,
            "action_items": actions_from_str(e.action_items),
            "notes": e.notes,
            "received_at": e.received_at.isoformat() + "Z" if e.received_at else None,
            "created_at": e.created_at.isoformat() + "Z",
        })
    return {"emails": emails, "total": len(emails)}


@app.post("/api/emails")
def add_email(
    data: EmailCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    body = clean_text(data.body)
    subject = clean_text(data.subject)
    email = Email(
        user_id=current_user.id,
        sender=data.sender.strip(),
        subject=subject,
        body=body,
        summary=simple_summarize(body),
        category=categorize_email(subject, body),
        priority=detect_priority(subject, body),
        action_items=actions_to_str(extract_action_items(body)),
    )
    db.add(email)
    db.commit()
    db.refresh(email)
    return {
        "id": email.id,
        "sender": email.sender,
        "subject": email.subject,
        "body": email.body,
        "summary": email.summary,
        "category": email.category,
        "priority": email.priority,
        "action_items": actions_from_str(email.action_items),
        "notes": email.notes,
        "received_at": email.received_at.isoformat() + "Z",
        "created_at": email.created_at.isoformat() + "Z",
    }


@app.get("/api/emails/{email_id}")
def get_email(email_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    e = db.query(Email).filter(Email.id == email_id, Email.user_id == current_user.id).first()
    if not e:
        raise HTTPException(404, "Email not found")
    return {
        "id": e.id,
        "sender": e.sender,
        "subject": e.subject,
        "body": e.body,
        "summary": e.summary,
        "category": e.category,
        "priority": e.priority,
        "action_items": actions_from_str(e.action_items),
        "notes": e.notes,
        "received_at": e.received_at.isoformat() + "Z" if e.received_at else None,
        "created_at": e.created_at.isoformat() + "Z",
    }


@app.patch("/api/emails/{email_id}")
def update_email(email_id: str, data: EmailUpdate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    e = db.query(Email).filter(Email.id == email_id, Email.user_id == current_user.id).first()
    if not e:
        raise HTTPException(404, "Email not found")
    if data.category is not None:
        e.category = data.category
    if data.priority is not None:
        e.priority = data.priority
    if data.notes is not None:
        e.notes = data.notes
    db.commit()
    db.refresh(e)
    return {
        "id": e.id,
        "sender": e.sender,
        "subject": e.subject,
        "body": e.body,
        "summary": e.summary,
        "category": e.category,
        "priority": e.priority,
        "action_items": actions_from_str(e.action_items),
        "notes": e.notes,
        "received_at": e.received_at.isoformat() + "Z" if e.received_at else None,
        "created_at": e.created_at.isoformat() + "Z",
    }


@app.delete("/api/emails/{email_id}")
def delete_email(email_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    e = db.query(Email).filter(Email.id == email_id, Email.user_id == current_user.id).first()
    if not e:
        raise HTTPException(404, "Email not found")
    db.delete(e)
    db.commit()
    return {"ok": True}


@app.get("/api/stats")
def get_stats(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(Email).filter(Email.user_id == current_user.id).all()
    cats, pris = {}, {}
    for e in rows:
        cats[e.category] = cats.get(e.category, 0) + 1
        pris[e.priority] = pris.get(e.priority, 0) + 1
    return {
        "total": len(rows),
        "by_category": cats,
        "by_priority": pris,
        "gmail_connected": current_user.gmail_connected,
        "inbox_connected": current_user.inbox_connected or current_user.gmail_connected,
        "last_sync": current_user.last_sync.isoformat() + "Z" if current_user.last_sync else None,
    }


@app.post("/api/reorganize")
def reorganize_all(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(Email).filter(Email.user_id == current_user.id).all()
    for e in rows:
        e.summary = simple_summarize(e.body)
        e.category = categorize_email(e.subject, e.body)
        e.priority = detect_priority(e.subject, e.body)
        e.action_items = actions_to_str(extract_action_items(e.body))
    db.commit()
    return {"ok": True, "count": len(rows)}


# ---------- Frontend ----------
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def root():
    return FileResponse("static/index.html")


@app.get("/login")
def login_page():
    return FileResponse("static/login.html")


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

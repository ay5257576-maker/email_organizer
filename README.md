# AI Email Organizer – Final Year Project

Users log in → connect their real email inbox → AI organizes & summarizes their emails.

## Features

- User registration & login (JWT)
- Connect real email inbox via IMAP (Gmail, Outlook, Yahoo, etc.)
- Automatic summarization, categorization, priority detection & action items
- Sync inbox later
- Clean modern UI (pure HTML/CSS/JS)

## How to Run (with Virtual Environment)

### 1. Extract the project
```bash
unzip email_organizer.zip
cd email_organizer
```

### 2. Create and activate virtual environment

**On Windows:**
```bash
python -m venv venv
venv\Scripts\activate
```

**On Mac / Linux:**
```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Install dependencies
```bash
pip install -r requirements.txt
```

### 4. Start the server
```bash
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### 5. Open in browser
```
http://localhost:8000
```

## First Time Use

1. Register a new account
2. Click **Connect Email Inbox**
3. Choose your provider (Gmail, Outlook, etc.)
4. Enter your email + **App Password**
5. Emails will be fetched and automatically organized

### Gmail App Password
Gmail does not allow normal passwords for IMAP.  
Create one here: https://myaccount.google.com/apppasswords

## Project Structure

```
email_organizer/
├── main.py
├── requirements.txt
├── README.md
└── static/
    ├── index.html
    ├── login.html
    ├── style.css
    └── app.js
```

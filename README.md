# Context-Aware Text-to-SQL with an Intelligent Clarification Engine

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Flask](https://img.shields.io/badge/framework-Flask%203.1-black.svg)](https://flask.palletsprojects.com/)
[![Gemini API](https://img.shields.io/badge/LLM-Gemini%203.5%20Flash--Lite%20(Free)-green.svg)](https://aistudio.google.com/apikey)
[![Tests Passing](https://img.shields.io/badge/tests-32%2F32%20passed-brightgreen.svg)]()
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

An intelligent, context-aware Natural Language Interface to Databases (NLIDB) with a conversational clarification engine and a deterministic SQL safety gate. 

If a user asks an ambiguous question—such as *"Give me last month's best customer"*—the system **does not guess**. Instead, it proactively asks a specific clarifying question, remembers the conversation context, incorporates the answer, and safely generates and executes the validated SQL.

---

## 🏛️ Two-Layer Decoupled Architecture

```
                    User Query / Follow-up Answer
                                 │
                                 ▼
       ┌──────────────────────────────────────────────────┐
       │   LAYER 1: SEMANTIC REASONING LAYER (Probabilistic)│
       │   - Powered by Gemini 3.5 Flash-Lite / OpenRouter│
       │   - Ambiguity Detection (Metric / Temporal / Entity)│
       │   - Interactive Clarification Question Generator │
       │   - Multi-Turn Transcript Context Manager        │
       └─────────────────────────┬────────────────────────┘
                                 │ Candidate SQL (Untrusted)
                                 ▼
       ┌──────────────────────────────────────────────────┐
       │   LAYER 2: DETERMINISTIC SAFETY GATE (Fixed)     │
       │   - AST-level SQL Validator (Single SELECT only) │
       │   - Forbidden DDL/DML Keyword Blocker            │
       │   - Sandboxed Read-Only SQLite (file:...mode=ro) │
       │   - Bounded Self-Correction Feedback Loop (max 2)│
       └─────────────────────────┬────────────────────────┘
                                 │ Safe Rows / Error Diagnosis
                                 ▼
                         Web User Interface
```

> **Core Guarantee**: *The LLM decides what question to ask; it NEVER decides what runs against the database.*

---

## ⚡ Quick Start

### 1. Clone the repository
```bash
git clone https://github.com/Yash-Gaurr/Context-Aware-Text-to-SQL.git
cd Context-Aware-Text-to-SQL
```

### 2. Set up virtual environment & dependencies
```bash
python -m venv venv
.\venv\Scripts\activate      # Windows (PowerShell: .\venv\Scripts\Activate.ps1)
# source venv/bin/activate   # Linux / macOS
pip install -r text2sql/requirements.txt
```

### 3. Build & seed the database
```bash
python text2sql/database/setup_db.py
```
*(Seeds 15 customers, 12 products, 90 orders, and 186 items relative to a fixed reference date for 100% reproducible testing).*

### 4. Configure Gemini API Key (Free)
Copy the template `.env.example` to `.env`:
```bash
cp .env.example .env
```
Add your free Gemini API key from [Google AI Studio](https://aistudio.google.com/apikey):
```env
GEMINI_API_KEY=AIzaSy...your_key_here...
REASONER=gemini
TEXT2SQL_MODEL=gemini-3.5-flash-lite
```
*(No key handy? The application automatically falls back to an offline rule-based reasoning engine so it runs anywhere without internet!)*

### 5. Run the web server
```bash
python run.py
```
Open **`http://localhost:5000`** in your browser.

---

## 💬 Conversational Disambiguation Demo

Try asking:
```text
User:   Give me last month's best customer.
System: How would you like to define the 'best' customer for last month?
        [Highest total revenue generated]  [Greatest number of orders placed]

User:   [Clicks "Highest total revenue generated"]
System: Generates validated SQL, executes on read-only SQLite, and displays 
        Customer 13 (Priya Nair) with revenue of 34,781.0!
```

---

## 🧪 Evaluation & Testing

### Run Offline Automated Evaluation (32 Checks)
No API key or network required:
```bash
python text2sql/tests/test_pipeline.py
```
Exercises:
- **Part A**: Unambiguous queries, ambiguity detection, clarification resolution, typo grounding.
- **Part B**: Deterministic SQL safety gate (DROP TABLE, DELETE, UPDATE, statement stacking).
- **Part C**: Adversarial tests proving malicious LLM outputs are blocked and self-corrected.
- **Part D**: Gemini regression tests.

### Run Live LLM Smoke Tests (Gemini 3.5 Flash-Lite)
```bash
python text2sql/tests/test_llm_smoke.py
```

---

## 📂 Repository Structure

```
.
├── .vscode/                 # VS Code launch & debug configurations
├── text2sql/
│   ├── app.py               # Flask REST router
│   ├── core/
│   │   ├── query_handler.py # Pipeline orchestrator & self-correction loop
│   │   ├── reasoner.py      # Abstract Reasoner interface (Gemini, OpenRouter, Rule)
│   │   ├── sql_validator.py # Deterministic AST & keyword safety gate
│   │   ├── db_executor.py   # Read-only SQLite executor (mode=ro)
│   │   ├── schema_manager.py# Live schema introspection & metric registry
│   │   └── context_manager.py # Multi-turn transcript memory
│   ├── database/
│   │   ├── setup_db.py      # Reproducible DB creation script
│   │   └── ecommerce.db     # SQLite benchmark database
│   ├── static/
│   │   └── index.html       # Responsive QueryBench chat interface
│   ├── tests/
│   │   ├── test_pipeline.py # 32-check deterministic evaluation suite
│   │   └── test_llm_smoke.py# Live network LLM smoke test
│   └── requirements.txt     # Python dependencies
├── .env.example             # Configuration template
├── run.py                   # Root entry point
├── run.bat                  # Double-click launcher for Windows
└── README.md
```

---

## 🛡️ Security Features

- **Read-Only SQLite URI**: The database connection is explicitly established via `file:ecommerce.db?mode=ro`.
- **AST Validator**: Prohibits stacked semicolons, non-SELECT queries, and DDL/DML keywords (`DROP`, `DELETE`, `UPDATE`, `ALTER`, `ATTACH`).
- **Bounded Self-Correction**: When an LLM proposes invalid or unsafe SQL, it receives error feedback with a hard retry cap of 2 attempts, preventing infinite loops.

---

## 📜 Academic Project
Developed by **Yash Gaur**  
Department of Computer Science & Engineering  
Lloyd Institute of Engineering & Technology, Greater Noida  
Affiliated to **Dr. A.P.J. Abdul Kalam Technical University (AKTU)**  
Session: **2026–2027**

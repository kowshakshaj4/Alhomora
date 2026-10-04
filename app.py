import streamlit as st
import pymupdf
from dotenv import load_dotenv
import os
import json
import base64
import sqlite3
import hashlib
import secrets
import re
from pathlib import Path
from datetime import datetime
import plotly.graph_objects as go
from google import genai


# =========================================================
# PAGE CONFIG
# =========================================================

st.set_page_config(
    page_title="ALHOMORA | Unlock Your Career Potential",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded"
)


# =========================================================
# AUTHENTICATION + USER DATABASE
# =========================================================

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "career_coach.db"
ASSET_DIR = BASE_DIR / "assets"
SIGNUP_BG = ASSET_DIR / "alhomora_background.png"
AUTH_BG_ALTERNATE = ASSET_DIR / "alhomora_auth_background.png"


def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def ensure_column(conn, table, column, definition):
    existing = {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_database():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            phone TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            website TEXT,
            created_at TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS career_profiles (
            user_id INTEGER PRIMARY KEY,
            target_role TEXT DEFAULT '',
            job_description TEXT DEFAULT '',
            updated_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS resumes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            filename TEXT NOT NULL,
            resume_text TEXT NOT NULL,
            uploaded_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS resume_analyses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            resume_id INTEGER,
            target_role TEXT,
            job_description TEXT,
            analysis_json TEXT NOT NULL,
            analyzed_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(resume_id) REFERENCES resumes(id) ON DELETE SET NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS interviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            mode TEXT NOT NULL,
            target_role TEXT,
            started_at TEXT NOT NULL,
            final_score REAL DEFAULT 0,
            final_report_json TEXT DEFAULT '{}',
            completed_at TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS interview_answers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            interview_id INTEGER NOT NULL,
            question TEXT NOT NULL,
            answer TEXT DEFAULT '',
            transcript TEXT DEFAULT '',
            feedback_json TEXT DEFAULT '{}',
            created_at TEXT NOT NULL,
            FOREIGN KEY(interview_id) REFERENCES interviews(id) ON DELETE CASCADE
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS career_progress (
            user_id INTEGER PRIMARY KEY,
            career_streak INTEGER NOT NULL DEFAULT 1,
            career_xp INTEGER NOT NULL DEFAULT 120,
            mission_completed INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS roadmap_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            roadmap_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS roadmap_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            skill TEXT NOT NULL,
            task_order INTEGER NOT NULL,
            task TEXT NOT NULL,
            completed INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            completed_at TEXT,
            UNIQUE(user_id, skill, task_order),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS career_game_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            game_name TEXT NOT NULL,
            score INTEGER NOT NULL DEFAULT 0,
            questions INTEGER NOT NULL DEFAULT 0,
            xp_earned INTEGER NOT NULL DEFAULT 0,
            played_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    # Migrate the existing local database safely. These columns are optional,
    # so older career_coach.db files continue to work without being deleted.
    ensure_column(conn, "career_progress", "last_mission_date", "TEXT DEFAULT ''")
    ensure_column(conn, "career_progress", "last_activity_date", "TEXT DEFAULT ''")
    ensure_column(conn, "career_progress", "unlocked_rewards", "TEXT DEFAULT '[]'")
    ensure_column(conn, "career_progress", "games_completed", "INTEGER DEFAULT 0")
    ensure_column(conn, "career_progress", "total_interviews", "INTEGER DEFAULT 0")
    ensure_column(conn, "career_progress", "best_interview_score", "REAL DEFAULT 0")

    conn.commit()
    conn.close()


def save_career_profile(user_id, target_role, job_description):
    conn = get_db()
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        """
        INSERT INTO career_profiles (user_id, target_role, job_description, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            target_role=excluded.target_role,
            job_description=excluded.job_description,
            updated_at=excluded.updated_at
        """,
        (user_id, target_role or "", job_description or "", now)
    )
    conn.commit()
    conn.close()


def save_resume(user_id, filename, resume_text):
    conn = get_db()
    existing = conn.execute(
        """
        SELECT id FROM resumes
        WHERE user_id=? AND filename=? AND resume_text=?
        ORDER BY uploaded_at DESC LIMIT 1
        """,
        (user_id, filename, resume_text)
    ).fetchone()
    if existing:
        conn.close()
        return existing["id"]

    now = datetime.now().isoformat(timespec="seconds")
    cur = conn.execute(
        "INSERT INTO resumes (user_id, filename, resume_text, uploaded_at) VALUES (?, ?, ?, ?)",
        (user_id, filename, resume_text, now)
    )
    resume_id = cur.lastrowid
    conn.commit()
    conn.close()
    return resume_id


def save_resume_analysis(user_id, resume_id, target_role, job_description, analysis):
    conn = get_db()
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        """
        INSERT INTO resume_analyses
        (user_id, resume_id, target_role, job_description, analysis_json, analyzed_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (user_id, resume_id, target_role, job_description, json.dumps(analysis), now)
    )
    conn.execute(
        """
        INSERT INTO roadmap_snapshots (user_id, roadmap_json, created_at)
        VALUES (?, ?, ?)
        """,
        (user_id, json.dumps({"target_role": target_role, "analysis": analysis}), now)
    )
    conn.commit()
    conn.close()
    sync_roadmap_tasks(user_id, analysis.get("missing_skills", []))


def get_progress_record(user_id):
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM career_progress WHERE user_id=?",
        (user_id,)
    ).fetchone()
    conn.close()
    return row


REWARD_DEFINITIONS = [
    (200, "advanced_resume_template", "Advanced Resume Template"),
    (400, "advanced_interview_challenge", "Advanced Interview Challenge"),
    (600, "career_mastery_badge", "Career Mastery Badge"),
]


def reward_ids_for_xp(xp):
    return [reward_id for threshold, reward_id, _ in REWARD_DEFINITIONS if int(xp) >= threshold]


def save_career_progress(
    user_id,
    streak,
    xp,
    mission_completed,
    last_mission_date=None,
    last_activity_date=None,
    unlocked_rewards=None,
    games_completed=None,
    total_interviews=None,
    best_interview_score=None,
):
    current = get_progress_record(user_id)
    today = datetime.now().date().isoformat()
    last_mission_date = last_mission_date if last_mission_date is not None else (current["last_mission_date"] if current else "")
    last_activity_date = last_activity_date if last_activity_date is not None else (current["last_activity_date"] if current else today)
    unlocked_rewards = unlocked_rewards if unlocked_rewards is not None else (
        json.loads(current["unlocked_rewards"] or "[]") if current and current["unlocked_rewards"] else []
    )
    unlocked_rewards = list(dict.fromkeys(
        list(unlocked_rewards or []) + reward_ids_for_xp(xp)
    ))
    games_completed = games_completed if games_completed is not None else (int(current["games_completed"] or 0) if current else 0)
    total_interviews = total_interviews if total_interviews is not None else (int(current["total_interviews"] or 0) if current else 0)
    best_interview_score = best_interview_score if best_interview_score is not None else (float(current["best_interview_score"] or 0) if current else 0)

    conn = get_db()
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        """
        INSERT INTO career_progress
        (user_id, career_streak, career_xp, mission_completed, updated_at,
         last_mission_date, last_activity_date, unlocked_rewards, games_completed,
         total_interviews, best_interview_score)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            career_streak=excluded.career_streak,
            career_xp=excluded.career_xp,
            mission_completed=excluded.mission_completed,
            updated_at=excluded.updated_at,
            last_mission_date=excluded.last_mission_date,
            last_activity_date=excluded.last_activity_date,
            unlocked_rewards=excluded.unlocked_rewards,
            games_completed=excluded.games_completed,
            total_interviews=excluded.total_interviews,
            best_interview_score=excluded.best_interview_score
        """,
        (
            user_id, int(streak), int(xp), int(bool(mission_completed)), now,
            last_mission_date or "", last_activity_date or today,
            json.dumps(unlocked_rewards or []), int(games_completed or 0),
            int(total_interviews or 0), float(best_interview_score or 0)
        )
    )
    conn.commit()
    conn.close()


def award_xp(user_id, amount, reason=""):
    row = get_progress_record(user_id)
    streak = int(row["career_streak"] or 1) if row else 1
    xp = int(row["career_xp"] or 120) if row else 120
    mission = bool(row["mission_completed"]) if row else False
    last_mission = row["last_mission_date"] if row else ""
    rewards = json.loads(row["unlocked_rewards"] or "[]") if row and row["unlocked_rewards"] else []
    games = int(row["games_completed"] or 0) if row else 0
    interviews = int(row["total_interviews"] or 0) if row else 0
    best = float(row["best_interview_score"] or 0) if row else 0
    xp += int(amount)
    save_career_progress(
        user_id, streak, xp, mission,
        last_mission_date=last_mission,
        last_activity_date=datetime.now().date().isoformat(),
        unlocked_rewards=rewards,
        games_completed=games,
        total_interviews=interviews,
        best_interview_score=best,
    )
    if st.session_state.get("user_id") == user_id:
        st.session_state.career_xp = xp
        st.session_state.unlocked_rewards = reward_ids_for_xp(xp)
    return xp


def complete_daily_mission(user_id):
    row = get_progress_record(user_id)
    today = datetime.now().date()
    streak = int(row["career_streak"] or 1) if row else 1
    xp = int(row["career_xp"] or 120) if row else 120
    last = (row["last_mission_date"] or "") if row else ""
    rewards = json.loads(row["unlocked_rewards"] or "[]") if row and row["unlocked_rewards"] else []
    games = int(row["games_completed"] or 0) if row else 0
    interviews = int(row["total_interviews"] or 0) if row else 0
    best = float(row["best_interview_score"] or 0) if row else 0

    if last == today.isoformat():
        return False, streak, xp

    if last:
        try:
            previous = datetime.fromisoformat(last).date()
            if (today - previous).days == 1:
                streak += 1
            elif (today - previous).days > 1:
                streak = 1
        except ValueError:
            streak = max(streak, 1)

    xp += 50
    save_career_progress(
        user_id, streak, xp, True,
        last_mission_date=today.isoformat(),
        last_activity_date=today.isoformat(),
        unlocked_rewards=rewards,
        games_completed=games,
        total_interviews=interviews,
        best_interview_score=best,
    )
    return True, streak, xp


def start_interview_record(user_id, mode, target_role):
    conn = get_db()
    now = datetime.now().isoformat(timespec="seconds")
    cur = conn.execute(
        "INSERT INTO interviews (user_id, mode, target_role, started_at) VALUES (?, ?, ?, ?)",
        (user_id, mode, target_role or "", now)
    )
    interview_id = cur.lastrowid
    conn.commit()
    conn.close()
    return interview_id


def save_interview_answer(interview_id, question, answer, transcript, feedback):
    if not interview_id:
        return
    conn = get_db()
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        """
        INSERT INTO interview_answers
        (interview_id, question, answer, transcript, feedback_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (interview_id, question or "", answer or "", transcript or "", json.dumps(feedback or {}), now)
    )
    conn.commit()
    conn.close()


def complete_interview_record(interview_id, report):
    if not interview_id:
        return
    conn = get_db()
    now = datetime.now().isoformat(timespec="seconds")
    try:
        score = float((report or {}).get("overall_score", 0) or 0)
    except (TypeError, ValueError):
        score = 0
    conn.execute(
        """
        UPDATE interviews
        SET final_score=?, final_report_json=?, completed_at=?
        WHERE id=?
        """,
        (score, json.dumps(report or {}), now, interview_id)
    )
    conn.commit()
    conn.close()

    row = get_progress_record(st.session_state.user_id)
    streak = int(row["career_streak"] or 1) if row else 1
    xp = int(row["career_xp"] or 120) if row else 120
    mission = bool(row["mission_completed"]) if row else False
    last_mission = row["last_mission_date"] if row else ""
    rewards = json.loads(row["unlocked_rewards"] or "[]") if row and row["unlocked_rewards"] else []
    games = int(row["games_completed"] or 0) if row else 0
    interviews = int(row["total_interviews"] or 0) if row else 0
    best = float(row["best_interview_score"] or 0) if row else 0
    interviews += 1
    best = max(best, score)
    save_career_progress(
        st.session_state.user_id, streak, xp, mission,
        last_mission_date=last_mission,
        last_activity_date=datetime.now().date().isoformat(),
        unlocked_rewards=rewards,
        games_completed=games,
        total_interviews=interviews,
        best_interview_score=best,
    )


def load_interview_history(user_id, limit=10):
    conn = get_db()
    rows = conn.execute(
        """
        SELECT id, mode, target_role, started_at, final_score, completed_at
        FROM interviews WHERE user_id=?
        ORDER BY started_at DESC LIMIT ?
        """,
        (user_id, limit)
    ).fetchall()
    conn.close()
    return rows


def sync_roadmap_tasks(user_id, missing_skills):
    skills = []
    for raw in missing_skills or []:
        skill = str(raw).strip()
        if skill and skill.lower() not in {s.lower() for s in skills}:
            skills.append(skill)

    conn = get_db()
    now = datetime.now().isoformat(timespec="seconds")
    templates = [
        "Learn the fundamentals and core concepts.",
        "Build a small practical project using this skill.",
        "Practice interview questions and explain the skill in your own words.",
    ]
    for skill in skills:
        for order, template in enumerate(templates, start=1):
            conn.execute(
                """
                INSERT OR IGNORE INTO roadmap_tasks
                (user_id, skill, task_order, task, completed, created_at)
                VALUES (?, ?, ?, ?, 0, ?)
                """,
                (user_id, skill, order, f"{template} Focus: {skill}.", now)
            )
    conn.commit()
    conn.close()


def load_roadmap_tasks(user_id):
    conn = get_db()
    rows = conn.execute(
        """
        SELECT id, skill, task_order, task, completed, completed_at
        FROM roadmap_tasks WHERE user_id=?
        ORDER BY skill, task_order
        """,
        (user_id,)
    ).fetchall()
    conn.close()
    return rows


def set_roadmap_task(user_id, task_id, completed=True):
    conn = get_db()
    now = datetime.now().isoformat(timespec="seconds") if completed else None
    conn.execute(
        "UPDATE roadmap_tasks SET completed=?, completed_at=? WHERE id=? AND user_id=?",
        (int(bool(completed)), now, task_id, user_id)
    )
    conn.commit()
    conn.close()


def save_game_result(user_id, game_name, score, questions, xp_earned):
    """Save a completed game and update CareerQuest progress exactly once."""
    conn = get_db()
    conn.execute(
        """
        INSERT INTO career_game_results
        (user_id, game_name, score, questions, xp_earned, played_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            game_name,
            int(score),
            int(questions),
            int(xp_earned),
            datetime.now().isoformat(timespec="seconds"),
        )
    )
    conn.commit()
    conn.close()

    row = get_progress_record(user_id)
    streak = int(row["career_streak"] or 1) if row else 1
    xp = int(row["career_xp"] or 120) if row else 120
    mission = bool(row["mission_completed"]) if row else False
    last_mission = row["last_mission_date"] if row else ""
    rewards = json.loads(row["unlocked_rewards"] or "[]") if row and row["unlocked_rewards"] else []
    games = int(row["games_completed"] or 0) if row else 0
    interviews = int(row["total_interviews"] or 0) if row else 0
    best = float(row["best_interview_score"] or 0) if row else 0

    games += 1
    xp += int(xp_earned)

    save_career_progress(
        user_id, streak, xp, mission,
        last_mission_date=last_mission,
        last_activity_date=datetime.now().date().isoformat(),
        unlocked_rewards=rewards,
        games_completed=games,
        total_interviews=interviews,
        best_interview_score=best,
    )

    unlocked = reward_ids_for_xp(xp)
    if st.session_state.get("user_id") == user_id:
        st.session_state.career_xp = xp
        st.session_state.games_completed = games
        st.session_state.unlocked_rewards = unlocked

    return xp, games, unlocked


def load_user_data(user_id):
    conn = get_db()
    profile = conn.execute(
        "SELECT target_role, job_description FROM career_profiles WHERE user_id=?",
        (user_id,)
    ).fetchone()
    resume = conn.execute(
        "SELECT id, filename, resume_text FROM resumes WHERE user_id=? ORDER BY uploaded_at DESC LIMIT 1",
        (user_id,)
    ).fetchone()
    analysis = conn.execute(
        "SELECT analysis_json FROM resume_analyses WHERE user_id=? ORDER BY analyzed_at DESC LIMIT 1",
        (user_id,)
    ).fetchone()
    progress = conn.execute(
        "SELECT * FROM career_progress WHERE user_id=?",
        (user_id,)
    ).fetchone()
    interview = conn.execute(
        """
        SELECT id, mode, final_report_json FROM interviews
        WHERE user_id=? AND completed_at IS NOT NULL
        ORDER BY completed_at DESC LIMIT 1
        """,
        (user_id,)
    ).fetchone()
    conn.close()

    if profile:
        st.session_state.target_role = profile["target_role"] or ""
        st.session_state.job_description = profile["job_description"] or ""
    if resume:
        st.session_state.resume_id = resume["id"]
        st.session_state.resume_name = resume["filename"] or ""
        st.session_state.resume_text = resume["resume_text"] or ""
    if analysis:
        try:
            st.session_state.jd_analysis = json.loads(analysis["analysis_json"] or "{}")
        except json.JSONDecodeError:
            st.session_state.jd_analysis = {}
    if progress:
        st.session_state.career_streak = int(progress["career_streak"] or 1)
        st.session_state.career_xp = int(progress["career_xp"] or 120)
        last_mission = progress["last_mission_date"] or ""
        st.session_state.mission_completed = last_mission == datetime.now().date().isoformat()
        try:
            stored_rewards = json.loads(progress["unlocked_rewards"] or "[]")
        except json.JSONDecodeError:
            stored_rewards = []
        st.session_state.unlocked_rewards = list(dict.fromkeys(
            list(stored_rewards or []) + reward_ids_for_xp(st.session_state.career_xp)
        ))
        st.session_state.games_completed = int(progress["games_completed"] or 0)
        st.session_state.total_interviews = int(progress["total_interviews"] or 0)
        st.session_state.best_interview_score = float(progress["best_interview_score"] or 0)
    else:
        save_career_progress(user_id, 1, 120, False, last_mission_date="", last_activity_date=datetime.now().date().isoformat(), unlocked_rewards=[])
        st.session_state.career_streak = 1
        st.session_state.career_xp = 120
        st.session_state.mission_completed = False
        st.session_state.unlocked_rewards = []
        st.session_state.games_completed = 0
        st.session_state.total_interviews = 0
        st.session_state.best_interview_score = 0

    if st.session_state.jd_analysis:
        sync_roadmap_tasks(user_id, st.session_state.jd_analysis.get("missing_skills", []))

    if interview:
        st.session_state.current_interview_id = interview["id"]
        try:
            st.session_state.final_interview_report = json.loads(interview["final_report_json"] or "{}")
        except json.JSONDecodeError:
            st.session_state.final_interview_report = {}

def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        120000
    )
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password, stored_hash):
    try:
        salt_hex, digest_hex = stored_hash.split("$", 1)
        salt = bytes.fromhex(salt_hex)
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            120000
        )
        return secrets.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def create_user(full_name, email, phone, password, website):
    conn = get_db()
    try:
        conn.execute(
            """
            INSERT INTO users
            (full_name, email, phone, password_hash, website, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                full_name.strip(),
                email.strip().lower(),
                phone.strip(),
                hash_password(password),
                website.strip(),
                datetime.now().isoformat(timespec="seconds")
            )
        )
        conn.commit()
        return True, "Account created successfully."
    except sqlite3.IntegrityError:
        return False, "An account with this email already exists."
    finally:
        conn.close()


def authenticate_user(email, password):
    conn = get_db()
    user = conn.execute(
        "SELECT * FROM users WHERE email = ?",
        (email.strip().lower(),)
    ).fetchone()
    conn.close()

    if user and verify_password(password, user["password_hash"]):
        return dict(user)
    return None


def render_auth_styles(background_data):
    st.html(f"""
    <style>
        html, body,
        [data-testid="stAppViewContainer"],
        [data-testid="stMain"] {{
            background: #070A12 !important;
        }}

        [data-testid="stHeader"] {{
            display: none !important;
        }}

        [data-testid="stSidebar"] {{
            display: none !important;
        }}

        .block-container {{
            max-width: 100% !important;
            width: 100% !important;
            min-height: 100vh !important;
            padding: 0 !important;
            margin: 0 !important;
        }}

        .alhomora-auth-background {{
            position: fixed;
            inset: 0;
            width: 100vw;
            height: 100vh;
            background-image: url("data:image/png;base64,{background_data}");
            background-size: cover;
            background-position: center center;
            background-repeat: no-repeat;
            z-index: 0;
        }}

        .alhomora-auth-background::after {{
            content: "";
            position: absolute;
            inset: 0;
            background: rgba(3, 6, 13, 0.05);
            pointer-events: none;
        }}

        /* The right column is the real interactive signup area.
           It sits over the empty/right side of the artwork. */
        div[data-testid="column"]:has(.alhomora-auth-right-column) {{
            position: relative !important;
            z-index: 3 !important;
            display: flex !important;
            align-items: center !important;
            justify-content: center !important;
            min-height: 100vh !important;
            padding: 34px 5vw 34px 18px !important;
            box-sizing: border-box !important;
        }}

        div[data-testid="column"]:has(.alhomora-auth-right-column) > div {{
            width: 100% !important;
            max-width: 540px !important;
        }}

        .alhomora-auth-right-column {{
            width: 100% !important;
            max-width: 540px !important;
        }}

        .alhomora-auth-right-column [data-testid="stVerticalBlockBorderWrapper"] {{
            background: rgba(13, 16, 25, 0.94) !important;
            border: 1px solid rgba(194, 145, 70, 0.55) !important;
            border-radius: 17px !important;
            box-shadow: 0 22px 60px rgba(0,0,0,.50), 0 0 0 1px rgba(255,255,255,.025) inset !important;
            padding: 26px 28px 24px !important;
            backdrop-filter: blur(5px) !important;
            -webkit-backdrop-filter: blur(5px) !important;
        }}

        .auth-card-title {{
            color: #F8FAFC;
            font-size: 29px;
            font-weight: 800;
            line-height: 1.15;
            letter-spacing: -.6px;
            margin: 0 0 7px 0;
        }}

        .auth-card-subtitle {{
            color: #A0AEC0;
            font-size: 13px;
            line-height: 1.5;
            margin: 0 0 18px 0;
        }}

        .auth-card-note {{
            color: #64748B;
            font-size: 10px;
            line-height: 1.45;
            text-align: center;
            margin-top: 8px;
        }}

        .auth-divider {{
            display: flex;
            align-items: center;
            gap: 11px;
            margin: 18px 0 11px;
            color: #8A94A6;
            font-size: 11px;
        }}

        .auth-divider::before, .auth-divider::after {{
            content: "";
            height: 1px;
            flex: 1;
            background: #303848;
        }}

        .social-row {{
            display: flex;
            justify-content: center;
            gap: 10px;
            margin-top: 5px;
        }}

        .social-icon {{
            width: 49px; height: 39px; border-radius: 9px;
            background: rgba(17,24,39,.95); border: 1px solid #30394C;
            color: #D7DEE8; display: flex; align-items: center; justify-content: center;
            font-size: 14px; font-weight: 800;
        }}

        .auth-footer {{
            color: #B5BFCE; text-align: center; font-size: 12px;
            margin-top: 13px; padding-bottom: 3px;
        }}

        .auth-accent {{ color: #D7A347; font-weight: 800; }}

        .stTextInput {{ margin-bottom: 8px !important; }}

        .stTextInput input {{
            background: #111827 !important; color: #E5E7EB !important;
            border: 1px solid #2B3549 !important; border-radius: 8px !important;
            min-height: 40px !important; height: 40px !important;
            padding: 0 13px !important; font-size: 12px !important; box-sizing: border-box !important;
        }}

        .stTextInput input::placeholder {{ color: #697A95 !important; opacity: 1 !important; }}
        .stTextInput input:focus {{ border-color: #C99642 !important; box-shadow: 0 0 0 1px #C99642 !important; }}
        .stTextInput label {{ display: none !important; }}

        [data-testid="stCheckbox"] {{ margin-top: 2px !important; margin-bottom: 8px !important; }}
        [data-testid="stCheckbox"] label, [data-testid="stCheckbox"] label p {{ color: #AAB4C3 !important; font-size: 11px !important; }}

        .stFormSubmitButton > button, .stButton > button {{
            background: linear-gradient(135deg,#D2A04B,#995A21) !important;
            color: #FFF8EA !important; border: 1px solid #D2A04B !important;
            border-radius: 9px !important; min-height: 43px !important; height: 43px !important;
            font-size: 12px !important; font-weight: 800 !important;
            box-shadow: 0 8px 24px rgba(154,91,34,.25) !important;
        }}

        .stFormSubmitButton > button:hover, .stButton > button:hover {{
            background: linear-gradient(135deg,#E0AF58,#A96628) !important; border-color: #E0AF58 !important;
        }}

        .auth-switch-button {{ margin-top: -43px !important; opacity: 0 !important; height: 43px !important; position: relative !important; z-index: 5 !important; }}
        .auth-switch-button button {{ opacity: 0 !important; cursor: pointer !important; }}
        [data-testid="stAlert"] {{ border-radius: 9px !important; font-size: 11px !important; }}

        @media (max-width: 900px) {{
            div[data-testid="column"]:has(.alhomora-auth-right-column) {{
                min-height: auto !important; padding: 30px 20px !important;
            }}
            .alhomora-auth-background {{ background-position: 35% center; }}
            .alhomora-auth-right-column {{ max-width: 560px !important; }}
        }}
    </style>
    """)


def auth_page():
    init_database()

    if "auth_view" not in st.session_state:
        st.session_state.auth_view = "signup"
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False
    if "user_id" not in st.session_state:
        st.session_state.user_id = None
    if "user_name" not in st.session_state:
        st.session_state.user_name = ""
    if "user_email" not in st.session_state:
        st.session_state.user_email = ""
    if "user_data_loaded" not in st.session_state:
        st.session_state.user_data_loaded = False

    if st.session_state.authenticated:
        return True

    background_file = SIGNUP_BG if SIGNUP_BG.exists() else AUTH_BG_ALTERNATE
    if background_file.exists():
        background_data = base64.b64encode(background_file.read_bytes()).decode("utf-8")
    else:
        background_data = ""

    render_auth_styles(background_data)
    st.html('<div class="alhomora-auth-background"></div>')

    # Keep Streamlit widgets in a normal column. The background artwork
    # already has an intentionally empty right side for the live form.
    _, right_form = st.columns([1.55, 0.95], gap="small")

    with right_form:
        st.html('<div class="alhomora-auth-right-column"></div>')

        with st.container(border=True):
            if st.session_state.auth_view == "signup":
                st.html("""
                <div class="auth-card-title">Create an account</div>
                <div class="auth-card-subtitle">Start your journey with Alhomora.</div>
                """)

                with st.form("signup_form", clear_on_submit=False):
                    full_name = st.text_input("Full Name", placeholder="Full Name", label_visibility="collapsed")
                    email = st.text_input("Email Address", placeholder="Email Address", label_visibility="collapsed")
                    phone = st.text_input("Phone Number", placeholder="Phone Number", label_visibility="collapsed")
                    password = st.text_input("Password", type="password", placeholder="Password", label_visibility="collapsed")
                    confirm_password = st.text_input("Confirm Password", type="password", placeholder="Confirm Password", label_visibility="collapsed")
                    website = st.text_input("Website", placeholder="Your Website (Optional)", label_visibility="collapsed")
                    terms = st.checkbox("I agree to the Terms of Service and Privacy Policy")
                    submitted = st.form_submit_button("Create an account  →", use_container_width=True)

                if submitted:
                    if not full_name.strip() or not email.strip() or not phone.strip() or not password:
                        st.error("Please fill in all required fields.")
                    elif not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email.strip()):
                        st.error("Please enter a valid email address.")
                    elif len(password) < 8:
                        st.error("Password must contain at least 8 characters.")
                    elif password != confirm_password:
                        st.error("Passwords do not match.")
                    elif not terms:
                        st.error("Please agree to the terms of service.")
                    else:
                        ok, message = create_user(full_name, email, phone, password, website)
                        if ok:
                            # New users start with their own persistent progress record.
                            user = authenticate_user(email, password)
                            if user:
                                save_career_progress(user["id"], 1, 120, False)
                            st.session_state.auth_view = "login"
                            st.success("Account created successfully. Please sign in.")
                            st.rerun()
                        else:
                            st.error(message)

                st.html("""
                <div class="auth-divider"><span>OR</span></div>
                <div class="social-row">
                    <div class="social-icon">G</div>
                    <div class="social-icon">⌁</div>
                    <div class="social-icon">in</div>
                </div>
                <div class="auth-card-note">Social sign-up will be connected in a future OAuth integration.</div>
                <div class="auth-footer">Already a member? <span class="auth-accent">Sign In</span></div>
                """)

                if st.button("Sign In", key="open_login", use_container_width=True):
                    st.session_state.auth_view = "login"
                    st.rerun()
            else:
                st.html("""
                <div class="auth-card-title">Welcome back</div>
                <div class="auth-card-subtitle">Continue your journey with Alhomora.</div>
                """)

                with st.form("login_form"):
                    login_email = st.text_input("Login Email", placeholder="Email Address", label_visibility="collapsed")
                    login_password = st.text_input("Login Password", type="password", placeholder="Password", label_visibility="collapsed")
                    login_submitted = st.form_submit_button("Sign In  →", use_container_width=True)

                if login_submitted:
                    user = authenticate_user(login_email, login_password)
                    if user:
                        st.session_state.authenticated = True
                        st.session_state.user_id = user["id"]
                        st.session_state.user_name = user["full_name"]
                        st.session_state.user_email = user["email"]
                        st.session_state.user_data_loaded = False
                        st.session_state.auth_view = "signup"
                        st.rerun()
                    else:
                        st.error("Incorrect email or password.")

                st.html("""
                <div class="auth-divider"><span>OR</span></div>
                <div class="social-row">
                    <div class="social-icon">G</div>
                    <div class="social-icon">⌁</div>
                    <div class="social-icon">in</div>
                </div>
                <div class="auth-card-note">Social login will be connected in a future OAuth integration.</div>
                <div class="auth-footer">Don't have an account? <span class="auth-accent">Create an account</span></div>
                """)

                if st.button("Create an account", key="open_signup", use_container_width=True):
                    st.session_state.auth_view = "signup"
                    st.rerun()

    return False



if not auth_page():
    st.stop()


# =========================================================
# GEMINI
# =========================================================

load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")
client = None
MODEL = "gemini-3.6-flash"

if api_key:
    try:
        client = genai.Client(api_key=api_key)
    except Exception:
        client = None


# =========================================================
# SESSION STATE
# =========================================================

defaults = {
    "page": "Dashboard",

    # Resume
    "resume_text": "",
    "resume_name": "",
    "resume_id": None,
    "target_role": "",
    "job_description": "",
    "jd_analysis": {},

    # Interview
    "interview_mode": "Text",
    "interview_started": False,
    "interview_question": "",
    "interview_answer": "",
    "interview_transcript": "",
    "interview_feedback": {},
    "followup_question": "",
    "final_interview_report": {},
    "current_interview_id": None,

    # CareerQuest
    "career_streak": 1,
    "career_xp": 120,
    "mission_completed": False,
    "unlocked_rewards": [],
    "games_completed": 0,
    "total_interviews": 0,
    "best_interview_score": 0.0,
    "active_game": "",
    "game_question_index": 0,
    "game_score": 0,
    "game_questions": [],
    "game_finished": False,
    "active_reward": "",

    # Authentication
    "authenticated": False,
    "user_id": None,
    "user_name": "",
    "user_email": "",
}

for key, value in defaults.items():
    if key not in st.session_state:
        st.session_state[key] = value

if st.session_state.authenticated and st.session_state.user_id:
    if not st.session_state.get("user_data_loaded", False):
        load_user_data(st.session_state.user_id)
        st.session_state.user_data_loaded = True


# =========================================================
# DARK THEME
# =========================================================

st.html("""
<style>

html, body {
    background: #0B1120 !important;
}

[data-testid="stAppViewContainer"] {
    background: #0B1120 !important;
}

[data-testid="stMain"] {
    background: #0B1120 !important;
}

[data-testid="stHeader"] {
    background: #0B1120 !important;
}

/* ================= SIDEBAR ================= */

[data-testid="stSidebar"] {
    background: #0A0F1C !important;
}

[data-testid="stSidebar"] > div:first-child {
    background: #0A0F1C !important;
}

[data-testid="stSidebar"] * {
    color: #E5E7EB;
}

.sidebar-brand {
    color: #F1F5F9;
    font-size: 23px;
    font-weight: 800;
    margin-bottom: 5px;
}

.sidebar-subtitle {
    color: #7C8AA5;
    font-size: 13px;
    margin-bottom: 22px;
}

.sidebar-divider {
    height: 1px;
    background: #1E293B;
    margin: 18px 0;
}

.sidebar-streak {
    background: #111827;
    border: 1px solid #1E293B;
    border-radius: 15px;
    padding: 17px;
    margin-top: 20px;
}

.sidebar-streak-label {
    color: #64748B;
    font-size: 11px;
    font-weight: 800;
    letter-spacing: 1px;
}

.sidebar-streak-value {
    color: #E5E7EB;
    font-size: 27px;
    font-weight: 800;
    margin-top: 6px;
}

.sidebar-streak-text {
    color: #64748B;
    font-size: 12px;
    margin-top: 4px;
}


/* ================= MAIN ================= */

.block-container {
    padding-top: 2rem;
    padding-bottom: 4rem;
}


/* ================= HERO ================= */

.hero {
    background: linear-gradient(
        135deg,
        #111827 0%,
        #172554 55%,
        #1D4ED8 100%
    );

    border: 1px solid #243B72;
    border-radius: 24px;

    padding: 36px;

    margin-bottom: 28px;

    box-shadow:
        0 12px 35px rgba(0, 0, 0, 0.25);
}

.hero-small {
    color: #93C5FD;
    font-size: 12px;
    font-weight: 800;
    letter-spacing: 1.4px;
    margin-bottom: 10px;
}

.hero-title {
    color: #E5E7EB;
    font-size: 35px;
    font-weight: 800;
    line-height: 1.15;
    margin-bottom: 12px;
}

.hero-text {
    color: #AFC0D8;
    font-size: 15px;
    line-height: 1.65;
    max-width: 900px;
}


/* ================= SECTION TITLES ================= */

.section-title {
    color: #E5E7EB;
    font-size: 23px;
    font-weight: 800;
    margin-top: 24px;
    margin-bottom: 15px;
}


/* ================= CARDS ================= */

.app-card {
    background: #111827;
    border: 1px solid #1E293B;
    border-radius: 18px;
    padding: 21px;
    margin-bottom: 17px;

    box-shadow:
        0 8px 25px rgba(0, 0, 0, 0.18);
}

.card-title {
    color: #E5E7EB;
    font-size: 17px;
    font-weight: 750;
    margin-bottom: 8px;
}

.card-text {
    color: #8998AE;
    font-size: 14px;
    line-height: 1.65;
}


/* ================= METRICS ================= */

.metric {
    background: #111827;
    border: 1px solid #1E293B;
    border-radius: 17px;
    padding: 20px;
    min-height: 115px;

    box-shadow:
        0 8px 25px rgba(0, 0, 0, 0.16);
}

.metric-title {
    color: #7C8AA5;
    font-size: 13px;
    font-weight: 650;
}

.metric-value {
    color: #E5E7EB;
    font-size: 28px;
    font-weight: 800;
    margin-top: 8px;
}

.metric-subtitle {
    color: #64748B;
    font-size: 12px;
    margin-top: 5px;
}


/* ================= QUESTION ================= */

.question-box {
    background: #101B32;
    border: 1px solid #263B66;
    border-radius: 18px;
    padding: 25px;
    margin: 18px 0;
}

.question-label {
    color: #60A5FA;
    font-size: 11px;
    font-weight: 800;
    letter-spacing: 1px;
    text-transform: uppercase;
}

.question-text {
    color: #E5E7EB;
    font-size: 21px;
    font-weight: 700;
    line-height: 1.5;
    margin-top: 10px;
}


/* ================= VOICE CARD ================= */

.voice-box {
    background: #111827;
    border: 1px solid #263B66;
    border-radius: 20px;
    padding: 27px;
    margin: 18px 0;
}

.voice-title {
    color: #E5E7EB;
    font-size: 20px;
    font-weight: 750;
    margin-bottom: 7px;
}

.voice-text {
    color: #8998AE;
    line-height: 1.6;
}


/* ================= MODE CARD ================= */

.mode-card {
    background: #111827;
    border: 1px solid #1E293B;
    border-radius: 18px;
    padding: 20px;
    margin-bottom: 20px;
}

.mode-title {
    color: #E5E7EB;
    font-size: 18px;
    font-weight: 750;
    margin-bottom: 5px;
}

.mode-description {
    color: #7C8AA5;
    font-size: 13px;
}


/* ================= STREAMLIT WIDGETS ================= */

.stTextInput input,
.stTextArea textarea {
    background: #111827 !important;
    color: #E5E7EB !important;
    border: 1px solid #263247 !important;
    border-radius: 11px !important;
}

.stTextInput input::placeholder,
.stTextArea textarea::placeholder {
    color: #64748B !important;
}

[data-testid="stFileUploader"] {
    background: #111827 !important;
    border: 1px solid #1E293B !important;
    border-radius: 15px !important;
}

[data-testid="stFileUploader"] * {
    color: #CBD5E1 !important;
}


/* Buttons */

.stButton > button {
    background: #172033 !important;
    color: #CBD5E1 !important;
    border: 1px solid #263247 !important;
    border-radius: 10px !important;
    min-height: 42px;
    font-weight: 650;
}

.stButton > button:hover {
    background: #1E293B !important;
    border-color: #3B82F6 !important;
    color: #E5E7EB !important;
}

.stButton > button[kind="primary"] {
    background: #2563EB !important;
    border-color: #2563EB !important;
    color: #F8FAFC !important;
}

.stButton > button[kind="primary"]:hover {
    background: #1D4ED8 !important;
    border-color: #1D4ED8 !important;
}


/* Radio */

[data-testid="stRadio"] label {
    color: #CBD5E1 !important;
}


/* Progress */

.stProgress > div > div {
    background: #2563EB !important;
}


/* Audio */

audio {
    width: 100%;
}

</style>
""")


# =========================================================
# HELPERS
# =========================================================

def go_to(page_name):
    st.session_state.page = page_name
    st.rerun()


def ai_error(error):
    message = str(error)
    if "429" in message or "TooManyRequests" in message or "quota" in message.lower():
        st.warning(
            "🧠 Gemini is temporarily unavailable because its current API quota/rate limit is reached. "
            "Your data is still saved. Alhomora will use local career tools where possible."
        )
    else:
        st.warning(f"🧠 AI service is temporarily unavailable: {message}")


def call_gemini(prompt, input_payload=None):
    if client is None:
        return None
    try:
        response = client.interactions.create(
            model=MODEL,
            input=prompt if input_payload is None else input_payload
        )
        return response.output_text.strip()
    except Exception as exc:
        ai_error(exc)
        return None


def clean_json(text):
    text = text.strip()

    if text.startswith("```"):
        text = text.replace("```json", "")
        text = text.replace("```", "")
        text = text.strip()

    start = text.find("{")
    end = text.rfind("}")

    if start != -1 and end != -1:
        text = text[start:end + 1]

    return json.loads(text)




def _tokens(text):
    return set(re.findall(r"[a-zA-Z][a-zA-Z0-9+#.-]{1,}", (text or "").lower()))


def local_resume_analysis(resume_text, target_role, job_description):
    """A deterministic, non-AI fallback so the website remains usable when Gemini is unavailable."""
    resume_tokens = _tokens(resume_text)
    jd_tokens = _tokens(job_description)
    role_tokens = _tokens(target_role)

    common = sorted((resume_tokens & jd_tokens) | (resume_tokens & role_tokens))
    stop = {"with", "from", "that", "this", "your", "using", "role", "engineer", "developer", "and", "the", "for", "into", "work"}
    common = [x for x in common if x not in stop][:12]

    skill_catalog = [
        "python", "machine learning", "deep learning", "sql", "mysql", "fastapi", "flask",
        "langchain", "rag", "prompt engineering", "llms", "rest", "docker", "kubernetes",
        "git", "aws", "azure", "google adk", "mcp", "faiss", "ocr", "voice ai", "pandas", "numpy"
    ]
    lower_resume = (resume_text or "").lower()
    lower_jd = (job_description or "").lower()
    matching = [skill for skill in skill_catalog if skill in lower_resume and (not lower_jd or skill in lower_jd)]
    if not matching:
        matching = common[:8]

    missing = [skill for skill in skill_catalog if skill in lower_jd and skill not in lower_resume]
    if not missing and job_description.strip():
        jd_words = sorted(jd_tokens - resume_tokens - stop, key=len, reverse=True)
        missing = jd_words[:6]

    if not missing:
        missing = [
            "Target-role fundamentals",
            "Interview communication",
            "Practical project depth"
        ]

    score = min(95, max(35, 45 + len(matching) * 6 - len(missing) * 2))
    technical = min(100, 40 + len(matching) * 7)
    experience = 70 if any(word in lower_resume for word in ["intern", "experience", "worked", "developer"]) else 45
    projects = 75 if "project" in lower_resume or "github" in lower_resume else 45
    education = 75 if any(word in lower_resume for word in ["b.tech", "b.e.", "degree", "engineering", "university", "college"]) else 50

    return {
        "match_score": score,
        "matching_skills": matching[:12],
        "missing_skills": missing[:10],
        "strengths": [
            f"Resume shows evidence relevant to {target_role or 'the target career'}.",
            "Technical and project information can be used for interview preparation.",
            "The profile is ready for a focused learning plan."
        ],
        "improvements": [
            "Add measurable outcomes to important projects and experience.",
            "Strengthen the missing skills listed above with practical projects.",
            "Practice explaining technical decisions clearly in interviews."
        ],
        "summary": "Local fallback analysis is being used because the AI service is currently unavailable. This analysis is based on text matching and is not an official ATS score.",
        "radar": {
            "technical": technical,
            "communication": 55,
            "experience": experience,
            "projects": projects,
            "education": education
        },
        "source": "local_fallback"
    }


def local_first_question(target_role, resume_text):
    role = (target_role or "software").lower()
    if "data" in role:
        return "Tell me about a data analysis or machine learning project you have worked on. What problem did you solve and how did you evaluate the result?"
    if "web" in role or "software" in role or "developer" in role:
        return "Walk me through a project where you built or improved a software application. What technical decision are you most confident about?"
    if "civil" in role:
        return "Describe a civil engineering project or design task you have worked on and explain one important engineering decision you made."
    if "mechanical" in role:
        return "Describe a mechanical engineering project or design task you have worked on and explain one engineering trade-off you considered."
    if "ai" in role or "ml" in role or "machine" in role:
        return "Choose one AI or machine learning project from your resume. What problem did it solve, and why did you choose the approach you used?"
    return "Tell me about the project or experience on your resume that best prepares you for this role. What did you personally contribute?"


def local_answer_evaluation(question, answer, target_role):
    words = re.findall(r"\b\w+\b", answer or "")
    length = len(words)
    technical_terms = [
        "because", "approach", "model", "data", "testing", "api", "python", "sql", "algorithm",
        "result", "impact", "problem", "solution", "trade-off", "deployment", "project"
    ]
    lower = (answer or "").lower()
    tech_hits = sum(1 for term in technical_terms if term in lower)
    score = min(95, max(25, 35 + min(length, 100) // 3 + tech_hits * 4))
    communication = min(95, max(30, 45 + min(length, 120) // 4))
    technical = min(95, max(25, 35 + tech_hits * 8))
    relevance = min(95, max(30, 45 + (10 if any(x in lower for x in ["project", "experience", "role"]) else 0)))
    followup = "What was the biggest challenge you faced, and how did you decide on the final solution?"
    return {
        "score": score,
        "technical_score": technical,
        "communication_score": communication,
        "relevance_score": relevance,
        "strengths": [
            "The answer provides a starting point for interview evaluation.",
            "The response can be improved further by adding concrete evidence and results."
        ],
        "improvements": [
            "Use a clear situation, action and result structure.",
            "Mention specific technical decisions and measurable outcomes where possible."
        ],
        "feedback": "Local fallback evaluation is being used because Gemini is unavailable. Focus on concrete examples, your personal contribution and measurable results.",
        "follow_up_question": followup,
        "source": "local_fallback"
    }


def local_final_report(first_feedback, followup_answer):
    base = first_feedback or {}
    answer_words = len(re.findall(r"\b\w+\b", followup_answer or ""))
    overall = min(95, max(35, int(base.get("score", 50)) + min(15, answer_words // 8)))
    technical = min(95, max(30, int(base.get("technical_score", 50))))
    communication = min(95, max(30, int(base.get("communication_score", 50)) + min(10, answer_words // 12)))
    confidence = min(95, max(30, 45 + min(30, answer_words // 4)))
    return {
        "overall_score": overall,
        "technical_score": technical,
        "communication_score": communication,
        "confidence_score": confidence,
        "key_strengths": base.get("strengths", [])[:3],
        "key_gaps": base.get("improvements", [])[:3],
        "final_feedback": "Local fallback report generated because Gemini is currently unavailable. Use the feedback as practice guidance rather than a definitive assessment.",
        "next_steps": [
            "Practice one target-role question every day.",
            "Strengthen the skill gaps identified by Resume Intelligence.",
            "Repeat the interview and compare your score and answer quality."
        ],
        "source": "local_fallback"
    }


def reset_interview_state():
    st.session_state.interview_started = False
    st.session_state.interview_question = ""
    st.session_state.interview_answer = ""
    st.session_state.interview_transcript = ""
    st.session_state.interview_feedback = {}
    st.session_state.followup_question = ""
    st.session_state.final_interview_report = {}
    st.session_state.current_interview_id = None

def render_card(title, description, icon="✨"):
    st.html(f"""
    <div class="app-card">
        <div class="card-title">
            {icon} {title}
        </div>

        <div class="card-text">
            {description}
        </div>
    </div>
    """)


def render_metric(title, value, subtitle):
    st.html(f"""
    <div class="metric">

        <div class="metric-title">
            {title}
        </div>

        <div class="metric-value">
            {value}
        </div>

        <div class="metric-subtitle">
            {subtitle}
        </div>

    </div>
    """)


def render_info(title, text, icon="💡"):
    st.html(f"""
    <div class="app-card">

        <div class="card-title">
            {icon} {title}
        </div>

        <div class="card-text">
            {text}
        </div>

    </div>
    """)


# =========================================================
# SIDEBAR
# =========================================================

with st.sidebar:

    st.html(f"""
    <div class="sidebar-brand">
        🪄 ALHOMORA
    </div>

    <div class="sidebar-subtitle">
        Welcome, {st.session_state.user_name or "Career Explorer"}
    </div>
    """)

    if st.button("Log out", key="sidebar_logout", use_container_width=True):
        st.session_state.authenticated = False
        st.session_state.user_id = None
        st.session_state.user_name = ""
        st.session_state.user_email = ""
        st.session_state.user_data_loaded = False
        st.session_state.auth_view = "login"
        reset_interview_state()
        st.session_state.active_game = ""
        st.session_state.game_questions = []
        st.rerun()

    pages = [
        "Dashboard",
        "Resume Intelligence",
        "AI Interview",
        "CareerQuest",
        "Learning Roadmap",
        "Progress"
    ]

    selected = st.radio(
        "Navigation",
        pages,
        index=pages.index(
            st.session_state.page
        ),
        label_visibility="collapsed"
    )

    if selected != st.session_state.page:
        st.session_state.page = selected
        st.rerun()

    st.html("""
    <div class="sidebar-divider"></div>
    """)

    st.html(f"""
    <div class="sidebar-streak">

        <div class="sidebar-streak-label">
            CAREER STREAK
        </div>

        <div class="sidebar-streak-value">
            🔥 {st.session_state.career_streak} Day
        </div>

        <div class="sidebar-streak-text">
            Keep building your career.
        </div>

    </div>
    """)


# =========================================================
# DASHBOARD
# =========================================================

if st.session_state.page == "Dashboard":

    st.html("""
    <div class="hero">

        <div class="hero-small">
            AI-POWERED CAREER DEVELOPMENT
        </div>

        <div class="hero-title">
            Welcome to ALHOMORA ✨
        </div>

        <div class="hero-text">
            Analyze your career profile, practice adaptive interviews,
            discover skill gaps and build your path toward your target career.
        </div>

    </div>
    """)

    st.html("""
    <div class="section-title">
        Your Career Snapshot
    </div>
    """)

    c1, c2, c3, c4 = st.columns(4)

    with c1:
        score = st.session_state.jd_analysis.get(
            "match_score",
            0
        )

        render_metric(
            "🎯 JD Match",
            f"{score}%",
            "Estimated career alignment"
        )

    with c2:
        render_metric(
            "📄 Resume",
            "Ready" if st.session_state.resume_text else "—",
            "Uploaded" if st.session_state.resume_text else "Not uploaded"
        )

    with c3:
        render_metric(
            "🎙 Interview",
            "Ready" if st.session_state.interview_question else "—",
            "Adaptive interview" if st.session_state.interview_question
            else "Not started"
        )

    with c4:
        render_metric(
            "🔥 Career Streak",
            str(st.session_state.career_streak),
            "Prototype streak"
        )

    st.html("""
    <div class="section-title">
        What do you want to do?
    </div>
    """)

    q1, q2, q3 = st.columns(3)

    with q1:
        render_card(
            "AI Interview",
            "Practice personalized interview questions using text or voice.",
            "🎙"
        )

        if st.button(
            "Start Interview →",
            key="dash_interview",
            use_container_width=True
        ):
            go_to("AI Interview")

    with q2:
        render_card(
            "Resume Intelligence",
            "Analyze your resume, compare it with a job description and discover skill gaps.",
            "📄"
        )

        if st.button(
            "Analyze Resume →",
            key="dash_resume",
            use_container_width=True
        ):
            go_to("Resume Intelligence")

    with q3:
        render_card(
            "CareerQuest",
            "Complete career missions, build streaks and improve job-ready skills.",
            "🎮"
        )

        if st.button(
            "Open CareerQuest →",
            key="dash_quest",
            use_container_width=True
        ):
            go_to("CareerQuest")

    st.html("""
    <div class="section-title">
        🎯 Today's Mission
    </div>
    """)

    render_info(
        "Complete one interview challenge",
        "Practice answering one technical question related to your target career.",
        "🎯"
    )


# =========================================================
# RESUME INTELLIGENCE
# =========================================================

elif st.session_state.page == "Resume Intelligence":

    st.html("""
    <div class="hero">

        <div class="hero-small">
            RESUME INTELLIGENCE
        </div>

        <div class="hero-title">
            Turn your resume into career intelligence 📄
        </div>

        <div class="hero-text">
            Upload your resume, select your target role and optionally
            provide a job description. AI will identify matching skills,
            skill gaps and improvement areas.
        </div>

    </div>
    """)

    uploaded_file = st.file_uploader(
        "Upload your resume",
        type=["pdf"]
    )

    target_role = st.text_input(
        "Target Job Role",
        value=st.session_state.target_role,
        placeholder="Example: Machine Learning Engineer"
    )

    job_description = st.text_area(
        "Job Description (Optional)",
        value=st.session_state.job_description,
        height=170,
        placeholder="Paste the job description here..."
    )

    if st.button(
        "Analyze Resume 🚀",
        type="primary",
        use_container_width=True
    ):

        if uploaded_file is None:

            st.warning(
                "Please upload your resume first."
            )

        elif not target_role.strip():

            st.warning(
                "Please enter your target job role."
            )

        else:

            try:

                pdf = pymupdf.open(
                    stream=uploaded_file.read(),
                    filetype="pdf"
                )

                resume_text = ""

                for page in pdf:
                    resume_text += page.get_text()

                pdf.close()

                st.session_state.resume_text = resume_text
                st.session_state.resume_name = uploaded_file.name
                st.session_state.target_role = target_role
                st.session_state.job_description = job_description

                st.session_state.resume_id = save_resume(
                    st.session_state.user_id,
                    uploaded_file.name,
                    resume_text
                )
                save_career_profile(
                    st.session_state.user_id,
                    target_role,
                    job_description
                )

                prompt = f"""
You are an AI career coach.

Analyze this resume for the target job.

TARGET ROLE:
{target_role}

JOB DESCRIPTION:
{job_description if job_description.strip() else "Not provided"}

RESUME:
{resume_text[:18000]}

Return ONLY valid JSON:

{{
    "match_score": 0,
    "matching_skills": [],
    "missing_skills": [],
    "strengths": [],
    "improvements": [],
    "summary": "",
    "radar": {{
        "technical": 0,
        "communication": 0,
        "experience": 0,
        "projects": 0,
        "education": 0
    }}
}}

Important:
- match_score is only an AI-estimated alignment score.
- It is not an official ATS score.
- Do not invent resume information.
- Scores must be 0 to 100.
"""

                ai_output = call_gemini(prompt)

                if ai_output:
                    try:
                        analysis = clean_json(ai_output)
                    except Exception:
                        analysis = local_resume_analysis(
                            resume_text, target_role, job_description
                        )
                        st.info("Gemini returned an invalid response, so local analysis was used instead.")
                else:
                    analysis = local_resume_analysis(
                        resume_text, target_role, job_description
                    )
                    st.info("Local analysis mode is active. Your resume was saved and can still be used for the roadmap and interview tools.")

                st.session_state.jd_analysis = analysis
                save_resume_analysis(
                    st.session_state.user_id,
                    st.session_state.get("resume_id"),
                    st.session_state.target_role,
                    st.session_state.job_description,
                    analysis
                )

                st.success(
                    "Resume analysis completed."
                )

            except Exception as e:
                ai_error(e)

    if st.session_state.jd_analysis:

        analysis = st.session_state.jd_analysis

        st.html("""
        <div class="section-title">
            Resume Analysis
        </div>
        """)

        score = analysis.get(
            "match_score",
            0
        )

        a, b, c = st.columns(3)

        with a:
            render_metric(
                "Estimated JD Match",
                f"{score}%",
                "AI-estimated alignment"
            )

        with b:
            render_metric(
                "Matching Skills",
                len(
                    analysis.get(
                        "matching_skills",
                        []
                    )
                ),
                "Detected"
            )

        with c:
            render_metric(
                "Skill Gaps",
                len(
                    analysis.get(
                        "missing_skills",
                        []
                    )
                ),
                "Areas to improve"
            )

        st.html("""
        <div class="section-title">
            Resume Radar Review
        </div>
        """)

        radar = analysis.get(
            "radar",
            {}
        )

        categories = [
            "Technical",
            "Communication",
            "Experience",
            "Projects",
            "Education"
        ]

        values = [
            radar.get("technical", 0),
            radar.get("communication", 0),
            radar.get("experience", 0),
            radar.get("projects", 0),
            radar.get("education", 0)
        ]

        fig = go.Figure()

        fig.add_trace(
            go.Scatterpolar(
                r=values,
                theta=categories,
                fill="toself",
                name="Resume"
            )
        )

        fig.update_layout(
            polar=dict(
                bgcolor="#111827",
                radialaxis=dict(
                    visible=True,
                    range=[0, 100],
                    gridcolor="#334155",
                    color="#94A3B8"
                ),
                angularaxis=dict(
                    gridcolor="#334155",
                    color="#94A3B8"
                )
            ),
            paper_bgcolor="#0B1120",
            plot_bgcolor="#0B1120",
            font=dict(
                color="#CBD5E1"
            ),
            showlegend=False,
            height=450
        )

        st.plotly_chart(
            fig,
            use_container_width=True
        )

        left, right = st.columns(2)

        with left:

            st.html("""
            <div class="section-title">
                Matching Skills
            </div>
            """)

            for skill in analysis.get(
                "matching_skills",
                []
            ):
                st.success(
                    f"✓ {skill}"
                )

        with right:

            st.html("""
            <div class="section-title">
                Missing / Unverified Skills
            </div>
            """)

            for skill in analysis.get(
                "missing_skills",
                []
            ):
                st.warning(
                    f"• {skill}"
                )

        st.html("""
        <div class="section-title">
            Strengths
        </div>
        """)

        for item in analysis.get(
            "strengths",
            []
        ):
            st.write(
                f"✅ {item}"
            )

        st.html("""
        <div class="section-title">
            Recommended Improvements
        </div>
        """)

        for item in analysis.get(
            "improvements",
            []
        ):
            st.write(
                f"💡 {item}"
            )

        render_info(
            "AI Summary",
            analysis.get(
                "summary",
                "No summary available."
            ),
            "🧠"
        )


# =========================================================
# AI INTERVIEW
# =========================================================

elif st.session_state.page == "AI Interview":

    st.html("""
    <div class="hero">

        <div class="hero-small">
            ADAPTIVE AI INTERVIEW
        </div>

        <div class="hero-title">
            Practice interviews your way 🎙
        </div>

        <div class="hero-text">
            Choose how you want to answer. Use text for traditional
            interview practice or voice for a more realistic speaking
            experience.
        </div>

    </div>
    """)

    # =====================================================
    # MODE SELECTION IS ALWAYS VISIBLE
    # =====================================================

    st.html("""
    <div class="section-title">
        Choose Interview Mode
    </div>

    <div class="mode-card">

        <div class="mode-title">
            How do you want to answer?
        </div>

        <div class="mode-description">
            You can practice using either written answers or your voice.
        </div>

    </div>
    """)

    mode = st.radio(
        "Interview mode",
        [
            "⌨️ Text Interview",
            "🎤 Voice Interview"
        ],
        horizontal=True,
        index=(
            1
            if st.session_state.interview_mode == "Voice"
            else 0
        ),
        label_visibility="collapsed"
    )

    if "Voice" in mode:
        st.session_state.interview_mode = "Voice"
    else:
        st.session_state.interview_mode = "Text"

    # =====================================================
    # RESUME CHECK
    # =====================================================

    if not st.session_state.resume_text:

        render_info(
            "Resume required before starting",
            "Your selected interview mode is ready. "
            "Now upload and analyze your resume so the AI can "
            "create personalized questions based on your skills "
            "and target career.",
            "📄"
        )

        if st.button(
            "Go to Resume Intelligence →",
            type="primary"
        ):
            go_to("Resume Intelligence")

    else:

        selected_role = st.session_state.target_role

        st.html(f"""
        <div class="app-card">

            <div class="card-title">
                🎯 Target Career
            </div>

            <div class="card-text">
                {selected_role}
            </div>

        </div>
        """)

        # =================================================
        # START
        # =================================================

        if not st.session_state.interview_started:

            if st.session_state.interview_mode == "Voice":

                render_info(
                    "Voice Interview Mode",
                    "The AI will ask personalized questions. "
                    "You answer using your microphone. Gemini "
                    "will analyze the recorded response.",
                    "🎤"
                )

            else:

                render_info(
                    "Text Interview Mode",
                    "The AI will ask personalized questions. "
                    "You type your response and receive adaptive "
                    "feedback.",
                    "⌨️"
                )

            if st.button(
                "🚀 Start Adaptive Interview",
                type="primary",
                use_container_width=True
            ):

                try:

                    prompt = f"""
You are conducting a professional mock interview.

Target role:
{selected_role}

Candidate resume:
{st.session_state.resume_text[:12000]}

Create the first interview question.

Requirements:
- Relevant to the target role.
- Relevant to the candidate's resume.
- Suitable for a student or job seeker.
- Ask only one question.
- Return ONLY the question.
"""

                    ai_output = call_gemini(prompt)
                    if ai_output:
                        question = ai_output.strip()
                    else:
                        question = local_first_question(
                            selected_role, st.session_state.resume_text
                        )
                        st.info("Gemini is unavailable, so a local interview question was loaded.")

                    st.session_state.interview_question = question
                    st.session_state.interview_started = True
                    st.session_state.current_interview_id = start_interview_record(
                        st.session_state.user_id,
                        st.session_state.interview_mode,
                        selected_role
                    )
                    st.rerun()

                except Exception as e:
                    ai_error(e)

        # =================================================
        # QUESTION
        # =================================================

        if st.session_state.interview_question:

            st.html(f"""
            <div class="question-box">

                <div class="question-label">
                    AI INTERVIEWER
                </div>

                <div class="question-text">
                    {st.session_state.interview_question}
                </div>

            </div>
            """)

            # =================================================
            # TEXT ANSWER
            # =================================================

            if st.session_state.interview_mode == "Text":

                st.html("""
                <div class="section-title">
                    ⌨️ Your Answer
                </div>
                """)

                answer = st.text_area(
                    "Type your answer",
                    height=180,
                    placeholder="Write your answer here..."
                )

                if st.button(
                    "Analyze Answer →",
                    type="primary",
                    use_container_width=True
                ):

                    if not answer.strip():

                        st.warning(
                            "Please enter an answer first."
                        )

                    else:

                        try:

                            prompt = f"""
You are an expert AI interview evaluator.

Target role:
{selected_role}

Question:
{st.session_state.interview_question}

Candidate answer:
{answer}

Candidate resume:
{st.session_state.resume_text[:10000]}

Evaluate the answer.

Return ONLY valid JSON:

{{
    "score": 0,
    "technical_score": 0,
    "communication_score": 0,
    "relevance_score": 0,
    "strengths": [],
    "improvements": [],
    "feedback": "",
    "follow_up_question": ""
}}

All scores must be between 0 and 100.

The follow-up question must respond
to the candidate's actual answer.
"""

                            ai_output = call_gemini(prompt)
                            if ai_output:
                                try:
                                    evaluation = clean_json(ai_output)
                                except Exception:
                                    evaluation = local_answer_evaluation(
                                        st.session_state.interview_question,
                                        answer,
                                        selected_role
                                    )
                            else:
                                evaluation = local_answer_evaluation(
                                    st.session_state.interview_question,
                                    answer,
                                    selected_role
                                )
                                st.info("Local interview evaluation is being used while Gemini is unavailable.")

                            st.session_state.interview_answer = answer
                            st.session_state.interview_feedback = evaluation
                            save_interview_answer(
                                st.session_state.current_interview_id,
                                st.session_state.interview_question,
                                answer,
                                "",
                                evaluation
                            )

                            st.session_state.followup_question = (
                                evaluation.get(
                                    "follow_up_question",
                                    ""
                                )
                            )

                            st.rerun()

                        except Exception as e:
                            ai_error(e)

            # =================================================
            # VOICE ANSWER
            # =================================================

            else:

                st.html("""
                <div class="voice-box">

                    <div style="
                        font-size:38px;
                        margin-bottom:10px;
                    ">
                        🎤
                    </div>

                    <div class="voice-title">
                        Voice Answer
                    </div>

                    <div class="voice-text">
                        Click the microphone button below,
                        record your answer and submit it for
                        AI analysis.
                    </div>

                </div>
                """)

                audio_answer = st.audio_input(
                    "🎤 Record your answer",
                    sample_rate=16000
                )

                if audio_answer:

                    st.audio(
                        audio_answer,
                        format="audio/wav"
                    )

                    if st.button(
                        "🧠 Analyze Voice Answer",
                        type="primary",
                        use_container_width=True
                    ):

                        try:

                            audio_bytes = (
                                audio_answer.getvalue()
                            )

                            encoded_audio = base64.b64encode(
                                audio_bytes
                            ).decode("utf-8")

                            prompt = f"""
You are an expert AI interview evaluator.

Analyze this candidate's recorded voice answer.

Target role:
{selected_role}

Interview question:
{st.session_state.interview_question}

Candidate resume:
{st.session_state.resume_text[:10000]}

Tasks:
1. Transcribe the speech.
2. Evaluate technical quality.
3. Evaluate communication.
4. Evaluate relevance.
5. Identify strengths.
6. Identify improvements.
7. Generate an adaptive follow-up question.

Return ONLY valid JSON:

{{
    "transcript": "",
    "score": 0,
    "technical_score": 0,
    "communication_score": 0,
    "relevance_score": 0,
    "strengths": [],
    "improvements": [],
    "feedback": "",
    "follow_up_question": ""
}}

All scores must be between 0 and 100.
"""

                            if client is None:
                                st.warning("Voice AI requires Gemini access. Your microphone recording is available, but AI transcription/evaluation is currently unavailable.")
                                evaluation = {}
                            else:
                                try:
                                    response = client.interactions.create(
                                        model=MODEL,
                                        input=[
                                            {"type": "text", "text": prompt},
                                            {"type": "audio", "data": encoded_audio, "mime_type": "audio/wav"}
                                        ]
                                    )
                                    evaluation = clean_json(response.output_text)
                                except Exception as exc:
                                    ai_error(exc)
                                    evaluation = {}

                            st.session_state.interview_feedback = evaluation

                            st.session_state.interview_transcript = (
                                evaluation.get(
                                    "transcript",
                                    ""
                                )
                            )

                            st.session_state.followup_question = (
                                evaluation.get(
                                    "follow_up_question",
                                    ""
                                )
                            )
                            save_interview_answer(
                                st.session_state.current_interview_id,
                                st.session_state.interview_question,
                                "",
                                st.session_state.interview_transcript,
                                evaluation
                            )

                            st.rerun()

                        except Exception as e:
                            ai_error(e)

            # =================================================
            # FEEDBACK
            # =================================================

            feedback = st.session_state.interview_feedback

            if feedback:

                st.html("""
                <div class="section-title">
                    🧠 AI Evaluation
                </div>
                """)

                s1, s2, s3, s4 = st.columns(4)

                with s1:
                    render_metric(
                        "Overall",
                        f"{feedback.get('score', 0)}%",
                        "Answer quality"
                    )

                with s2:
                    render_metric(
                        "Technical",
                        f"{feedback.get('technical_score', 0)}%",
                        "Technical depth"
                    )

                with s3:
                    render_metric(
                        "Communication",
                        f"{feedback.get('communication_score', 0)}%",
                        "Clarity"
                    )

                with s4:
                    render_metric(
                        "Relevance",
                        f"{feedback.get('relevance_score', 0)}%",
                        "Question relevance"
                    )

                if st.session_state.interview_transcript:

                    st.html("""
                    <div class="section-title">
                        Voice Transcript
                    </div>
                    """)

                    render_info(
                        "Transcription",
                        st.session_state.interview_transcript,
                        "📝"
                    )

                st.html("""
                <div class="section-title">
                    Strengths
                </div>
                """)

                for item in feedback.get(
                    "strengths",
                    []
                ):
                    st.success(
                        f"✓ {item}"
                    )

                st.html("""
                <div class="section-title">
                    Improvements
                </div>
                """)

                for item in feedback.get(
                    "improvements",
                    []
                ):
                    st.warning(
                        f"• {item}"
                    )

                render_info(
                    "AI Feedback",
                    feedback.get(
                        "feedback",
                        "No additional feedback."
                    ),
                    "🧠"
                )

            # =================================================
            # FOLLOW-UP
            # =================================================

            if st.session_state.followup_question:

                st.html("""
                <div class="section-title">
                    Adaptive Follow-up
                </div>
                """)

                st.html(f"""
                <div class="question-box">

                    <div class="question-label">
                        AI FOLLOW-UP
                    </div>

                    <div class="question-text">
                        {st.session_state.followup_question}
                    </div>

                </div>
                """)

                # -------------------------------------------------
                # TEXT FOLLOW-UP
                # -------------------------------------------------

                if st.session_state.interview_mode == "Text":

                    followup = st.text_area(
                        "Your follow-up answer",
                        height=160,
                        placeholder="Answer the follow-up question..."
                    )

                    if st.button(
                        "Finish Interview & Generate Report",
                        type="primary",
                        use_container_width=True
                    ):

                        if not followup.strip():

                            st.warning(
                                "Please answer the follow-up question."
                            )

                        else:

                            try:

                                prompt = f"""
You are an expert interview coach.

Target role:
{selected_role}

Original question:
{st.session_state.interview_question}

Original answer:
{st.session_state.interview_answer}

Follow-up question:
{st.session_state.followup_question}

Follow-up answer:
{followup}

Generate a final interview report.

Return ONLY valid JSON:

{{
    "overall_score": 0,
    "technical_score": 0,
    "communication_score": 0,
    "confidence_score": 0,
    "key_strengths": [],
    "key_gaps": [],
    "final_feedback": "",
    "next_steps": []
}}
"""

                                ai_output = call_gemini(prompt)
                                if ai_output:
                                    try:
                                        report = clean_json(ai_output)
                                    except Exception:
                                        report = local_final_report(
                                            st.session_state.interview_feedback,
                                            followup
                                        )
                                else:
                                    report = local_final_report(
                                        st.session_state.interview_feedback,
                                        followup
                                    )
                                    st.info("Local final interview report is being used while Gemini is unavailable.")

                                st.session_state.final_interview_report = report
                                complete_interview_record(
                                    st.session_state.current_interview_id,
                                    report
                                )
                                save_career_progress(
                                    st.session_state.user_id,
                                    st.session_state.career_streak,
                                    st.session_state.career_xp,
                                    st.session_state.mission_completed
                                )

                                st.rerun()

                            except Exception as e:
                                ai_error(e)

                # -------------------------------------------------
                # VOICE FOLLOW-UP
                # -------------------------------------------------

                else:

                    followup_audio = st.audio_input(
                        "🎤 Record follow-up answer",
                        sample_rate=16000
                    )

                    if followup_audio:

                        st.audio(
                            followup_audio,
                            format="audio/wav"
                        )

                        if st.button(
                            "🎤 Finish Voice Interview",
                            type="primary",
                            use_container_width=True
                        ):

                            try:

                                audio_bytes = (
                                    followup_audio.getvalue()
                                )

                                encoded_audio = base64.b64encode(
                                    audio_bytes
                                ).decode("utf-8")

                                prompt = f"""
You are an expert interview coach.

Analyze the candidate's final voice response.

Target role:
{selected_role}

Original question:
{st.session_state.interview_question}

Previous evaluation:
{json.dumps(
    st.session_state.interview_feedback
)}

Follow-up question:
{st.session_state.followup_question}

Generate the final interview report.

Return ONLY valid JSON:

{{
    "transcript": "",
    "overall_score": 0,
    "technical_score": 0,
    "communication_score": 0,
    "confidence_score": 0,
    "key_strengths": [],
    "key_gaps": [],
    "final_feedback": "",
    "next_steps": []
}}

All scores must be between 0 and 100.
"""

                                if client is None:
                                    st.warning("Voice final report requires Gemini access. Finish the interview in Text mode for the local fallback report.")
                                    report = {}
                                else:
                                    try:
                                        response = client.interactions.create(
                                            model=MODEL,
                                            input=[
                                                {"type": "text", "text": prompt},
                                                {"type": "audio", "data": encoded_audio, "mime_type": "audio/wav"}
                                            ]
                                        )
                                        report = clean_json(response.output_text)
                                    except Exception as exc:
                                        ai_error(exc)
                                        report = {}

                                if not report:
                                    st.stop()

                                st.session_state.final_interview_report = report
                                complete_interview_record(
                                    st.session_state.current_interview_id,
                                    report
                                )
                                save_career_progress(
                                    st.session_state.user_id,
                                    st.session_state.career_streak,
                                    st.session_state.career_xp,
                                    st.session_state.mission_completed
                                )

                                st.rerun()

                            except Exception as e:
                                ai_error(e)

            # =================================================
            # FINAL REPORT
            # =================================================

            report = st.session_state.final_interview_report

            if report:

                st.html("""
                <div class="section-title">
                    🏆 Final Interview Report
                </div>
                """)

                r1, r2, r3, r4 = st.columns(4)

                with r1:
                    render_metric(
                        "Overall",
                        f"{report.get('overall_score', 0)}%",
                        "Interview performance"
                    )

                with r2:
                    render_metric(
                        "Technical",
                        f"{report.get('technical_score', 0)}%",
                        "Technical knowledge"
                    )

                with r3:
                    render_metric(
                        "Communication",
                        f"{report.get('communication_score', 0)}%",
                        "Communication"
                    )

                with r4:
                    render_metric(
                        "Confidence",
                        f"{report.get('confidence_score', 0)}%",
                        "Response confidence"
                    )

                st.html("""
                <div class="section-title">
                    Key Strengths
                </div>
                """)

                for item in report.get(
                    "key_strengths",
                    []
                ):
                    st.success(
                        f"✓ {item}"
                    )

                st.html("""
                <div class="section-title">
                    Key Gaps
                </div>
                """)

                for item in report.get(
                    "key_gaps",
                    []
                ):
                    st.warning(
                        f"• {item}"
                    )

                render_info(
                    "Final AI Feedback",
                    report.get(
                        "final_feedback",
                        "No final feedback available."
                    ),
                    "🧠"
                )

                st.html("""
                <div class="section-title">
                    Next Steps
                </div>
                """)

                for item in report.get(
                    "next_steps",
                    []
                ):
                    st.write(
                        f"🎯 {item}"
                    )

                if st.button(
                    "🔄 Start New Interview",
                    use_container_width=True
                ):

                    reset_interview_state()
                    st.rerun()


# =========================================================
# CAREERQUEST
# =========================================================

elif st.session_state.page == "CareerQuest":

    st.html("""
    <div class="hero">
        <div class="hero-small">CAREERQUEST</div>
        <div class="hero-title">Turn career preparation into a game 🎮</div>
        <div class="hero-text">
            Complete meaningful career missions, solve short skill challenges,
            earn XP and unlock useful career rewards.
        </div>
    </div>
    """)

    a, b, c, d = st.columns(4)
    with a:
        render_metric("🔥 Career Streak", st.session_state.career_streak, "Consecutive mission days")
    with b:
        render_metric("⭐ Career XP", st.session_state.career_xp, "Experience points")
    with c:
        render_metric("🎮 Games", st.session_state.games_completed, "Completed challenges")
    with d:
        render_metric("🎁 Next Reward", f"{200 if st.session_state.career_xp < 200 else 400} XP", "Career reward")

    st.html('<div class="section-title">🎯 Today\'s Mission</div>')
    render_card(
        "Complete one mock interview",
        "Start an interview, answer at least one question and receive feedback. This mission rewards actual preparation, not screen time.",
        "🎙"
    )

    if not st.session_state.mission_completed:
        if st.button("Complete Mission +50 XP", type="primary", key="complete_daily_mission", use_container_width=True):
            completed, streak, xp = complete_daily_mission(st.session_state.user_id)
            if completed:
                st.session_state.career_streak = streak
                st.session_state.career_xp = xp
                st.session_state.mission_completed = True
                st.success("Mission completed! +50 XP 🎉")
                st.rerun()
            else:
                st.info("Today's mission has already been completed.")
    else:
        st.success("Today's mission completed! Come back tomorrow for the next streak day. 🔥")

    st.html('<div class="section-title">🧩 Career Mini Games</div>')

    x, y, z = st.columns(3)
    with x:
        render_card("Debug It", "Find the issue in a short Python/code challenge.", "🐛")
        if st.button("Play Debug It →", key="play_debug", use_container_width=True):
            st.session_state.active_game = "Debug It"
            st.session_state.game_question_index = 0
            st.session_state.game_score = 0
            st.session_state.game_finished = False
            st.session_state.game_questions = []
            st.rerun()
    with y:
        render_card("Logic Sprint", "Solve quick reasoning problems without using Gemini.", "🧠")
        if st.button("Play Logic Sprint →", key="play_logic", use_container_width=True):
            st.session_state.active_game = "Logic Sprint"
            st.session_state.game_question_index = 0
            st.session_state.game_score = 0
            st.session_state.game_finished = False
            st.session_state.game_questions = []
            st.rerun()
    with z:
        render_card("Career Quiz", "Test knowledge connected to your target career.", "🎯")
        if st.button("Play Career Quiz →", key="play_quiz", use_container_width=True):
            st.session_state.active_game = "Career Quiz"
            st.session_state.game_question_index = 0
            st.session_state.game_score = 0
            st.session_state.game_finished = False
            st.session_state.game_questions = []
            st.rerun()

    debug_questions = [
        {
            "q": "What is wrong with this Python line?  print('Hello'",
            "options": ["Missing closing parenthesis", "Missing import", "Invalid print function", "Nothing is wrong"],
            "answer": 0,
            "why": "The print call needs a closing parenthesis."
        },
        {
            "q": "Which keyword is used to define a function in Python?",
            "options": ["func", "def", "function", "define"],
            "answer": 1,
            "why": "Python uses the def keyword to define functions."
        },
        {
            "q": "Which data structure stores key-value pairs in Python?",
            "options": ["List", "Tuple", "Dictionary", "Set"],
            "answer": 2,
            "why": "A dictionary stores values against keys."
        },
        {
            "q": "What is the likely problem with: x = 10 / 0 ?",
            "options": ["SyntaxError", "TypeError", "ZeroDivisionError", "NameError"],
            "answer": 2,
            "why": "Division by zero raises ZeroDivisionError."
        },
        {
            "q": "Which command installs a Python package from PyPI?",
            "options": ["python add", "pip install", "package get", "py installpkg"],
            "answer": 1,
            "why": "pip install is the standard package installation command."
        }
    ]

    logic_questions = [
        {"q": "A sequence is 2, 4, 8, 16. What comes next?", "options": ["20", "24", "32", "36"], "answer": 2, "why": "Each number is multiplied by 2."},
        {"q": "If all ML engineers are programmers and some programmers are designers, which statement is definitely true?", "options": ["All designers are ML engineers", "All ML engineers are programmers", "No programmer is a designer", "All programmers are ML engineers"], "answer": 1, "why": "That is the only statement directly guaranteed by the premise."},
        {"q": "You have 3 tasks taking 10, 20 and 30 minutes. Doing the longest task first does what to total time?", "options": ["Changes total time", "Halves total time", "Does not change total work time", "Doubles total time"], "answer": 2, "why": "The order changes scheduling, not the total amount of work."},
        {"q": "If a test fails only when input is empty, what should you inspect first?", "options": ["The empty-input validation path", "The monitor brightness", "The database password", "The CPU fan"], "answer": 0, "why": "The failure condition points directly to empty-input handling."},
        {"q": "A project has 4 bugs. You fix 1, then 2 more appear. How many remain?", "options": ["1", "2", "3", "5"], "answer": 2, "why": "4 - 1 + 2 = 5? Wait, this exposes why careful arithmetic matters: the answer is 5."}
    ]
    # Correct the intentionally simple final logic question explicitly.
    logic_questions[-1]["answer"] = 3

    career_questions = {
        "ai": [
            {"q": "Which technique retrieves relevant documents before an LLM generates an answer?", "options": ["RAG", "CSS", "DNS", "FTP"], "answer": 0, "why": "RAG combines retrieval with generation."},
            {"q": "What does an embedding represent?", "options": ["A vector representation of information", "A password", "A database backup", "A video codec"], "answer": 0, "why": "Embeddings represent text or other data as vectors."},
            {"q": "Which metric is commonly used for classification?", "options": ["Accuracy", "Kilometers", "Voltage", "Frame rate only"], "answer": 0, "why": "Accuracy is a standard classification metric."},
            {"q": "What is prompt engineering?", "options": ["Designing effective instructions for an AI model", "Building a CPU", "Encrypting a hard drive", "Installing Windows"], "answer": 0, "why": "Prompt engineering focuses on structuring model instructions."},
            {"q": "What is overfitting?", "options": ["A model learns training data too specifically", "A server loses power", "A dataset has no rows", "A model has no parameters"], "answer": 0, "why": "Overfitting harms generalization to unseen data."}
        ],
        "software": [
            {"q": "Which tool is commonly used to track source-code changes?", "options": ["Git", "Excel", "PowerPoint", "Bluetooth"], "answer": 0, "why": "Git is a version control system."},
            {"q": "What does an API provide?", "options": ["A way for software components to communicate", "Only a user interface", "A laptop battery", "A graphics card"], "answer": 0, "why": "APIs define ways for software to interact."},
            {"q": "What does HTTP status 404 usually mean?", "options": ["Not Found", "Success", "Unauthorized only", "Server started"], "answer": 0, "why": "404 indicates that the requested resource was not found."},
            {"q": "Which structure follows LIFO?", "options": ["Stack", "Queue", "Graph", "Database"], "answer": 0, "why": "A stack follows last-in, first-out."},
            {"q": "What is a unit test?", "options": ["A test of a small unit of code", "A network cable", "A deployment server", "A database table"], "answer": 0, "why": "Unit tests verify small pieces of program behavior."}
        ],
        "data": [
            {"q": "What is a primary purpose of data visualization?", "options": ["Communicate patterns and insights", "Delete data", "Increase RAM", "Encrypt passwords"], "answer": 0, "why": "Visualization helps communicate patterns and findings."},
            {"q": "Which is a supervised learning task?", "options": ["Classification with labeled data", "Random guessing", "Disk formatting", "Password hashing"], "answer": 0, "why": "Supervised learning uses labeled examples."},
            {"q": "What does SQL primarily work with?", "options": ["Relational databases", "Audio microphones", "GPU drivers", "CSS layouts"], "answer": 0, "why": "SQL is used to query and manage relational data."},
            {"q": "Why split data into training and test sets?", "options": ["To evaluate generalization", "To make the file prettier", "To increase screen resolution", "To remove all features"], "answer": 0, "why": "The test set helps estimate performance on unseen data."},
            {"q": "What is a missing value?", "options": ["An absent data entry", "A GPU failure", "A web domain", "A Python package"], "answer": 0, "why": "Missing data represents an unavailable value."}
        ],
        "generic": [
            {"q": "What is the main purpose of a resume?", "options": ["Present relevant qualifications and experience", "Replace an interview", "Guarantee a job", "Store passwords"], "answer": 0, "why": "A resume communicates relevant qualifications to employers."},
            {"q": "Which behavior helps in a technical interview?", "options": ["Explain your reasoning", "Guess every answer", "Avoid clarifying questions", "Read unrelated notes"], "answer": 0, "why": "Explaining reasoning helps an interviewer understand your approach."},
            {"q": "What makes a project bullet stronger?", "options": ["Specific action and measurable result", "Only a project title", "No technical details", "Random adjectives"], "answer": 0, "why": "Specific actions and results make achievements clearer."},
            {"q": "What is a skill gap?", "options": ["A required skill you still need to develop", "A browser tab", "A password field", "A laptop port"], "answer": 0, "why": "A skill gap is a difference between current and required capability."},
            {"q": "What is a useful interview practice loop?", "options": ["Answer → feedback → improve → retry", "Answer once and stop", "Skip feedback", "Only memorize definitions"], "answer": 0, "why": "Iterative practice turns feedback into improvement."}
        ]
    }

    if st.session_state.active_game:
        st.html(f'<div class="section-title">🎮 {st.session_state.active_game}</div>')

        if not st.session_state.game_questions:
            advanced_interview_questions = [
                {"q": "You deployed an ML model and accuracy dropped in production. What should you investigate first?", "options": ["Data drift and production data quality", "Change the UI color", "Delete the model", "Increase monitor brightness"], "answer": 0, "why": "Production data drift or quality changes can cause a trained model to perform differently."},
                {"q": "An API becomes slow only under heavy traffic. Which approach is most useful first?", "options": ["Measure latency and inspect bottlenecks", "Rewrite everything immediately", "Remove error handling", "Disable logging permanently"], "answer": 0, "why": "Measurement identifies the actual bottleneck before you optimize."},
                {"q": "Your RAG system retrieves irrelevant documents. What should you examine?", "options": ["Chunking, embeddings and retrieval quality", "Only the website logo", "The keyboard layout", "The laptop wallpaper"], "answer": 0, "why": "Poor retrieval can come from chunking, embeddings, indexing or ranking choices."},
                {"q": "A Python service works locally but fails in deployment. What is a strong first check?", "options": ["Environment variables and dependency versions", "Replace the monitor", "Delete the source code", "Increase font size"], "answer": 0, "why": "Environment and dependency differences are common causes of deployment failures."},
                {"q": "In an interview, you do not know an answer. What is the strongest response?", "options": ["Explain what you know, reason through it, and state how you would verify the unknown", "Invent a confident answer", "Stay silent", "Change the topic"], "answer": 0, "why": "Good engineering communication shows reasoning, honesty and a verification strategy."},
            ]
            if st.session_state.active_game == "Debug It":
                st.session_state.game_questions = debug_questions
            elif st.session_state.active_game == "Logic Sprint":
                st.session_state.game_questions = logic_questions
            elif st.session_state.active_game == "Advanced Interview Challenge":
                st.session_state.game_questions = advanced_interview_questions
            else:
                role = (st.session_state.target_role or "").lower()
                if any(x in role for x in ["ai", "ml", "machine", "data"]):
                    st.session_state.game_questions = career_questions["ai"] if "ai" in role or "ml" in role or "machine" in role else career_questions["data"]
                elif any(x in role for x in ["software", "developer", "web", "backend", "frontend"]):
                    st.session_state.game_questions = career_questions["software"]
                else:
                    st.session_state.game_questions = career_questions["generic"]

        if not st.session_state.game_finished:
            idx = st.session_state.game_question_index
            questions = st.session_state.game_questions
            if idx < len(questions):
                q = questions[idx]
                st.html(f'<div class="question-box"><div class="question-label">QUESTION {idx + 1} / {len(questions)}</div><div class="question-text">{q["q"]}</div></div>')
                choice = st.radio("Choose an answer", q["options"], key=f"game_choice_{st.session_state.active_game}_{idx}")
                if st.button("Submit Answer →", type="primary", key=f"game_submit_{st.session_state.active_game}_{idx}", use_container_width=True):
                    selected_index = q["options"].index(choice)
                    if selected_index == q["answer"]:
                        st.session_state.game_score += 1
                        st.success(f"Correct! +10 XP. {q['why']}")
                    else:
                        st.warning(f"Not quite. {q['why']}")
                    st.session_state.game_question_index += 1
                    if st.session_state.game_question_index >= len(questions):
                        st.session_state.game_finished = True
                        xp_earned = st.session_state.game_score * 10
                        save_game_result(
                            st.session_state.user_id,
                            st.session_state.active_game,
                            st.session_state.game_score,
                            len(questions),
                            xp_earned,
                        )
                    st.rerun()
        else:
            score = st.session_state.game_score
            total = len(st.session_state.game_questions)
            xp_earned = score * 10
            st.success(f"Challenge complete! You scored {score}/{total} and earned {xp_earned} XP. 🏆")
            render_metric("Game Score", f"{score}/{total}", "Correct answers")
            if st.button("Play Again", key="game_again", use_container_width=True):
                st.session_state.game_question_index = 0
                st.session_state.game_score = 0
                st.session_state.game_finished = False
                st.rerun()
            if st.button("Close Game", key="game_close", use_container_width=True):
                st.session_state.active_game = ""
                st.session_state.game_questions = []
                st.session_state.game_question_index = 0
                st.session_state.game_score = 0
                st.session_state.game_finished = False
                st.rerun()

    st.html('<div class="section-title">🎁 Career Rewards</div>')
    # Always derive reward state from the persisted XP. This prevents a reward
    # from appearing unlocked in one session and locked after login.
    st.session_state.unlocked_rewards = reward_ids_for_xp(st.session_state.career_xp)

    reward_lookup = {reward_id: name for _, reward_id, name in REWARD_DEFINITIONS}
    unlocked_rewards = set(st.session_state.unlocked_rewards)

    c1, c2, c3 = st.columns(3)

    with c1:
        unlocked = "advanced_resume_template" in unlocked_rewards
        status = "Unlocked" if unlocked else "Requires 200 XP"
        render_card(
            "Advanced Resume Template",
            f"A clean, ATS-friendly career resume structure. {status}.",
            "📄"
        )
        if unlocked:
            if st.button("📄 Open Reward", key="open_resume_reward", use_container_width=True):
                st.session_state.active_reward = "advanced_resume_template"
                st.rerun()

    with c2:
        unlocked = "advanced_interview_challenge" in unlocked_rewards
        status = "Unlocked" if unlocked else "Requires 400 XP"
        render_card(
            "Advanced Interview Challenge",
            f"A harder local career challenge for engineering judgement. {status}.",
            "🎙"
        )
        if unlocked:
            if st.button("🎙 Open Reward", key="open_interview_reward", use_container_width=True):
                st.session_state.active_reward = "advanced_interview_challenge"
                st.rerun()

    with c3:
        unlocked = "career_mastery_badge" in unlocked_rewards
        status = "Unlocked" if unlocked else "Requires 600 XP"
        render_card(
            "Career Mastery Badge",
            f"A downloadable achievement badge for reaching 600 Career XP. {status}.",
            "🏆"
        )
        if unlocked:
            if st.button("🏆 Open Reward", key="open_badge_reward", use_container_width=True):
                st.session_state.active_reward = "career_mastery_badge"
                st.rerun()

    # Actual reward actions. These are deliberately outside the decorative
    # cards so every unlocked reward has an unmistakable working action.
    active_reward = st.session_state.get("active_reward", "")
    if active_reward and active_reward not in unlocked_rewards:
        st.session_state.active_reward = ""
        active_reward = ""

    if active_reward:
        st.html('<div class="section-title">🎁 Reward Unlocked</div>')

        if active_reward == "advanced_resume_template":
            st.success("🎉 Advanced Resume Template unlocked! You can preview it and download it below.")
            resume_template = (
                f"# {st.session_state.user_name or '[YOUR NAME]'}\n\n"
                f"**{st.session_state.target_role or '[TARGET ROLE]'}**\n\n"
                "Email: [your.email@example.com]  |  Phone: [Your Phone]  |  LinkedIn: [LinkedIn URL]  |  GitHub: [GitHub URL]\n\n"
                "## PROFESSIONAL SUMMARY\nWrite 2-3 lines describing your strongest skills, domain focus, and target role.\n\n"
                "## TECHNICAL SKILLS\n- Programming: [Python, Java, C/C++]\n- Frameworks: [Frameworks]\n- AI/ML: [Machine Learning, Deep Learning, LLMs, RAG]\n- Tools: [Git, Docker, Cloud]\n- Databases: [MySQL, PostgreSQL]\n\n"
                "## EXPERIENCE\n### [Job Title] | [Company]\n[Month Year] - [Month Year]\n- Built [what you built] using [technology].\n- Improved [metric/result] by [measurable amount].\n- Collaborated with [team/stakeholders] to deliver [result].\n\n"
                "## PROJECTS\n### [Project Name]\n- Built [project] using [technology].\n- Implemented [important technical feature].\n- Achieved [result/metric].\n\n"
                "## EDUCATION\n### [Degree] | [College]\n[Year]\n\n"
                "## CERTIFICATIONS / ACHIEVEMENTS\n- [Certification or achievement]\n\n"
                "## RESUME CHECKLIST\n- Use measurable results where possible.\n- Match important skills from the job description.\n- Keep bullets concise and action-oriented.\n- Put your strongest relevant projects first.\n- Proofread before submitting.\n"
            )
            st.download_button(
                "⬇️ Download Resume Template",
                data=resume_template,
                file_name="Alhomora_Advanced_Resume_Template.md",
                mime="text/markdown",
                key="download_resume_reward_v2",
                use_container_width=True,
            )
            with st.expander("Preview Template", expanded=True):
                st.markdown(resume_template)

        elif active_reward == "advanced_interview_challenge":
            st.success("🎉 Advanced Interview Challenge unlocked! Start it whenever you are ready.")
            if st.button("🎙 Start Advanced Challenge", key="start_advanced_reward_v2", type="primary", use_container_width=True):
                st.session_state.active_game = "Advanced Interview Challenge"
                st.session_state.active_reward = ""
                st.session_state.game_question_index = 0
                st.session_state.game_score = 0
                st.session_state.game_finished = False
                st.session_state.game_questions = []
                st.rerun()

        elif active_reward == "career_mastery_badge":
            st.success("🏆 Career Mastery Badge unlocked! Congratulations on reaching 600 Career XP.")
            badge_text = (
                "ALHOMORA\n"
                "CAREER MASTERY BADGE\n\n"
                f"Awarded to: {st.session_state.user_name or 'Career Explorer'}\n"
                "Achievement: 600+ Career XP\n"
                "Purpose: Consistent career preparation and measurable progress.\n"
            )
            st.download_button(
                "⬇️ Download Badge",
                data=badge_text,
                file_name="Alhomora_Career_Mastery_Badge.txt",
                mime="text/plain",
                key="download_badge_reward_v2",
                use_container_width=True,
            )

        if st.button("Close Reward", key="close_reward", use_container_width=True):
            st.session_state.active_reward = ""
            st.rerun()

# =========================================================
# LEARNING ROADMAP
# =========================================================

elif st.session_state.page == "Learning Roadmap":

    st.html("""
    <div class="hero">
        <div class="hero-small">PERSONALIZED LEARNING</div>
        <div class="hero-title">Your Career Roadmap 🗺️</div>
        <div class="hero-text">
            Convert your identified skill gaps into practical tasks and track what you have completed.
        </div>
    </div>
    """)

    if not st.session_state.jd_analysis:
        render_info(
            "Analyze your resume first",
            "Your roadmap is created from the skill gaps found by Resume Intelligence. If Gemini is unavailable, Alhomora can still create a local fallback analysis.",
            "📚"
        )
    else:
        missing = st.session_state.jd_analysis.get("missing_skills", [])
        sync_roadmap_tasks(st.session_state.user_id, missing)
        tasks = load_roadmap_tasks(st.session_state.user_id)

        if not tasks:
            render_info("No roadmap tasks yet", "Your current profile does not have any stored skill-gap tasks.", "🎉")
        else:
            completed_count = sum(1 for row in tasks if row["completed"])
            total_count = len(tasks)
            render_metric("Roadmap Progress", f"{completed_count}/{total_count}", "Tasks completed")

            current_skill = None
            for row in tasks:
                if row["skill"] != current_skill:
                    current_skill = row["skill"]
                    st.html(f'<div class="section-title">📚 {current_skill}</div>')
                done = bool(row["completed"])
                c1, c2 = st.columns([5, 1])
                with c1:
                    icon = "✅" if done else "⬜"
                    render_card(f"{icon} Step {row['task_order']}", row["task"], "📌")
                with c2:
                    if done:
                        st.success("Done")
                    else:
                        if st.button("Complete", key=f"roadmap_task_{row['id']}", use_container_width=True):
                            set_roadmap_task(st.session_state.user_id, row["id"], True)
                            new_xp = award_xp(st.session_state.user_id, 10, "Roadmap task")
                            st.session_state.career_xp = new_xp
                            st.session_state.unlocked_rewards = reward_ids_for_xp(new_xp)
                            st.success("Task completed! +10 XP")
                            st.rerun()

# =========================================================
# PROGRESS
# =========================================================

elif st.session_state.page == "Progress":

    st.html("""
    <div class="hero">
        <div class="hero-small">CAREER PROGRESS</div>
        <div class="hero-title">Track your improvement 📈</div>
        <div class="hero-text">
            Monitor resume alignment, interview preparation, roadmap completion and CareerQuest activity.
        </div>
    </div>
    """)

    a, b, c, d = st.columns(4)
    with a:
        render_metric("⭐ Career XP", st.session_state.career_xp, "Total XP earned")
    with b:
        render_metric("🔥 Streak", st.session_state.career_streak, "Current mission streak")
    with c:
        score = st.session_state.final_interview_report.get("overall_score", 0) if st.session_state.final_interview_report else st.session_state.best_interview_score
        render_metric("🎙 Interview Score", f"{score}%", "Latest completed interview")
    with d:
        render_metric("🎮 Games", st.session_state.games_completed, "Completed challenges")

    st.html('<div class="section-title">Career Development</div>')

    st.write("Resume Intelligence")
    st.progress(min(st.session_state.jd_analysis.get("match_score", 0) / 100, 1.0))

    st.write("Interview Preparation")
    interview_progress = 1.0 if st.session_state.final_interview_report else (0.5 if st.session_state.interview_started else 0.0)
    st.progress(interview_progress)

    st.write("Learning Roadmap")
    roadmap_tasks = load_roadmap_tasks(st.session_state.user_id)
    roadmap_done = sum(1 for row in roadmap_tasks if row["completed"])
    roadmap_progress = roadmap_done / len(roadmap_tasks) if roadmap_tasks else 0.0
    st.progress(roadmap_progress)

    st.write("CareerQuest")
    st.progress(min(st.session_state.career_xp / 600, 1.0))

    st.html('<div class="section-title">📊 Interview History</div>')
    history = load_interview_history(st.session_state.user_id, 10)
    if history:
        for row in history:
            status = "Completed" if row["completed_at"] else "In progress"
            score_text = f"{row['final_score']:.0f}%" if row["completed_at"] else "—"
            render_card(
                f"{row['mode']} Interview • {score_text}",
                f"Target role: {row['target_role'] or 'Not set'} • {status} • Started {row['started_at']}",
                "🎙"
            )
    else:
        render_info("No interviews yet", "Complete your first mock interview to start building your history.", "🎙")

    st.html('<div class="section-title">🧭 Your Next Steps</div>')
    next_steps = []
    if not st.session_state.resume_text:
        next_steps.append("Upload your resume in Resume Intelligence.")
    if not st.session_state.jd_analysis:
        next_steps.append("Run Resume Intelligence to identify skill gaps.")
    if st.session_state.jd_analysis and roadmap_progress < 1:
        next_steps.append("Complete the next roadmap task and earn +10 XP.")
    if not st.session_state.final_interview_report:
        next_steps.append("Complete an adaptive mock interview.")
    if not st.session_state.mission_completed:
        next_steps.append("Complete today's CareerQuest mission.")
    if not next_steps:
        next_steps.append("Keep your streak alive and retry an interview to improve your scores.")

    for item in next_steps:
        st.write(f"🎯 {item}")


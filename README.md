# Alhomora | AI Career Coach

> An AI-powered career platform that helps users understand their resume, prepare for interviews, practice role-specific challenges, and follow a personalized career roadmap.

**Live Demo:** https://alhomora.streamlit.app  
**GitHub:** https://github.com/kowshakshaj4/Alhomora

## ✨ Overview

Alhomora is a career coaching web application built to help job seekers prepare for a target professional role from one place.

A user can upload a resume, choose a target role, and optionally provide a job description. The platform then connects resume analysis, interview practice, career challenges, learning guidance, and progress tracking around that career goal.

The application is designed to adapt its guidance to the user's target career rather than being limited to a single technical profession.

## 🚀 Features

### 📄 Resume Intelligence
- Upload a PDF resume.
- Supports text-based and image/scanned resumes.
- Extracts and analyzes resume information.
- Compares the resume with a target role and optional job description.
- Identifies relevant strengths and skill gaps.
- Builds a career profile from the analysis.

### 🎤 AI Mock Interview
- Practice interviews based on the selected career.
- Questions use the target role, resume, career profile, and job description when available.
- Supports text answers and voice input.
- Provides AI-powered feedback and interview evaluation.

### 🎯 CareerQuest
- Role-adaptive career challenges.
- Generates practical questions and scenarios based on the user's target profession.
- Includes role knowledge, real-world scenarios, and professional problem-solving challenges.
- Awards progress/XP through completed challenges.

### 🗺️ Learning Roadmap
- Uses identified skill gaps to create a learning path.
- Tracks roadmap tasks and completion progress.
- Connects career preparation with the user's target role.

### 📊 Progress Tracking
- Tracks career streaks and XP.
- Stores interview and CareerQuest progress.
- Records relevant career activity and results.

### 📱 Responsive Interface
- Designed for desktop and mobile screens.
- Uses a dark, calm interface with a dedicated Alhomora visual identity.

## 🧠 How It Works

```text
             Target Role
                 │
                 ▼
        ┌─────────────────┐
        │ Resume + JD     │
        └────────┬────────┘
                 │
                 ▼
        ┌─────────────────┐
        │ AI Career       │
        │ Profile         │
        └────────┬────────┘
                 │
       ┌─────────┼─────────┐
       ▼         ▼         ▼
   Resume      Interview  CareerQuest
 Intelligence             │
       │                   ▼
       └──────────► Learning
                    Roadmap
                         │
                         ▼
                    Progress
```

The core idea is to keep the user's **target role, resume, and job description** connected across the major career-preparation features.

## 🛠️ Tech Stack

### Application
- Python
- Streamlit
- HTML/CSS
- Plotly

### AI
- Google Gemini API
- `google-genai`

### Resume Processing
- PyMuPDF (`pymupdf`)
- Gemini document understanding for image/scanned resumes

### Database
- PostgreSQL
- Supabase

### Development & Deployment
- Git
- GitHub
- Streamlit Community Cloud
- Python virtual environment

## 📁 Project Structure

```text
Alhomora/
│
├── app.py
├── requirements.txt
├── README.md
├── .gitignore
│
└── assets/
    └── alhomora_background.png
```

Sensitive/local development files such as `.env`, virtual environments, and local database files are excluded from the repository.

## ⚙️ Run Locally

### 1. Clone the repository

```bash
git clone https://github.com/kowshakshaj4/Alhomora.git
cd Alhomora
```

### 2. Create a virtual environment

Windows:

```powershell
python -m venv .venv
.venv\Scripts\activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

Create a `.env` file in the project root:

```env
GEMINI_API_KEY=your_gemini_api_key
```

For the hosted deployment, database credentials are configured through the deployment platform's secret management system.

**Never commit `.env` or database credentials to GitHub.**

### 5. Start the application

```bash
streamlit run app.py
```

## 🔐 Security

Secrets are kept outside the source code.

- API keys are stored through environment variables or deployment secrets.
- `.env` is excluded using `.gitignore`.
- Database credentials are stored as deployment secrets.
- Local database files are not committed to GitHub.

## ☁️ Deployment

Alhomora is deployed using:

- **GitHub** for source code
- **Streamlit Community Cloud** for the web application
- **Supabase PostgreSQL** for hosted application data
- **Google Gemini API** for AI-powered functionality

## 📌 Project Goals

Alhomora was built to explore how generative AI can be combined with career-development workflows to create a more personalized job-preparation experience.

The project connects resume analysis, interview preparation, practical career challenges, learning guidance, and progress tracking in one application.

## 🔮 Future Improvements

Possible future directions include more advanced job-description matching, richer learning resources, additional interview modes, deeper analytics, and integrations with career platforms.

## 👨‍💻 Author

**Kowshak Shaj**

B.Tech Artificial Intelligence & Machine Learning student.

- GitHub: https://github.com/kowshakshaj4

---

**Built with Python, Streamlit, Gemini, PostgreSQL, and Supabase.**

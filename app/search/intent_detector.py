"""Intent detection.

Classifies a user's message into one of the supported intents using
keyword heuristics, and attempts to identify the relevant subject/module
using known repository metadata plus the user's conversation history. This
avoids the cost/latency of an LLM call just to route the request, and keeps
behavior predictable and testable.

In addition to intent, this module extracts two exam-oriented signals that
shape answer length/structure downstream in the prompt builder:
- requested_marks: a "5 marks" / "10 marks" style hint, if present.
- requested_mcq_count: an explicit "25 MCQs" style count, if present.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from app.database.repository import Repository


class Intent(str, Enum):
    EXPLANATION = "explanation"
    DEFINITION = "definition"
    COMPARISON = "comparison"
    DIFFERENCE = "difference"
    ADVANTAGES_DISADVANTAGES = "advantages_disadvantages"
    APPLICATIONS = "applications"
    FEATURES = "features"
    WORKING = "working"
    ARCHITECTURE = "architecture"
    ALGORITHM = "algorithm"
    INTERVIEW_QUESTIONS = "interview_questions"
    PLACEMENT_PREP = "placement_prep"
    EXAM_ANSWER = "exam_answer"
    REVISION_NOTES = "revision_notes"
    SUMMARY = "summary"
    MCQ = "mcq"
    PROGRAMMING_QUESTION = "programming_question"
    CODE_EXPLANATION = "code_explanation"
    FOLLOW_UP = "follow_up"
    GENERAL_QUESTION = "general_question"


# Order matters: earlier entries are checked first, so more specific phrases
# (e.g. "advantages and disadvantages") are matched before generic ones.
_INTENT_KEYWORDS: dict[Intent, list[str]] = {
    Intent.MCQ: ["mcq", "multiple choice", "quiz me"],
    Intent.INTERVIEW_QUESTIONS: ["interview question", "interview prep", "asked in interviews"],
    Intent.PLACEMENT_PREP: ["placement", "campus recruitment", "off-campus", "aptitude"],
    Intent.PROGRAMMING_QUESTION: ["coding question", "programming problem", "leetcode", "write a program", "write code for"],
    Intent.CODE_EXPLANATION: ["explain this code", "what does this code do", "explain the following code"],
    Intent.EXAM_ANSWER: ["marks question", "for exam", "semester exam", "write an answer for", "marks answer", "university exam"],
    Intent.REVISION_NOTES: [
        "revision notes", "quick notes", "short notes", "notes for revision",
        "important questions", "one-line notes", "one line notes", "exam revision", "revision mode",
    ],
    Intent.SUMMARY: ["summarize", "summarise", "summary of", "tl;dr"],
    Intent.COMPARISON: ["compare", "comparison between", "vs", "versus"],
    Intent.DIFFERENCE: ["difference between", "differentiate"],
    Intent.ADVANTAGES_DISADVANTAGES: ["advantages and disadvantages", "pros and cons", "merits and demerits", "advantages", "disadvantages"],
    Intent.APPLICATIONS: ["applications of", "application of", "real world use", "real-world use", "use cases", "applications", "application"],
    Intent.FEATURES: ["features of", "key features", "characteristics of"],
    Intent.WORKING: ["working of", "how does it work", "how it works"],
    Intent.ARCHITECTURE: ["architecture of", "design of", "components of"],
    Intent.ALGORITHM: ["algorithm for", "how does the algorithm", "steps of the algorithm"],
    Intent.DEFINITION: ["what is", "define", "definition of"],
}

_FOLLOW_UP_PATTERNS = [
    r"^give (an )?example",
    r"^what about",
    r"^and (its|the)",
    r"^why",
    r"^how about",
    r"^what are (its|the) (disadvantages|advantages|drawbacks|benefits)",
    r"^continue",
    r"^more (on|about) (this|that)",
    r"^elaborate",
]

# Common course-code aliases that may not literally match a GitHub folder
# name (e.g. a folder named "Database Management Systems" but students ask
# about "DBMS"). This only widens matching for subject detection — the
# repository's own folder names remain the source of truth via
# repository.list_subjects().
_SUBJECT_ALIASES: dict[str, list[str]] = {
    "dbms": ["database management", "database management system"],
    "daa": ["design and analysis of algorithms"],
    "os": ["operating system"],
    "cn": ["computer network"],
    "dms": ["data mining"],
    "dav": ["data analytics and visualization", "data analysis and visualization"],
    "usp": ["unix system programming", "unix and shell programming"],
}

_MARKS_PATTERN = re.compile(r"(\d{1,2})\s*(?:-|\s)?\s*mark", re.IGNORECASE)
_MCQ_COUNT_PATTERN = re.compile(r"(\d{1,3})\s*(?:mcqs?|questions?)", re.IGNORECASE)


@dataclass
class DetectedContext:
    intent: Intent
    subject: str | None
    module: str | None
    is_follow_up: bool
    requested_marks: int | None = None
    requested_mcq_count: int | None = None


def detect_intent(message: str) -> Intent:
    text = message.strip().lower()

    for pattern in _FOLLOW_UP_PATTERNS:
        if re.match(pattern, text):
            return Intent.FOLLOW_UP

    for intent, keywords in _INTENT_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            return intent

    return Intent.EXPLANATION if len(text.split()) > 2 else Intent.GENERAL_QUESTION


def detect_requested_marks(message: str) -> int | None:
    """Extract a "5 marks" / "10-mark" style hint, if present."""
    match = _MARKS_PATTERN.search(message)
    if not match:
        return None
    marks = int(match.group(1))
    return marks if 0 < marks <= 20 else None


def detect_requested_mcq_count(message: str, intent: Intent) -> int | None:
    """Extract an explicit MCQ count (e.g. "25 MCQs"), only when intent is MCQ."""
    if intent != Intent.MCQ:
        return None
    match = _MCQ_COUNT_PATTERN.search(message)
    if not match:
        return None
    count = int(match.group(1))
    return count if 0 < count <= 100 else None


def _alias_matches(subject_key: str, text: str) -> bool:
    """Check subject-name aliasing in both directions.

    Handles both a short-code folder name with the student using the long
    form ("DBMS" folder, student says "database management system") and a
    long-form folder name with the student using the short code
    ("Database Management Systems" folder, student says "DBMS").

    The short-code side always matches on a whole word (``\\bos\\b``) so a
    two-letter code like "os" doesn't false-positive inside unrelated words
    such as "most" or "close".
    """
    subject_lower = subject_key.lower()
    for code, aliases in _SUBJECT_ALIASES.items():
        code_pattern = rf"\b{re.escape(code)}\b"
        if (subject_lower == code or code in subject_lower) and any(alias in text for alias in aliases):
            return True
        if any(alias in subject_lower for alias in aliases) and re.search(code_pattern, text):
            return True
    return False


def detect_subject_module(
    message: str,
    repository: Repository,
    *,
    session_subject: str | None,
    session_module: str | None,
) -> tuple[str | None, str | None]:
    """Match known subjects/modules against the message; fall back to session focus.

    Matching checks each repository subject both literally and against a
    small alias table (e.g. "DBMS" also matches "database management
    system"), so course-code shorthand works even when the GitHub folder
    uses a longer name, or vice versa.
    """
    text = message.lower()

    subjects = repository.list_subjects()
    matched_subject = next(
        (s for s in subjects if s.lower() in text or _alias_matches(s, text)),
        None,
    )

    modules = repository.list_modules(matched_subject) if matched_subject else repository.list_modules()
    matched_module = next((m for m in modules if m and m.lower() in text), None)

    subject = matched_subject or session_subject
    module = matched_module or (session_module if matched_subject in (None, session_subject) else None)
    return subject, module


def build_detected_context(
    message: str,
    repository: Repository,
    *,
    session_subject: str | None,
    session_module: str | None,
) -> DetectedContext:
    intent = detect_intent(message)
    subject, module = detect_subject_module(
        message, repository, session_subject=session_subject, session_module=session_module
    )
    return DetectedContext(
        intent=intent,
        subject=subject,
        module=module,
        is_follow_up=(intent == Intent.FOLLOW_UP),
        requested_marks=detect_requested_marks(message),
        requested_mcq_count=detect_requested_mcq_count(message, intent),
    )

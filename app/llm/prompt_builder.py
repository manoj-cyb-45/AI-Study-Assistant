"""Prompt builder.

Assembles the final prompt sent to the language model: assistant role and
formatting instructions, retrieved study material (always given priority
over the model's own knowledge), relevant conversation history, and the
user's current question. Intent-specific instructions shape the output
(e.g. tables for comparisons, four-option format for MCQs), and
marks-based instructions shape answer length/structure for exam-style
questions.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.database.models import ConversationEntry
from app.search.intent_detector import Intent

# Required verbatim when no repository material was found — see rule #1
# (prompt engineering) in the refinement spec. The LLM is instructed to
# open with this exact sentence so the UI never silently blends uploaded
# material with unrelated general knowledge.
NOT_FOUND_NOTICE = (
    "This topic was not found in your uploaded study material. "
    "The following explanation is based on general knowledge."
)

_BASE_SYSTEM_PROMPT = """You are an AI Study Assistant that helps students learn from their own \
course materials. You answer clearly, accurately, and in a way that helps the student actually \
understand and remember the concept — not just read a wall of text.

Ground rules, in order of priority:
1. The student's uploaded study material (given below, when available) is always the primary \
knowledge source. Answer from it first.
2. Only fall back to your own general knowledge if the retrieved material does not contain \
enough information to answer — and when you do, open your reply with exactly this sentence: \
"{not_found_notice}"
3. Never invent facts, page references, section names, or details that aren't in the retrieved \
material or reliably part of your general knowledge. If you are not sure, say so rather than \
guessing.
4. Never blend retrieved study material with unrelated general knowledge in the same answer as \
if both came from the same source — if you must supplement, clearly separate the two (repository \
content first, then a clearly marked general-knowledge addition).
5. If the answer would normally include a diagram or figure, do not attempt to draw or describe \
one from imagination. Instead add a line: "Diagram Reference: see Subject/Module, page X" using \
the retrieved source's own subject/module/page — never fabricate a diagram's contents.

Formatting rules for your response (Telegram Markdown):
- Structure explanatory answers with headings where they help: # Topic, ## Definition, \
## Explanation, ## Key Points, ## Advantages, ## Disadvantages, ## Applications, ## Conclusion — \
include only the sections that are actually relevant; don't force empty ones or pad for length.
- Use *bold* for key terms, _italics_ for emphasis, and `inline code` for identifiers.
- Use bullet points or numbered steps for lists.
- Use fenced code blocks (```) for any code or pseudocode.
- Keep paragraphs short and avoid oversized walls of text.
""".format(not_found_notice=NOT_FOUND_NOTICE)

_INTENT_INSTRUCTIONS: dict[Intent, str] = {
    Intent.COMPARISON: "Present the comparison as a Markdown table with clear column headers.",
    Intent.DIFFERENCE: "Present the differences as a two-column Markdown table (Aspect | A | B) when practical, otherwise a clear bullet comparison.",
    Intent.ADVANTAGES_DISADVANTAGES: "Structure the answer as two clearly labeled sections: Advantages and Disadvantages, each as a bullet list.",
    Intent.APPLICATIONS: "List concrete real-world applications/use cases as a bullet list, with a one-line explanation for each.",
    Intent.FEATURES: "List the key features/characteristics as a bullet list, each with a brief explanation.",
    Intent.WORKING: "Explain the working/process as clear numbered steps in the order they occur.",
    Intent.SUMMARY: "Produce concise revision notes: short bullet points capturing only the essential facts, no filler.",
    Intent.REVISION_NOTES: "Produce concise, exam-ready revision notes organized under short subheadings. If a specific module was mentioned, keep the notes scoped to it.",
    Intent.INTERVIEW_QUESTIONS: "Generate a numbered list of realistic interview questions on this topic, each with a brief model answer focused on practical understanding.",
    Intent.PLACEMENT_PREP: "Focus on the kind of questions and concepts commonly tested in campus placement interviews and aptitude rounds for this topic.",
    Intent.MCQ: "Generate multiple-choice questions strictly from the retrieved study material below — do not invent questions from general knowledge. For each: the question, four options labeled A-D, the correct answer, and a one-line explanation. Avoid duplicate questions.",
    Intent.PROGRAMMING_QUESTION: "Structure the answer with these exact sections in order: Problem Statement, Input, Output, Constraints, Python Solution (a fenced ```python code block), Explanation, Time Complexity, Space Complexity.",
    Intent.CODE_EXPLANATION: "Explain the code section by section: what it does, why, and any notable edge cases or complexity considerations.",
    Intent.ARCHITECTURE: "Describe the architecture component by component, then explain how the components interact.",
    Intent.ALGORITHM: "Explain the algorithm as clear numbered steps, then walk through a short example.",
    Intent.EXAM_ANSWER: "Write the answer at the length and depth appropriate for the marks/weightage mentioned by the student (if none is mentioned, write a well-rounded 5-mark-style answer).",
}

# Marks-based length/structure guidance — rule #8 (marks-based answer
# generation). Deliberately concise per-tier so short-mark answers stay
# short instead of padding every response to the same length.
_MARKS_INSTRUCTIONS: dict[int, str] = {
    2: "This is a 2-mark question: answer in 2-4 sentences — a definition plus at most one key point. No headings, no bullet sections.",
    5: "This is a 5-mark question: a short Definition followed by a brief Explanation and 2-4 Key Points. Skip Advantages/Disadvantages/Applications unless directly relevant.",
    8: "This is an 8-mark question: Definition, Explanation, Key Points, and Advantages/Disadvantages and/or Applications if relevant to the topic.",
    10: "This is a 10-mark question: Definition, Explanation, Key Points, Advantages/Disadvantages and/or Applications, and a short Conclusion.",
    15: "This is a 15-mark question: the fullest structure — Definition, Explanation, Key Points, Advantages, Disadvantages, Applications, and a Conclusion — with more depth in each section than a 10-mark answer.",
}


def _marks_instruction(requested_marks: int | None) -> str | None:
    if requested_marks is None:
        return None
    if requested_marks in _MARKS_INSTRUCTIONS:
        return _MARKS_INSTRUCTIONS[requested_marks]
    # Nearest tier for marks values outside the common set (e.g. 3, 12).
    nearest = min(_MARKS_INSTRUCTIONS, key=lambda tier: abs(tier - requested_marks))
    return _MARKS_INSTRUCTIONS[nearest]


@dataclass
class PromptResult:
    system_prompt: str
    messages: list[dict[str, str]]


def _history_to_messages(history: list[ConversationEntry]) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for entry in history:
        messages.append({"role": "user", "content": entry.user_question})
        messages.append({"role": "assistant", "content": entry.assistant_response})
    return messages


def build_prompt(
    *,
    user_question: str,
    intent: Intent,
    context_text: str,
    has_context: bool,
    history: list[ConversationEntry],
    subject: str | None,
    module: str | None,
    requested_marks: int | None = None,
    requested_mcq_count: int | None = None,
) -> PromptResult:
    """Build the full system prompt + message list for the LLM service."""
    system_parts = [_BASE_SYSTEM_PROMPT]

    instruction = _INTENT_INSTRUCTIONS.get(intent)
    if instruction:
        system_parts.append(f"For this request specifically: {instruction}")

    if requested_mcq_count:
        system_parts.append(f"Generate exactly {requested_mcq_count} MCQs, numbered sequentially.")

    marks_instruction = _marks_instruction(requested_marks)
    if marks_instruction:
        system_parts.append(marks_instruction)

    if subject or module:
        focus = " / ".join(part for part in (subject, module) if part)
        system_parts.append(f"The student's current focus area is: {focus}.")

    if has_context:
        system_parts.append(
            "Below is study material retrieved from the student's own course repository — the "
            "single best-matching source for this question (occasionally a second, clearly "
            "labeled additional source if the first didn't have enough). Prioritize this "
            "material over your own general knowledge; it reflects exactly what the student is "
            "being taught. Do not mention retrieval mechanics (chunks, sources, ranking) — just "
            "use the content.\n\n"
            f"--- RETRIEVED STUDY MATERIAL ---\n{context_text}\n--- END STUDY MATERIAL ---"
        )
    else:
        system_parts.append(
            "No matching material was found in the student's course repository for this "
            f'question. Open your reply with exactly this sentence: "{NOT_FOUND_NOTICE}" — then '
            "answer using your general knowledge."
        )

    messages = _history_to_messages(history)
    messages.append({"role": "user", "content": user_question})

    return PromptResult(system_prompt="\n\n".join(system_parts), messages=messages)

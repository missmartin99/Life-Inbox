"""Privacy layer: every text AI call in Life Inbox goes through ask_ai().

Why one function? If all AI calls pass through a single "door", we only have
to get privacy right in one place:

  1. redact()  -- hide phone numbers, emails, card / ID numbers before sending
  2. call the AI model (OpenAI or DeepSeek -- both use the `openai` library)
  3. restore() -- put the real values back into the AI's reply, locally
  4. log the call in the ai_call_log table (what was sent, never the text)

Switching to DeepSeek only needs environment variables (e.g. in .env):
    OPENAI_API_KEY=<your deepseek key>
    OPENAI_BASE_URL=https://api.deepseek.com
    OPENAI_MODEL=deepseek-chat
"""

import json
import logging
import os
import re

logger = logging.getLogger(__name__)


def extract_json_object(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.replace("```json", "").replace("```", "").strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object found in model output")
    return json.loads(raw[start : end + 1])

# Shown to the AI instead of the content of an item marked "sensitive".
HIDDEN_TEXT = "[private item - content hidden]"

# Order matters: longer number patterns must run before shorter ones,
# otherwise a 16-digit card number would be half-matched as a phone number.
PATTERNS = [
    ("EMAIL", r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),
    ("CARD", r"\b\d{4}[ -]?\d{4}[ -]?\d{4}[ -]?\d{4}\b"),
    ("AADHAAR", r"\b\d{4} \d{4} \d{4}\b"),
    ("PAN", r"\b[A-Z]{5}\d{4}[A-Z]\b"),
    # 10 digits, optionally split by spaces/dashes, with an optional +country code.
    ("PHONE", r"(?<!\w)(?:\+\d{1,3}[ -]?)?(?:\d[ -]?){9}\d\b"),
]


def redact(text):
    """Replace personal details with placeholders like [PHONE_1].

    Returns (redacted_text, mapping) where mapping is
    {"[PHONE_1]": "98765 43210", ...} so the values can be restored later.
    The same value always gets the same placeholder.
    """
    mapping = {}
    for label, pattern in PATTERNS:
        counter = 0

        def replace(match):
            nonlocal counter
            value = match.group(0)
            # Reuse the placeholder if we have seen this exact value before.
            for placeholder, original in mapping.items():
                if original == value:
                    return placeholder
            counter += 1
            placeholder = f"[{label}_{counter}]"
            mapping[placeholder] = value
            return placeholder

        text = re.sub(pattern, replace, text)
    return text, mapping


def restore(text, mapping):
    """Put the real values back in place of the placeholders."""
    for placeholder, original in mapping.items():
        text = text.replace(placeholder, original)
    return text


def ai_is_configured():
    return bool(os.getenv("OPENAI_API_KEY", "").strip())


def ask_ai(prompt, feature):
    """Send a prompt to the AI model safely and return its text reply.

    `feature` is a short label for the log, e.g. "analyze" or "ask".
    Raises an exception if the call fails, so callers can use their own
    non-AI fallback (every feature in Life Inbox has one).
    """
    from openai import OpenAI  # imported here so tests can replace it

    model = os.getenv("OPENAI_MODEL", "gpt-5-mini")
    safe_prompt, mapping = redact(prompt)

    try:
        client = OpenAI()  # reads OPENAI_API_KEY and OPENAI_BASE_URL itself
        # chat.completions works with both OpenAI and DeepSeek.
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": safe_prompt}],
            max_tokens=int(os.getenv("OPENAI_MAX_OUTPUT_TOKENS", "8000")),
        )
        reply = response.choices[0].message.content or ""
    except Exception:
        log_ai_call(feature, model, "error", len(safe_prompt), len(mapping))
        raise

    log_ai_call(feature, model, "ok", len(safe_prompt), len(mapping))
    return restore(reply, mapping)


def log_ai_call(feature, model, status, chars_sent, redactions):
    """Record that an AI call happened. Logging must never break the app."""
    try:
        from app.database import add_ai_call_log, get_current_owner_id

        # Only log calls made on behalf of a signed-in user (i.e. API requests).
        if get_current_owner_id() is None:
            return
        add_ai_call_log(feature, model, status, chars_sent, redactions)
    except Exception:
        logger.warning("Could not write AI call log", exc_info=True)

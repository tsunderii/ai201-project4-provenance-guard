import json
import os
import uuid
from datetime import datetime, timezone

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from groq import Groq


load_dotenv()

app = Flask(__name__)

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)

LOG_PATH = "audit_log.jsonl"


def log_event(entry):
    entry["timestamp"] = datetime.now(timezone.utc).isoformat()

    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")


def read_log(limit=20):
    try:
        with open(LOG_PATH, "r") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return []

    return [json.loads(line) for line in lines[-limit:]]


def get_placeholder_label(attribution):
    if attribution == "likely_ai":
        return (
            "Provenance Guard found signs that this piece may have been AI-generated. "
            "This is an early single-signal assessment and not absolute proof."
        )

    if attribution == "likely_human":
        return (
            "Provenance Guard found signs that this piece was likely written by a human creator. "
            "This is an early single-signal assessment and may change after more signals are added."
        )

    return (
        "Provenance Guard could not confidently determine whether this piece was human-written "
        "or AI-generated. More signals are needed before making a stronger claim."
    )


def attribution_from_score(score):
    if score >= 0.80:
        return "likely_ai"
    if score <= 0.29:
        return "likely_human"
    return "uncertain"


def groq_llm_signal(text):
    """
    First detection signal.

    Returns:
        {
            "score": float from 0.0 to 1.0,
            "label": "likely_ai" | "likely_human" | "uncertain",
            "reasoning": str
        }

    Score meaning:
        0.0 = strongly human-written
        0.5 = uncertain
        1.0 = strongly AI-generated
    """

    api_key = os.getenv("GROQ_API_KEY")

    if not api_key:
        raise RuntimeError("Missing GROQ_API_KEY. Add it to your .env file.")

    client = Groq(api_key=api_key)

    prompt = f"""
You are Signal 1 for a system called Provenance Guard.

Your task is to assess whether the submitted creative writing seems more likely
human-written, AI-generated, or uncertain.

Important rules:
- Do not claim certainty.
- A polished human writer may look AI-like, so be cautious.
- A score near 0.5 means uncertain.
- Return only valid JSON.
- Do not include markdown.

Use this exact JSON shape:
{{
  "score": 0.0,
  "label": "likely_human",
  "reasoning": "one short explanation"
}}

Score meaning:
- 0.0 = strongly human-written
- 0.5 = uncertain / mixed evidence
- 1.0 = strongly AI-generated

Allowed labels:
- likely_human
- uncertain
- likely_ai

Text to evaluate:
{text}
"""

    completion = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {
                "role": "system",
                "content": "You are a cautious text attribution classifier that returns only JSON."
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        temperature=0,
    )

    raw_content = completion.choices[0].message.content

    try:
        parsed = json.loads(raw_content)
    except json.JSONDecodeError:
        return {
            "score": 0.5,
            "label": "uncertain",
            "reasoning": "Groq response could not be parsed as JSON, so the system defaulted to uncertain.",
            "raw_response": raw_content
        }

    score = float(parsed.get("score", 0.5))

    # Clamp score so it always stays between 0 and 1.
    score = max(0.0, min(1.0, score))

    label = parsed.get("label", attribution_from_score(score))

    if label not in ["likely_human", "uncertain", "likely_ai"]:
        label = attribution_from_score(score)

    return {
        "score": score,
        "label": label,
        "reasoning": parsed.get("reasoning", "No reasoning provided.")
    }


@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "message": "Provenance Guard is running. Try GET /health, POST /submit, or GET /log."
    })


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "running",
        "service": "Provenance Guard"
    })


@app.route("/submit", methods=["POST"])
@limiter.limit("10 per minute;100 per day")
def submit():
    data = request.get_json() or {}

    text = data.get("text", "").strip()
    creator_id = data.get("creator_id", "").strip()
    title = data.get("title", "Untitled").strip()

    if not text:
        return jsonify({
            "error": "Missing required field: text"
        }), 400

    if not creator_id:
        return jsonify({
            "error": "Missing required field: creator_id"
        }), 400

    content_id = str(uuid.uuid4())

    llm_result = groq_llm_signal(text)

    attribution = llm_result["label"]

    # Milestone 3 placeholder confidence:
    # For now, use the Groq score directly.
    # In Milestone 4, this will become the combined Groq + stylometry score.
    confidence = llm_result["score"]

    label = get_placeholder_label(attribution)

    response = {
        "content_id": content_id,
        "creator_id": creator_id,
        "title": title,
        "attribution": attribution,
        "confidence": confidence,
        "label": label,
        "signals": {
            "groq_llm": llm_result
        },
        "status": "classified"
    }

    log_event({
        "event_type": "classification",
        "content_id": content_id,
        "creator_id": creator_id,
        "title": title,
        "text_preview": text[:120],
        "attribution": attribution,
        "confidence": confidence,
        "llm_score": llm_result["score"],
        "llm_label": llm_result["label"],
        "llm_reasoning": llm_result["reasoning"],
        "status": "classified"
    })

    return jsonify(response)


@app.route("/log", methods=["GET"])
def view_log():
    return jsonify({
        "entries": read_log()
    })


if __name__ == "__main__":
    app.run(port=5000, debug=True)
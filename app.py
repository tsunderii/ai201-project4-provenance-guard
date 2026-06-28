import json
import os
import re
import string
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

# In-memory content store keyed by content_id. Each value holds the original
# classification decision plus a mutable status that the appeals workflow updates.
# (A real deployment would use a database; this keeps the milestone self-contained.)
app.submissions = {}
app.appeals = []

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


def generate_transparency_label(attribution):
    """
    Transparency label generator.

    Maps a final attribution (derived from the combined confidence score via
    attribution_from_score) to one of the three exact label variants defined in
    planning.md. The text is intentionally cautious: the AI label says "may have
    been AI-generated" rather than presenting detection as absolute proof.
    """

    if attribution == "likely_ai":
        return (
            "Provenance Guard found strong signs that this piece may have been AI-generated. "
            "This label is based on multiple detection signals and should be read as a "
            "confidence-based assessment, not absolute proof."
        )

    if attribution == "likely_human":
        return (
            "Provenance Guard found strong signs that this piece was likely written by a human creator. "
            "This label is based on multiple detection signals and may still be reviewed if new context is provided."
        )

    return (
        "Provenance Guard could not confidently determine whether this piece was human-written "
        "or AI-generated. The evidence was mixed, so this content is labeled as uncertain rather "
        "than making a stronger claim."
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


def stylometric_signal(text):
    """
    Second detection signal.

    This signal measures structural writing patterns:
    - sentence length variance
    - type-token ratio / vocabulary diversity
    - punctuation density
    - repetition ratio

    Returns:
        {
            "score": float from 0.0 to 1.0,
            "label": "likely_human" | "uncertain" | "likely_ai",
            "features": {...}
        }

    Score meaning:
        0.0 = more human-like structural variation
        0.5 = mixed / uncertain
        1.0 = more AI-like structural uniformity
    """

    sentences = re.split(r"[.!?]+", text)
    sentences = [sentence.strip() for sentence in sentences if sentence.strip()]

    words = re.findall(r"\b\w+\b", text.lower())
    total_words = len(words)

    if total_words == 0:
        return {
            "score": 0.5,
            "label": "uncertain",
            "features": {
                "average_sentence_length": 0,
                "sentence_length_variance": 0,
                "type_token_ratio": 0,
                "punctuation_density": 0,
                "repetition_ratio": 0,
                "note": "No words found."
            }
        }

    sentence_lengths = [
        len(re.findall(r"\b\w+\b", sentence))
        for sentence in sentences
    ]

    if sentence_lengths:
        average_sentence_length = sum(sentence_lengths) / len(sentence_lengths)
    else:
        average_sentence_length = total_words

    if len(sentence_lengths) > 1:
        mean = average_sentence_length
        sentence_length_variance = sum(
            (length - mean) ** 2 for length in sentence_lengths
        ) / len(sentence_lengths)
    else:
        sentence_length_variance = 0

    unique_words = set(words)
    type_token_ratio = len(unique_words) / total_words

    punctuation_count = sum(1 for char in text if char in string.punctuation)
    punctuation_density = punctuation_count / max(len(text), 1)

    repeated_words = total_words - len(unique_words)
    repetition_ratio = repeated_words / total_words

    # Convert individual features into AI-likelihood subscores.
    # Higher score = more AI-like.

    # AI writing often has low sentence length variance.
    if sentence_length_variance < 8:
        variance_score = 0.8
    elif sentence_length_variance < 25:
        variance_score = 0.5
    else:
        variance_score = 0.2

    # AI writing may have lower vocabulary diversity in generic text.
    if type_token_ratio < 0.45:
        vocabulary_score = 0.75
    elif type_token_ratio < 0.65:
        vocabulary_score = 0.5
    else:
        vocabulary_score = 0.25

    # Very low punctuation density can indicate smooth/formal generated prose.
    # Very high punctuation can indicate casual/human messiness, but not always.
    if punctuation_density < 0.025:
        punctuation_score = 0.7
    elif punctuation_density < 0.07:
        punctuation_score = 0.5
    else:
        punctuation_score = 0.25

    # Repetition can be AI-like, but poetry may also repeat intentionally.
    if repetition_ratio > 0.65:
        repetition_score = 0.7
    elif repetition_ratio > 0.45:
        repetition_score = 0.5
    else:
        repetition_score = 0.3

    raw_score = (
        0.35 * variance_score
        + 0.30 * vocabulary_score
        + 0.20 * punctuation_score
        + 0.15 * repetition_score
    )

    # Very short submissions are hard to classify.
    # Pull score toward uncertainty because stylometry is unreliable with little text.
    if total_words < 40:
        final_score = (raw_score + 0.5) / 2
    else:
        final_score = raw_score

    final_score = round(max(0.0, min(1.0, final_score)), 2)

    return {
        "score": final_score,
        "label": attribution_from_score(final_score),
        "features": {
            "word_count": total_words,
            "sentence_count": len(sentences),
            "average_sentence_length": round(average_sentence_length, 2),
            "sentence_length_variance": round(sentence_length_variance, 2),
            "type_token_ratio": round(type_token_ratio, 2),
            "punctuation_density": round(punctuation_density, 3),
            "repetition_ratio": round(repetition_ratio, 2),
            "variance_score": variance_score,
            "vocabulary_score": vocabulary_score,
            "punctuation_score": punctuation_score,
            "repetition_score": repetition_score
        }
    }


def combine_signal_scores(groq_score, stylometric_score):
    """
    Combine both detection signals into one AI-likelihood score.

    planning.md formula:
        combined_score = (0.65 * groq_score) + (0.35 * stylometric_score)

    Thresholds (via attribution_from_score):
        0.80 to 1.00 = likely_ai
        0.30 to 0.79 = uncertain
        0.00 to 0.29 = likely_human
    """

    combined_score = (0.65 * groq_score) + (0.35 * stylometric_score)
    combined_score = round(max(0.0, min(1.0, combined_score)), 2)

    return {
        "combined_score": combined_score,
        "attribution": attribution_from_score(combined_score)
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
    stylometric_result = stylometric_signal(text)

    combined_result = combine_signal_scores(
        llm_result["score"],
        stylometric_result["score"]
    )

    attribution = combined_result["attribution"]
    confidence = combined_result["combined_score"]

    label = generate_transparency_label(attribution)

    response = {
        "content_id": content_id,
        "creator_id": creator_id,
        "title": title,
        "attribution": attribution,
        "confidence": confidence,
        "label": label,
        "signals": {
            "groq_llm": llm_result,
            "stylometric_heuristics": stylometric_result
        },
        "status": "classified"
    }

    # Persist the decision so the appeals workflow can find it and update its status.
    app.submissions[content_id] = {
        "content_id": content_id,
        "creator_id": creator_id,
        "title": title,
        "text_preview": text[:120],
        "attribution": attribution,
        "confidence": confidence,
        "label": label,
        "signals": {
            "groq_llm": llm_result,
            "stylometric_heuristics": stylometric_result
        },
        "status": "classified",
        "appeal": None
    }

    log_event({
        "event_type": "classification",
        "content_id": content_id,
        "creator_id": creator_id,
        "title": title,
        "text_preview": text[:120],
        "attribution": attribution,
        "confidence": confidence,
        "groq_score": llm_result["score"],
        "groq_label": llm_result["label"],
        "groq_reasoning": llm_result["reasoning"],
        "stylometric_score": stylometric_result["score"],
        "stylometric_label": stylometric_result["label"],
        "stylometric_features": stylometric_result["features"],
        "status": "classified",
        "appeal_filed": False
    })

    return jsonify(response)


@app.route("/appeal", methods=["POST"])
def appeal():
    data = request.get_json() or {}

    content_id = data.get("content_id", "").strip()
    # Accept "creator_reasoning" (current spec) or "appeal_reason" (planning.md).
    creator_reasoning = (
        data.get("creator_reasoning")
        or data.get("appeal_reason")
        or ""
    ).strip()

    if not content_id:
        return jsonify({
            "error": "Missing required field: content_id"
        }), 400

    if not creator_reasoning:
        return jsonify({
            "error": "Missing required field: creator_reasoning"
        }), 400

    # Validate that the content_id refers to a real prior classification.
    original = app.submissions.get(content_id)
    if original is None:
        return jsonify({
            "error": f"No content found for content_id: {content_id}"
        }), 404

    appeal_id = str(uuid.uuid4())
    previous_status = original["status"]

    # Update the content status in storage.
    original["status"] = "under_review"
    original["appeal"] = {
        "appeal_id": appeal_id,
        "appeal_reasoning": creator_reasoning
    }

    app.appeals.append({
        "appeal_id": appeal_id,
        "content_id": content_id,
        "appeal_reasoning": creator_reasoning,
        "previous_status": previous_status,
        "status": "under_review"
    })

    # Log the appeal alongside the original classification decision.
    log_event({
        "event_type": "appeal",
        "appeal_id": appeal_id,
        "content_id": content_id,
        "creator_id": original.get("creator_id"),
        "title": original.get("title"),
        "text_preview": original.get("text_preview"),
        "appeal_reasoning": creator_reasoning,
        "previous_status": previous_status,
        "status": "under_review",
        "original_classification": {
            "attribution": original.get("attribution"),
            "confidence": original.get("confidence"),
            "groq_score": original["signals"]["groq_llm"]["score"],
            "groq_reasoning": original["signals"]["groq_llm"].get("reasoning"),
            "stylometric_score": original["signals"]["stylometric_heuristics"]["score"],
            "stylometric_features": original["signals"]["stylometric_heuristics"].get("features"),
            "transparency_label": original.get("label")
        }
    })

    return jsonify({
        "appeal_id": appeal_id,
        "content_id": content_id,
        "status": "under_review",
        "message": "Your appeal has been received and the content is now under review."
    })


@app.route("/log", methods=["GET"])
def view_log():
    return jsonify({
        "entries": read_log()
    })


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(port=port, debug=True)
# Provenance Guard

Provenance Guard is a small Flask application that estimates whether a piece of creative writing looks more likely to be human-written or AI-generated. The project is intentionally cautious: instead of presenting a binary verdict, it returns a confidence score, a transparency label, and a structured audit trail that can be reviewed by a human operator.

## Project goal

The core problem this project addresses is not “can we perfectly detect AI text?” but “can we build a system that surfaces uncertainty clearly and gives creators a fair review path?” That framing shaped both the architecture and the product decisions. The service is designed for a creative-writing platform where false positives are costly, so the interface emphasizes explanation over certainty.

## Architecture overview

The app is organized around a simple request pipeline in [app.py](app.py):

1. A client submits text through the Flask `/submit` endpoint.
2. The text is evaluated by two independent signals: a Groq-backed LLM signal and a stylometric heuristic signal.
3. The two scores are combined into one confidence value and mapped to an attribution category.
4. The result is persisted in memory for appeals, written to a JSONL audit log, and returned to the user with a transparency label.

That path is the key architecture flow: input text -> signal evaluation -> confidence score -> transparency label -> storage/logging. I chose a lightweight architecture for clarity and milestone delivery. A real production deployment would likely replace the in-memory submission store with a database and the file-based log with an append-only event store, but the current structure is enough to demonstrate the full workflow end to end without extra infrastructure.

A submission returns a structured JSON payload that includes the attribution result, confidence score, transparency label text, and the individual signal outputs. For example:

```json
{
  "content_id": "4f1f7b37-0d11-4d95-94d1-3016dc3f2691",
  "attribution": "likely_ai",
  "confidence": 0.85,
  "label": "Provenance Guard found strong signs that this piece may have been AI-generated. This label is based on multiple detection signals and should be read as a confidence-based assessment, not absolute proof.",
  "signals": {
    "groq_llm": {"score": 0.9, "label": "likely_ai"},
    "stylometric_heuristics": {"score": 0.75, "label": "likely_ai"}
  }
}
```

## Detection signals and why they exist

I used two signals because a single score would be too brittle for this problem. A pure model-based score can overfit to surface-level wording, while a pure heuristic score can miss broader semantic context. Together they offer a useful balance:

- The Groq signal measures broader semantics and stylistic cues by asking a language model to judge the text holistically. I chose it because it can notice patterns that are hard to encode manually, but it can miss explicit structural signals and can be overconfident when the writing is polished or unusually phrased.
- The stylometric signal measures concrete properties such as sentence-length variance, vocabulary diversity, punctuation density, and repetition ratio. I chose it because it is transparent, deterministic, and grounded in the text itself, but it misses deeper semantic context and can be fooled by intentionally stylized or very short writing.

That combination makes the system more transparent and easier to reason about, especially when the result lands in the uncertain band.

## Confidence scoring approach

The final confidence is computed with a weighted combination:

```text
combined_score = (0.65 * groq_score) + (0.35 * stylometric_score)
```

I weighted the Groq signal more heavily because it evaluates the text more holistically, while the stylometric signal acts as a second opinion and a guardrail against overconfident model judgments. The score is then mapped to three categories:

- 0.80–1.00: likely_ai
- 0.30–0.79: uncertain
- 0.00–0.29: likely_human

I intentionally made the uncertain band fairly broad because false positives are harmful in a creative-writing context. A score like 0.51 does not mean the system has “proven” that the content is AI-generated; it means the evidence was mixed and the system should be cautious.

If this were deployed for real, I would change the scoring in three ways:

1. Calibrate the weights against a labeled dataset of human-reviewed cases.
2. Add a third signal, such as edit-history or metadata-based features, to reduce brittleness.
3. Make the thresholds configurable so different communities could choose a more strict or more permissive policy.

### Example submissions with meaningful score variation

I validated that the scoring is meaningful by running contrasting submissions through the live `/submit` workflow and checking that the combined score moved in a way that matched the text characteristics. The two examples below produced clearly different confidence values:

- Repetitive, uniform prose: combined score 0.85, attribution likely_ai
- Formal, borderline academic prose: combined score 0.51, attribution uncertain

That variation matters because it shows the scoring is not a constant output; it responds to different linguistic patterns and changes the resulting label accordingly.

## Transparency labels

The transparency label is the user-facing explanation returned by `/submit`. It is intentionally written to avoid overstating certainty. The system exposes three variants, each with a distinct message:

- High-confidence AI: “Provenance Guard found strong signs that this piece may have been AI-generated. This label is based on multiple detection signals and should be read as a confidence-based assessment, not absolute proof.”
- High-confidence human: “Provenance Guard found strong signs that this piece was likely written by a human creator. This label is based on multiple detection signals and may still be reviewed if new context is provided.”
- Uncertain: “Provenance Guard could not confidently determine whether this piece was human-written or AI-generated. The evidence was mixed, so this content is labeled as uncertain rather than making a stronger claim.”

The label is generated from the final attribution rather than being hard-coded per request, so it changes with the confidence outcome.

## Appeals workflow and auditability

The app includes a lightweight appeals process. After a submission is classified, a caller can submit a POST request to `/appeal` with the content ID and appeal reasoning. The app updates the stored status to `under_review`, records the appeal in memory, and appends a structured audit entry that includes the previous status, the appeal reasoning, and a snapshot of the original classification.

This design reflects the product goal of giving creators a path to contest an outcome without pretending the system is perfect. Appeals are not auto-reclassified; they are handed off to a human reviewer for follow-up. The current test suite exercises this flow end to end by submitting a classification, sending an appeal with creator reasoning, and verifying that the returned status is `under_review` and that the appeal entry appears in the audit log.

## Rate limiting

The `/submit` route is rate-limited with Flask-Limiter so a single IP address cannot flood the detection pipeline. The current policy is 10 submissions per minute and 100 per day. That limit is generous for normal manual use but restrictive enough to slow down simple automated abuse. The current tests verify that once the per-minute threshold is exceeded, the 11th request returns `429 Too Many Requests`, which is the behavior a platform operator would see in production.

## Audit log format

Every classification and appeal is appended as a structured JSON line to [audit_log.jsonl](audit_log.jsonl). Each entry captures the event type, timestamp, content ID, creator ID, title, preview text, and the relevant signal data. That makes the system reviewable even when the state exists only in memory. A representative log entry looks like this:

```json
{"event_type": "classification", "timestamp": "2026-06-28T...", "content_id": "...", "attribution": "likely_ai", "confidence": 0.85, "status": "classified"}
```

The app also appends appeal entries with the appeal ID, previous status, the creator’s reasoning, and the original classification snapshot so the review trail is preserved.

## Known limitations

This system is deliberately limited and I would not treat it as a high-stakes decision engine. One specific case it will likely get wrong is highly stylized human-authored poetry or very short creative snippets. The stylometric signal is sensitive to sentence-length variance, repetition, and punctuation density, so a human piece that is intentionally repetitive or unusually compact can look AI-like even when it is not. That is a weakness of the signal design itself rather than a generic data issue.

## Spec reflection

One way the spec helped guide implementation was by making the API shape and the audit-log requirements concrete. The planning document clearly called for `/submit`, `/appeal`, a transparency label, and structured logging, so I could build to those requirements directly.

One place the implementation diverged from the spec was in the appeals behavior. The spec described a full review workflow, but the milestone scope was narrower, so I chose not to implement automatic reclassification after an appeal. That decision kept the project focused on the core value of showing a clear appeal path and preserving decision context for a human reviewer.

## AI usage

I used AI assistance in two specific ways while building the project:

1. I asked an AI coding assistant to draft the initial Flask route skeletons and JSON response shapes for `/submit` and `/appeal`. It produced a workable scaffold quickly, but I revised the handlers to ensure the audit log fields, confidence output, and status transitions matched the project requirements.
2. I asked for help drafting the transparency label copy and the initial README structure. The first pass was useful as wording inspiration, but I overrode it to make the tone more cautious and to ensure the labels reflected the actual scoring logic rather than sounding overly definitive.

## Running locally

```bash
source .venv/bin/activate
python app.py
```

The app serves on port 5000. On macOS, `localhost:5000` may be intercepted by the AirPlay Receiver, so use `http://127.0.0.1:5000` in curl commands if requests return empty responses.

## Testing

```bash
python -m unittest tests.test_app -v
```

## Stretch features

The project also includes a lightweight set of bonus features that extend the core workflow:

- Ensemble-style detection summary: the API returns both individual signal scores and the combined result, so the submission payload shows how the signals contributed to the final outcome.
- Provenance certificate: when a submission includes creator verification, the response includes a distinct certificate object with a verified provenance message.
- Analytics dashboard: the `/analytics` endpoint reports total submissions, total appeals, the appeal rate, and attribution counts.
- Multi-modal support: the `/submit` endpoint accepts a `content_type` field, so non-text content such as image descriptions can be routed through the same submission pipeline and return a response with a certificate field when verified.

## Portfolio walkthrough

A short walkthrough script is available in [portfolio_walkthrough.md](portfolio_walkthrough.md).

# Provenance Guard Planning Spec

## Project Overview

Provenance Guard is a backend system designed for creative-sharing platforms where users post original writing. The system analyzes submitted text and returns an attribution result, confidence score, and transparency label explaining whether the text appears likely human-written, likely AI-generated, or uncertain.

The goal of this project is not to perfectly detect AI writing. AI detection is not fully reliable, especially for creative work. Instead, Provenance Guard is designed to combine multiple signals, communicate uncertainty clearly, and give creators an appeals process if they believe their work was misclassified.

---

## Architecture

```text
Submission Flow

User / Creative Platform
        |
        | POST /submit
        | raw text, creator_id, title
        v
Flask API Endpoint
        |
        | validated text
        v
Detection Pipeline
        |
        | raw text
        |----------------------------|
        v                            v
Groq LLM Signal              Stylometric Signal
        |                            |
        | llm_label, llm_score        | heuristic_score, feature_values
        |                            |
        |------------->--------------|
                      |
                      v
              Confidence Scoring
                      |
                      | combined_score, final classification
                      v
          Transparency Label Generator
                      |
                      | label text
                      v
                 Audit Log
                      |
                      | saved decision with signals,
                      | confidence, label, timestamp
                      v
                 API Response
                      |
                      | content_id, result,
                      | confidence, label, signals
                      v
                User / Platform
```

```text
Appeal Flow

Creator
   |
   | POST /appeal
   | content_id, creator_reasoning
   v
Flask API Endpoint
   |
   | validated appeal request
   v
Appeals Component
   |
   | finds original decision
   | saves appeal reasoning
   | updates status to "under_review"
   v
Audit Log
   |
   | records appeal with original decision,
   | reason, timestamp, and new status
   v
API Response
   |
   | appeal_id, content_id,
   | status: "under_review",
   | confirmation message
   v
Creator
```

When a user submits text, the request first reaches the Flask `/submit` endpoint. The text is validated, passed into the detection pipeline, analyzed by both the Groq LLM signal and the stylometric heuristic signal, combined into a final confidence score, converted into a transparency label, saved in the audit log, and returned to the user as a structured JSON response.

When a creator submits an appeal, the request goes to the Flask `/appeal` endpoint. The system finds the original content decision, saves the creator’s reasoning, updates the content status to `"under_review"`, records the appeal in the audit log, and returns a confirmation response.

---

## Detection Signals

Provenance Guard will use two distinct detection signals. The first signal is semantic and model-based. The second signal is structural and statistical. These signals are intentionally different because they capture different properties of the submitted text.

### Signal 1: Groq LLM Classification

The first signal uses Groq with `llama-3.3-70b-versatile` to classify the text as likely AI-generated, likely human-written, or uncertain.

This signal measures the overall impression of the writing. It looks at tone, coherence, originality, specificity, structure, and whether the piece feels formulaic or overly polished. This matters because AI-generated writing often has recognizable patterns, such as generic phrasing, smooth but predictable structure, or a lack of specific personal voice.

The output of this signal will be a score from `0.0` to `1.0`, where:

* `0.0` means strongly human-written
* `0.5` means uncertain
* `1.0` means strongly AI-generated

Example output:

```json
{
  "signal_name": "groq_llm",
  "score": 0.82,
  "label": "likely_ai",
  "reasoning": "The text is highly polished, generic, and structurally uniform."
}
```

Blind spot: the LLM signal can be biased or overconfident. It may misclassify polished human writing as AI-generated or classify messy AI-generated writing as human. It is also not objective proof of authorship.

---

### Signal 2: Stylometric Heuristics

The second signal uses pure Python to calculate measurable writing features. This signal does not try to understand the meaning of the text. Instead, it measures structural patterns in the writing.

The stylometric analyzer will calculate features such as:

* Average sentence length
* Sentence length variance
* Type-token ratio, which measures vocabulary diversity
* Punctuation density
* Repetition or repeated phrase patterns

This matters because AI writing often tends to be smoother and more uniform, while human writing can be more varied, uneven, or idiosyncratic. For example, a human writer may use one very short sentence followed by a long emotional sentence, while AI writing may keep sentence length and paragraph structure more balanced.

The output of this signal will also be a score from `0.0` to `1.0`, where:

* `0.0` means the writing has more human-like structural variation
* `0.5` means the structural evidence is mixed
* `1.0` means the writing has more AI-like uniformity

Example output:

```json
{
  "signal_name": "stylometric_heuristics",
  "score": 0.67,
  "features": {
    "average_sentence_length": 18.4,
    "sentence_length_variance": 3.2,
    "type_token_ratio": 0.41,
    "punctuation_density": 0.07,
    "repetition_score": 0.22
  }
}
```

Blind spot: stylometric heuristics cannot understand meaning, intent, originality, emotion, or context. A simple poem with repetition might look AI-like according to the heuristics, even if it was written by a human. Also, AI-generated text can be prompted to imitate human variation.

---

## Confidence Scoring and Uncertainty Representation

The system will combine the two detection signals into one final AI-likelihood score. This final score represents how strongly the system believes the submitted text may be AI-generated.

The combined score will use weighted averaging:

```text
combined_score = (0.65 * groq_score) + (0.35 * stylometric_score)
```

Groq receives a slightly higher weight because it can evaluate meaning, tone, and style holistically. Stylometric heuristics receive a lower but still meaningful weight because they provide an independent structural check.

A confidence score of `0.6` means the system sees some signs of AI generation, but the evidence is not strong enough to label the text as likely AI-generated. In this system, a score near the middle means uncertainty, not proof. A `0.6` should produce an uncertain label, while a `0.95` should produce a high-confidence AI label.

Because false positives are especially harmful on a creative platform, the threshold for labeling something as AI-generated will be intentionally strict.

### Label Thresholds

| Combined Score Range | Classification | Meaning                                 |
| -------------------- | -------------- | --------------------------------------- |
| `0.80` to `1.00`     | `likely_ai`    | Strong evidence of AI generation        |
| `0.30` to `0.79`     | `uncertain`    | Mixed evidence or not enough confidence |
| `0.00` to `0.29`     | `likely_human` | Strong evidence of human authorship     |

This means the system does not flip between human and AI at `0.5`. Instead, it has a wide uncertainty range. This is intentional because AI detection is imperfect, and mislabeling a human creator’s work as AI-generated can be harmful.

### Example Score Cases

| Groq Score | Stylometric Score | Combined Score | Final Label    |
| ---------- | ----------------- | -------------- | -------------- |
| `0.90`     | `0.75`            | `0.85`         | `likely_ai`    |
| `0.62`     | `0.51`            | `0.58`         | `uncertain`    |
| `0.18`     | `0.33`            | `0.23`         | `likely_human` |
| `0.85`     | `0.20`            | `0.62`         | `uncertain`    |

The last example shows why uncertainty matters. If Groq says AI but the stylometric signal says human, the system should avoid making a high-confidence claim.

---

## Transparency Label Design

The transparency label is the text shown to readers on the platform. It should communicate the attribution result in plain language without pretending the system is perfect.

The README will include these exact three label variants.

| Label Variant         | Exact Label Text                                                                                                                                                                                                |
| --------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| High-confidence AI    | `"Provenance Guard found strong signs that this piece may have been AI-generated. This label is based on multiple detection signals and should be read as a confidence-based assessment, not absolute proof."`  |
| High-confidence human | `"Provenance Guard found strong signs that this piece was likely written by a human creator. This label is based on multiple detection signals and may still be reviewed if new context is provided."`          |
| Uncertain             | `"Provenance Guard could not confidently determine whether this piece was human-written or AI-generated. The evidence was mixed, so this content is labeled as uncertain rather than making a stronger claim."` |

The wording is intentionally cautious. The high-confidence AI label says “may have been AI-generated” instead of “was AI-generated” because the system should not present detection as absolute proof. The uncertain label clearly explains that mixed evidence does not equal guilt or certainty.

---

## Appeals Workflow

Creators can submit an appeal if they believe their content was misclassified. An appeal can be submitted by the creator associated with the original content or by anyone with the content ID in this project version.

The appeal request will include:

```json
{
  "content_id": "content_001",
  "creator_id": "creator_123",
  "creator_reasoning": "I wrote this poem myself and can provide drafts showing my revision process."
}
```

When an appeal is received, the system will:

1. Validate that the `content_id` exists.
2. Save the creator’s appeal reason.
3. Update the content status to `"under_review"`.
4. Log the appeal alongside the original attribution decision.
5. Return a confirmation response.

Example response:

```json
{
  "appeal_id": "appeal_001",
  "content_id": "content_001",
  "status": "under_review",
  "message": "Your appeal has been received and the content is now under review."
}
```

The system does not need to automatically reclassify appealed content. The purpose of the appeal workflow is to show that creators have a path to contest decisions.

A human reviewer opening the appeal queue would see:

* Content ID
* Creator ID
* Original submitted text or text preview
* Original classification
* Original confidence score
* Groq signal score and reasoning
* Stylometric signal score and features
* Transparency label shown to the user
* Creator’s appeal reason
* Appeal timestamp
* Current status: `"under_review"`

---

## Audit Log Design

Every attribution decision will be recorded in a structured audit log. The audit log is important because it shows what the system decided, what signals contributed to the decision, and whether the decision was appealed.

Each audit log entry should include:

```json
{
  "content_id": "content_001",
  "creator_id": "creator_123",
  "title": "Ocean Poem",
  "text_preview": "I hated the ocean that day...",
  "classification": "uncertain",
  "confidence": 0.58,
  "transparency_label": "Provenance Guard could not confidently determine whether this piece was human-written or AI-generated...",
  "signals": {
    "groq_llm": {
      "score": 0.62,
      "label": "possibly_ai",
      "reasoning": "The text is polished but not conclusive."
    },
    "stylometric_heuristics": {
      "score": 0.51,
      "features": {
        "average_sentence_length": 14.2,
        "sentence_length_variance": 6.8,
        "type_token_ratio": 0.48,
        "punctuation_density": 0.09,
        "repetition_score": 0.12
      }
    }
  },
  "status": "classified",
  "appeal": null,
  "timestamp": "2026-06-26T12:00:00"
}
```

If the content is appealed, the audit log should update or add an appeal record:

```json
{
  "content_id": "content_001",
  "event_type": "appeal_submitted",
  "appeal_id": "appeal_001",
  "appeal_reasoning": "I wrote this myself and can provide draft history.",
  "previous_status": "classified",
  "new_status": "under_review",
  "timestamp": "2026-06-26T12:15:00"
}
```

---

## API Surface

### `POST /submit`

Purpose: Submit text for attribution analysis.

Example request:

```json
{
  "creator_id": "creator_123",
  "title": "Ocean Poem",
  "text": "I hated the ocean that day. Not because of the water, but because everyone kept pretending it was peaceful."
}
```

Example response:

```json
{
  "content_id": "content_001",
  "classification": "uncertain",
  "confidence": 0.58,
  "transparency_label": "Provenance Guard could not confidently determine whether this piece was human-written or AI-generated. The evidence was mixed, so this content is labeled as uncertain rather than making a stronger claim.",
  "signals": {
    "groq_llm": {
      "score": 0.62,
      "label": "possibly_ai"
    },
    "stylometric_heuristics": {
      "score": 0.51,
      "features": {
        "average_sentence_length": 14.2,
        "sentence_length_variance": 6.8,
        "type_token_ratio": 0.48,
        "punctuation_density": 0.09
      }
    }
  },
  "status": "classified"
}
```

---

### `POST /appeal`

Purpose: Submit an appeal for a classification.

Example request:

```json
{
  "content_id": "content_001",
  "creator_id": "creator_123",
  "creator_reasoning": "I wrote this myself and can provide draft history."
}
```

Example response:

```json
{
  "appeal_id": "appeal_001",
  "content_id": "content_001",
  "status": "under_review",
  "message": "Your appeal has been received and the content is now under review."
}
```

---

### `GET /log`

Purpose: Return structured audit log entries.

Example response:

```json
{
  "logs": [
    {
      "content_id": "content_001",
      "classification": "uncertain",
      "confidence": 0.58,
      "status": "under_review"
    }
  ]
}
```

---

### `GET /health`

Purpose: Confirm that the backend is running.

Example response:

```json
{
  "status": "running"
}
```

---

## Anticipated Edge Cases

### Edge Case 1: Repetitive Poetry

A poem may intentionally use repeated words, short sentences, or simple vocabulary for artistic effect. The stylometric heuristic signal might score this as AI-like because it sees repetition and low vocabulary diversity. However, repetition is common in poetry and does not mean the work is AI-generated.

How the system should respond: If only the stylometric signal suggests AI but the Groq signal is less certain, the final classification should likely be uncertain rather than high-confidence AI.

---

### Edge Case 2: Very Polished Human Writing

Some human writers naturally write in a clean, organized, and polished style. Their work may have smooth sentence structure, consistent paragraphs, and formal vocabulary. The Groq signal might interpret this as AI-like, and the stylometric signal may also see uniformity.

How the system should respond: The system should use a strict AI threshold of `0.80` to avoid labeling polished human writing as AI-generated unless both signals strongly agree.

---

### Edge Case 3: Very Short Submissions

A short text, such as a two-line poem or one-paragraph excerpt, may not provide enough information for reliable analysis. Stylometric features like sentence length variance and vocabulary diversity are less meaningful when there are only a few sentences.

How the system should respond: For very short submissions, the system should lower confidence or return uncertain because there is not enough evidence to make a strong attribution claim.

---

### Edge Case 4: AI Text Prompted to Sound Human

AI-generated text can be prompted to include messy punctuation, uneven sentence lengths, personal details, and emotional phrasing. This may trick both the Groq signal and the stylometric signal into scoring the text as human-like.

How the system should respond: The system should acknowledge that classification is confidence-based and not absolute proof. The audit log should preserve signal details so reviewers can inspect the reasoning later.

---

## AI Tool Plan

### Milestone 3: Submission Endpoint and First Signal

For Milestone 3, I will provide the AI tool with these sections:

* Project Overview
* Architecture
* Detection Signals
* API Surface

I will ask the AI tool to generate:

* A Flask app skeleton
* A `POST /submit` endpoint
* Basic input validation for submitted text
* A Groq LLM classification function
* A placeholder response structure that includes `content_id`, `classification`, `confidence`, `transparency_label`, and `signals`

I will verify the output by:

* Running the Flask app locally
* Testing `GET /health`
* Sending a few sample texts to `POST /submit`
* Checking that the Groq signal returns a structured score between `0.0` and `1.0`
* Confirming that empty text produces a helpful error response

---

### Milestone 4: Second Signal and Confidence Scoring

For Milestone 4, I will provide the AI tool with these sections:

* Detection Signals
* Confidence Scoring and Uncertainty Representation
* Anticipated Edge Cases
* Architecture

I will ask the AI tool to generate:

* A stylometric heuristic function
* Feature calculations for sentence length variance, type-token ratio, punctuation density, and repetition
* A scoring function that converts stylometric features into a `0.0` to `1.0` score
* Combined confidence scoring using the formula:

```text
combined_score = (0.65 * groq_score) + (0.35 * stylometric_score)
```

* Classification thresholds:

  * `0.80` to `1.00` = `likely_ai`
  * `0.30` to `0.79` = `uncertain`
  * `0.00` to `0.29` = `likely_human`

I will verify the output by:

* Testing clearly AI-like text, clearly human-like text, and mixed text
* Checking that scores vary meaningfully between inputs
* Confirming that a score around `0.6` returns `uncertain`
* Confirming that the system does not classify content as AI just because the score is slightly above `0.5`
* Testing very short submissions to see whether they return lower confidence or uncertainty

---

### Milestone 5: Production Layer

For Milestone 5, I will provide the AI tool with these sections:

* Transparency Label Design
* Appeals Workflow
* Audit Log Design
* API Surface
* Architecture

I will ask the AI tool to generate:

* Transparency label generation logic
* A `POST /appeal` endpoint
* Status updates from `"classified"` to `"under_review"`
* Structured audit logging for attribution decisions
* Structured audit logging for appeals
* A `GET /log` endpoint
* Rate limiting for `POST /submit`

I will verify the output by:

* Testing all three label variants:

  * high-confidence AI
  * high-confidence human
  * uncertain
* Submitting an appeal and checking that the status changes to `"under_review"`
* Checking that the appeal reason is saved with the original decision
* Calling `GET /log` and confirming that at least three entries are visible
* Testing rate limiting by sending repeated requests to `/submit`
* Confirming the README documents the chosen rate limits and reasoning

---

## Rate Limiting Plan

The `/submit` endpoint will be rate limited to protect the system from spam or abuse.

Initial chosen limit:

```text
10 submissions per minute per IP address
```

Reasoning: On a writing platform, a normal creator is unlikely to submit more than 10 full pieces of writing per minute for attribution analysis. This limit allows normal testing and reasonable use while making it harder for an adversary to flood the system with automated submissions.

If needed, a stricter long-term limit could also be added:

```text
100 submissions per day per IP address
```

This would help prevent large-scale abuse while still allowing legitimate users to test multiple pieces of writing.

---

## Stretch Feature Plan

I will update this planning document before starting any stretch feature.

Possible stretch features:

1. Ensemble detection with a third signal
2. Provenance certificate for verified human creators
3. Analytics dashboard showing detection patterns and appeal rates
4. Multi-modal support for another content type

For now, the required features are the priority.

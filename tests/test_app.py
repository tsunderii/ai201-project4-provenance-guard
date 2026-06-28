import os
import unittest
from unittest.mock import patch

from app import LOG_PATH, app


class ProvenanceGuardTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        app.submissions.clear()
        app.appeals.clear()
        if os.path.exists(LOG_PATH):
            os.remove(LOG_PATH)

    def _submit(self, text, creator_id="test-user-1", remote_addr="127.0.0.1"):
        return self.client.post(
            "/submit",
            json={"text": text, "creator_id": creator_id},
            environ_overrides={"REMOTE_ADDR": remote_addr},
        )

    def test_submit_returns_json_with_expected_fields(self):
        response = self._submit("The sun dipped below the horizon.")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIn("content_id", payload)
        self.assertIn("attribution", payload)
        self.assertIn("confidence", payload)
        self.assertIn("label", payload)
        self.assertIn(
            payload["attribution"], ["likely_ai", "uncertain", "likely_human"]
        )

    def test_appeal_uses_content_id_from_submit(self):
        submit_response = self._submit("The sun dipped below the horizon.")
        content_id = submit_response.get_json()["content_id"]

        appeal_response = self.client.post(
            "/appeal",
            json={
                "content_id": content_id,
                "creator_id": "test-user-1",
                "creator_reasoning": "I wrote this myself.",
            },
            environ_overrides={"REMOTE_ADDR": "127.0.0.2"},
        )

        self.assertEqual(appeal_response.status_code, 200)
        payload = appeal_response.get_json()
        self.assertEqual(payload["content_id"], content_id)
        self.assertEqual(payload["status"], "under_review")

    def test_transparency_label_changes_with_confidence(self):
        with patch(
            "app.groq_llm_signal",
            return_value={"score": 0.2, "label": "likely_human", "reasoning": "short"},
        ), patch(
            "app.stylometric_signal",
            return_value={"score": 0.2, "label": "likely_human", "features": {}},
        ):
            human_response = self._submit(
                "A casual personal note about my day and the weather.",
                creator_id="human-user",
                remote_addr="127.0.0.3",
            )

        self.assertEqual(human_response.status_code, 200)
        self.assertIn("likely written by a human creator", human_response.get_json()["label"])

        with patch(
            "app.groq_llm_signal",
            return_value={"score": 0.9, "label": "likely_ai", "reasoning": "short"},
        ), patch(
            "app.stylometric_signal",
            return_value={"score": 0.9, "label": "likely_ai", "features": {}},
        ):
            ai_response = self._submit(
                "A repetitive uniform passage with little variation.",
                creator_id="ai-user",
                remote_addr="127.0.0.4",
            )

        self.assertEqual(ai_response.status_code, 200)
        self.assertIn("AI-generated", ai_response.get_json()["label"])

    def test_rate_limit_triggers_after_limit_is_exceeded(self):
        with patch(
            "app.groq_llm_signal",
            return_value={"score": 0.2, "label": "likely_human", "reasoning": "short"},
        ), patch(
            "app.stylometric_signal",
            return_value={"score": 0.2, "label": "likely_human", "features": {}},
        ):
            statuses = []
            for index in range(11):
                response = self.client.post(
                    "/submit",
                    json={"text": f"Sample text {index}", "creator_id": "rate-limit-user"},
                    environ_overrides={"REMOTE_ADDR": "127.0.0.5"},
                )
                statuses.append(response.status_code)

        self.assertEqual(statuses.count(200), 10)
        self.assertEqual(statuses.count(429), 1)

    def test_audit_log_contains_structured_entries_for_submissions_and_appeals(self):
        with patch(
            "app.groq_llm_signal",
            return_value={"score": 0.5, "label": "uncertain", "reasoning": "short"},
        ), patch(
            "app.stylometric_signal",
            return_value={"score": 0.5, "label": "uncertain", "features": {}},
        ):
            first_submit = self._submit(
                "First submission for the audit log.",
                creator_id="audit-user",
                remote_addr="127.0.0.6",
            )
            second_submit = self._submit(
                "Second submission for the audit log.",
                creator_id="audit-user",
                remote_addr="127.0.0.7",
            )

        content_id = first_submit.get_json()["content_id"]
        appeal_response = self.client.post(
            "/appeal",
            json={
                "content_id": content_id,
                "creator_id": "audit-user",
                "creator_reasoning": "I wrote this text myself.",
            },
            environ_overrides={"REMOTE_ADDR": "127.0.0.8"},
        )

        self.assertEqual(appeal_response.status_code, 200)
        log_response = self.client.get("/log")
        entries = log_response.get_json()["entries"]

        self.assertGreaterEqual(len(entries), 3)
        self.assertTrue(any(entry["event_type"] == "classification" for entry in entries))
        self.assertTrue(any(entry["event_type"] == "appeal" for entry in entries))

        for entry in entries:
            self.assertIn("event_type", entry)
            self.assertIn("timestamp", entry)

    def test_analytics_dashboard_reports_submission_and_appeal_metrics(self):
        with patch(
            "app.groq_llm_signal",
            return_value={"score": 0.2, "label": "likely_human", "reasoning": "short"},
        ), patch(
            "app.stylometric_signal",
            return_value={"score": 0.2, "label": "likely_human", "features": {}},
        ):
            self._submit("One submission", creator_id="analytics-user", remote_addr="127.0.0.9")
            self._submit("Another submission", creator_id="analytics-user", remote_addr="127.0.0.10")

        content_id = self.client.get("/log").get_json()["entries"][0]["content_id"]
        self.client.post(
            "/appeal",
            json={
                "content_id": content_id,
                "creator_id": "analytics-user",
                "creator_reasoning": "I wrote this myself.",
            },
            environ_overrides={"REMOTE_ADDR": "127.0.0.11"},
        )

        response = self.client.get("/analytics")
        payload = response.get_json()

        self.assertEqual(payload["total_submissions"], 2)
        self.assertEqual(payload["total_appeals"], 1)
        self.assertIn("appeal_rate", payload)
        self.assertIn("attribution_counts", payload)

    def test_submit_supports_non_text_content_types(self):
        response = self.client.post(
            "/submit",
            json={
                "content_type": "image_description",
                "text": "A bright image of a city skyline at sunset.",
                "creator_id": "image-user",
                "creator_verified": True,
            },
            environ_overrides={"REMOTE_ADDR": "127.0.0.12"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["content_type"], "image_description")
        self.assertIn("certificate", payload)


if __name__ == "__main__":
    unittest.main()

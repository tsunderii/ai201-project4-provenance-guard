import unittest

from app import app


class ProvenanceGuardTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        app.submissions.clear()
        app.appeals.clear()

    def test_submit_returns_json_with_expected_fields(self):
        response = self.client.post(
            "/submit",
            json={"text": "The sun dipped below the horizon.", "creator_id": "test-user-1"},
        )

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
        submit_response = self.client.post(
            "/submit",
            json={"text": "The sun dipped below the horizon.", "creator_id": "test-user-1"},
        )
        content_id = submit_response.get_json()["content_id"]

        appeal_response = self.client.post(
            "/appeal",
            json={
                "content_id": content_id,
                "creator_id": "test-user-1",
                "creator_reasoning": "I wrote this myself.",
            },
        )

        self.assertEqual(appeal_response.status_code, 200)
        payload = appeal_response.get_json()
        self.assertEqual(payload["content_id"], content_id)
        self.assertEqual(payload["status"], "under_review")


if __name__ == "__main__":
    unittest.main()

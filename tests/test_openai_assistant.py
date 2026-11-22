import unittest

from openai_assistant import analyze_with_openai


class FakeResponse:
    status_code = 200

    def json(self):
        return {
            "choices": [{
                "message": {"role": "assistant", "content": "سلام! پاسخ آزمایشی."}
            }]
        }

    def close(self):
        pass


class FakeSession:
    def __init__(self):
        self.kwargs = None

    def post(self, url, **kwargs):
        self.kwargs = {"url": url, **kwargs}
        return FakeResponse()


class GroqConversationTests(unittest.TestCase):
    def test_sends_conversation_history_and_returns_persian_text(self):
        session = FakeSession()
        history = [
            {"role": "user", "content": "سلام"},
            {"role": "assistant", "content": "سلام، چطور کمک کنم؟"},
        ]
        answer = analyze_with_openai(
            "gsk-test", "وضعیت بازار چیست؟",
            {"selected_symbol": "BTC_USDT"},
            session=session,
            conversation_history=history,
        )
        self.assertEqual(answer, "سلام! پاسخ آزمایشی.")
        self.assertEqual(session.kwargs["url"],
                         "https://api.groq.com/openai/v1/chat/completions")
        payload = session.kwargs["json"]
        self.assertEqual(payload["model"], "qwen/qwen3.8-27b")
        self.assertEqual(payload["messages"][2:4], history)
        self.assertEqual(payload["messages"][-1], {
            "role": "user", "content": "وضعیت بازار چیست؟"
        })
        self.assertIn("APP CONTEXT", payload["messages"][1]["content"])

    def test_limits_history_to_last_twelve_messages(self):
        session = FakeSession()
        history = [{"role": "user", "content": "U{}".format(i)} for i in range(20)]
        analyze_with_openai(
            "gsk-test", "پیام جدید", {}, session=session,
            conversation_history=history,
        )
        sent = session.kwargs["json"]["messages"]
        self.assertEqual(len(sent), 2 + 12 + 1)
        self.assertEqual(sent[2]["content"], "U8")
        self.assertEqual(sent[-1]["content"], "پیام جدید")

    def test_requires_api_key_and_message(self):
        with self.assertRaises(ValueError):
            analyze_with_openai("", "سلام")
        with self.assertRaises(ValueError):
            analyze_with_openai("gsk-test", "")


if __name__ == "__main__":
    unittest.main()

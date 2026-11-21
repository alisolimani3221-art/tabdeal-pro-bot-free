import unittest

from openai_assistant import analyze_with_openai


class FakeResponse:
    status_code = 200

    def json(self):
        return {
            "output": [{
                "type": "message",
                "content": [{"type": "output_text", "text": "سلام! پاسخ آزمایشی."}],
            }]
        }


class FakeSession:
    def __init__(self):
        self.kwargs = None

    def post(self, url, **kwargs):
        self.kwargs = {"url": url, **kwargs}
        return FakeResponse()


class OpenAIConversationTests(unittest.TestCase):
    def test_sends_conversation_history_and_returns_chatgpt_text(self):
        session = FakeSession()
        history = [
            {"role": "user", "content": "سلام"},
            {"role": "assistant", "content": "سلام، چطور کمک کنم؟"},
        ]
        answer = analyze_with_openai(
            "sk-test", "وضعیت بازار چیست؟",
            {"selected_symbol": "BTC_USDT"},
            session=session,
            conversation_history=history,
        )
        self.assertEqual(answer, "سلام! پاسخ آزمایشی.")
        payload = session.kwargs["json"]
        self.assertEqual(payload["model"], "gpt-6-luna")
        self.assertEqual(payload["input"][1:3], history)
        self.assertEqual(payload["input"][-1], {
            "role": "user", "content": "وضعیت بازار چیست؟"
        })
        self.assertIn("APP CONTEXT", payload["input"][0]["content"])

    def test_limits_history_to_last_six_turn_pairs(self):
        session = FakeSession()
        history = []
        for i in range(20):
            history.append({"role": "user", "content": "U{}".format(i)})
        analyze_with_openai(
            "sk-test", "پیام جدید", {}, session=session,
            conversation_history=history,
        )
        sent = session.kwargs["json"]["input"]
        self.assertEqual(len(sent), 1 + 12 + 1)
        self.assertEqual(sent[1]["content"], "U8")
        self.assertEqual(sent[-1]["content"], "پیام جدید")

    def test_requires_api_key_and_message(self):
        with self.assertRaises(ValueError):
            analyze_with_openai("", "سلام")
        with self.assertRaises(ValueError):
            analyze_with_openai("sk-test", "")


if __name__ == "__main__":
    unittest.main()

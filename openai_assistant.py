"""Minimal read-only OpenAI Responses API client for the Android app."""
import requests

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
OPENAI_MODEL = "gpt-6-luna"


def analyze_with_openai(api_key, question, app_context=None, session=None):
    """Send a bounded diagnostic snapshot and user prompt; never exposes trading controls."""
    key = str(api_key or "").strip()
    prompt = str(question or "").strip()
    if not key:
        raise ValueError("OpenAI API key is required")
    if not prompt:
        raise ValueError("A message is required")
    context = app_context if isinstance(app_context, dict) else {}
    context_text = repr(context)[:12000]
    instructions = (
        "You are a read-only diagnostic assistant embedded in a futures market analyzer. "
        "You may inspect only the supplied app context and answer the user's question. "
        "You cannot change app settings, start or stop engines, place/cancel orders, or trade. "
        "Do not claim to have inspected data that was not supplied. "
        "Explain uncertainty and never promise profit. Respond in Persian when the user writes Persian."
    )
    payload = {
        "model": OPENAI_MODEL,
        "instructions": instructions,
        "input": "APP CONTEXT (read-only):\n{}\n\nUSER REQUEST:\n{}".format(
            context_text, prompt),
        "max_output_tokens": 900
    }
    client = session or requests
    response = client.post(
        OPENAI_RESPONSES_URL,
        headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json"
        },
        json=payload,
        timeout=(12, 55)
    )
    if response.status_code >= 400:
        detail = ""
        try:
            error = response.json().get("error", {})
            detail = error.get("message", "")
        except Exception:
            detail = response.text[:240]
        raise RuntimeError("OpenAI HTTP {}: {}".format(response.status_code, detail[:240]))
    data = response.json()
    parts = []
    for item in data.get("output", []):
        if item.get("type") != "message":
            continue
        for block in item.get("content", []):
            if block.get("type") == "output_text" and block.get("text"):
                parts.append(block["text"])
    answer = "\n".join(parts).strip()
    if not answer:
        raise RuntimeError("OpenAI returned no text output")
    return answer

"""Canned OpenRouter-style responses for tests."""


def chat_completion(text: str) -> dict:
    return {
        "id": "gen-fake",
        "object": "chat.completion",
        "model": "fake",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }

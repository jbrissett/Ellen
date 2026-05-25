"""Synthetic test for the chat model fallback chain.

Mocks the Anthropic SDK so we can:
1. Confirm resolve_model_chain produces the right ordered chain for each preference.
2. Force Sonnet to raise overloaded_error and verify run_chat_turn FALLS BACK
   to Opus (and succeeds), without hitting the real API.
3. Force ALL models in the chain to raise overloaded; verify we hit the final
   exception with no model leftover.

Run: python tools/repro_chat_model_fallback.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import anthropic

from traffic_intake import chat  # type: ignore
from traffic_intake.models import StudyRequest  # type: ignore


def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    safe = lambda s: s.encode("ascii", "replace").decode("ascii") if isinstance(s, str) else s
    print(f"  {status}  {safe(label)}  {safe(detail)}")
    return condition


def make_overloaded_error(model_id: str) -> anthropic.APIStatusError:
    """Build a realistic APIStatusError that _retryable_api_error will accept."""
    response = MagicMock()
    response.status_code = 529
    response.headers = {}
    response.text = '{"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}'
    response.json = lambda: {
        "type": "error",
        "error": {"type": "overloaded_error", "message": "Overloaded"},
    }
    body = response.json()
    try:
        return anthropic.APIStatusError("Overloaded", response=response, body=body)
    except TypeError:
        # SDK version difference — try positional only
        return anthropic.APIStatusError("Overloaded", response=response)


def make_streaming_success(model_id: str):
    """Build a mock that yields a small text stream and a final message
    containing a single text block (no tool_use), ending the chat turn cleanly.
    """
    final_message = SimpleNamespace(
        content=[
            SimpleNamespace(
                type="text",
                text="ok",
                model_dump=lambda: {"type": "text", "text": "ok"},
            )
        ]
    )

    class _StreamCtx:
        def __init__(self):
            self.text_stream = iter(["ok"])

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get_final_message(self):
            return final_message

    return _StreamCtx()


def main() -> int:
    passed = 0
    total = 0

    # ---- Resolver checks ----
    total += 1
    if check(
        "resolve_model_chain('auto') = [sonnet, opus, haiku]",
        chat.resolve_model_chain("auto") == ["claude-sonnet-4-6", "claude-opus-4-7", "claude-haiku-4-5-20251001"],
        "",
    ):
        passed += 1

    for key, expected in [
        ("sonnet", ["claude-sonnet-4-6"]),
        ("opus",   ["claude-opus-4-7"]),
        ("haiku",  ["claude-haiku-4-5-20251001"]),
    ]:
        total += 1
        if check(
            f"resolve_model_chain({key!r}) = single-model chain",
            chat.resolve_model_chain(key) == expected,
            f"got {chat.resolve_model_chain(key)}",
        ):
            passed += 1

    total += 1
    if check(
        "resolve_model_chain('garbage') falls back to auto chain",
        chat.resolve_model_chain("garbage") == chat.MODELS["auto"],
        "",
    ):
        passed += 1

    # ---- Fallback success case ----
    # Sonnet raises overloaded on every retry; Opus succeeds on first attempt.
    print("\nFallback-after-overload simulation:")
    sonnet_calls = []
    opus_calls = []

    def fake_stream(*, model, messages, **kw):
        if model == "claude-sonnet-4-6":
            sonnet_calls.append(1)
            raise make_overloaded_error(model)
        if model == "claude-opus-4-7":
            opus_calls.append(1)
            return make_streaming_success(model)
        raise RuntimeError(f"unexpected model: {model}")

    client_mock = MagicMock()
    client_mock.messages.stream = fake_stream

    state = StudyRequest(
        email_subject="x", email_from="x@x", email_to="y@y", locations=[],
    )
    text_chunks = []
    tool_results = []

    # Speed up the test — patch time.sleep so we don't actually wait the backoff.
    with patch("traffic_intake.chat.Anthropic", return_value=client_mock), \
         patch("traffic_intake.chat.time.sleep", return_value=None), \
         patch("traffic_intake.chat.get_api_key", return_value="fake-key"):
        chat.run_chat_turn(
            "hi", history=[], state=state,
            on_text_delta=text_chunks.append,
            on_tool_result=lambda n, r: tool_results.append((n, r)),
            model_chain=["claude-sonnet-4-6", "claude-opus-4-7", "claude-haiku-4-5-20251001"],
        )

    total += 1
    if check(
        "Sonnet was tried multiple times then exhausted retries",
        len(sonnet_calls) >= 2,
        f"sonnet calls: {len(sonnet_calls)}",
    ):
        passed += 1

    total += 1
    if check(
        "Fallback to Opus happened and succeeded",
        len(opus_calls) == 1,
        f"opus calls: {len(opus_calls)}",
    ):
        passed += 1

    total += 1
    full_text = "".join(text_chunks)
    if check(
        "User saw 'busy' retry status messages emitted to chat panel",
        "busy" in full_text.lower() or "retrying" in full_text.lower(),
        f"saw: {full_text[:200]!r}",
    ):
        passed += 1

    total += 1
    if check(
        "User saw 'switching' message when chain fell back to Opus",
        "switching" in full_text.lower() and "Opus" in full_text,
        f"saw: {full_text[-200:]!r}",
    ):
        passed += 1

    # ---- Full chain exhaustion: every model fails ----
    print("\nAll-models-exhausted simulation:")
    def fake_stream_all_fail(*, model, messages, **kw):
        raise make_overloaded_error(model)

    client_mock2 = MagicMock()
    client_mock2.messages.stream = fake_stream_all_fail
    state2 = StudyRequest(
        email_subject="x", email_from="x@x", email_to="y@y", locations=[],
    )

    raised = None
    with patch("traffic_intake.chat.Anthropic", return_value=client_mock2), \
         patch("traffic_intake.chat.time.sleep", return_value=None), \
         patch("traffic_intake.chat.get_api_key", return_value="fake-key"):
        try:
            chat.run_chat_turn(
                "hi", history=[], state=state2,
                on_text_delta=lambda s: None,
                on_tool_result=lambda n, r: None,
                model_chain=chat.MODELS["auto"],
            )
        except BaseException as exc:
            raised = exc

    total += 1
    if check(
        "When all models exhausted, raises the underlying error",
        raised is not None and isinstance(raised, anthropic.APIStatusError),
        f"raised: {type(raised).__name__ if raised else None}",
    ):
        passed += 1

    # ---- Single-model preference: no fallback to other models ----
    print("\nSingle-model-preference simulation (sonnet-only, no fallback):")
    sonnet_calls_solo = []
    opus_calls_solo = []

    def fake_stream_solo(*, model, messages, **kw):
        if model == "claude-sonnet-4-6":
            sonnet_calls_solo.append(1)
            raise make_overloaded_error(model)
        opus_calls_solo.append(1)
        return make_streaming_success(model)

    client_mock3 = MagicMock()
    client_mock3.messages.stream = fake_stream_solo
    state3 = StudyRequest(email_subject="x", email_from="x@x", email_to="y@y", locations=[])

    raised_solo = None
    with patch("traffic_intake.chat.Anthropic", return_value=client_mock3), \
         patch("traffic_intake.chat.time.sleep", return_value=None), \
         patch("traffic_intake.chat.get_api_key", return_value="fake-key"):
        try:
            chat.run_chat_turn(
                "hi", history=[], state=state3,
                on_text_delta=lambda s: None,
                on_tool_result=lambda n, r: None,
                model_chain=chat.resolve_model_chain("sonnet"),
            )
        except BaseException as exc:
            raised_solo = exc

    total += 1
    if check(
        "Sonnet-only chain raises on exhaustion; does NOT fall back to Opus",
        raised_solo is not None and len(opus_calls_solo) == 0,
        f"raised: {type(raised_solo).__name__ if raised_solo else None}, opus_calls={len(opus_calls_solo)}",
    ):
        passed += 1

    print(f"\nResult: {passed}/{total} passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())

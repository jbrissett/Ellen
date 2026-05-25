"""Claude computer-use driver — operates a Playwright browser via vision.

Used where selector-based automation is unreliable (qchub's Angular form with
duplicate-id inputs, variable cascade timing, and a re-rendering modal).
Computer-use sees screenshots, picks coordinates, and types like a human, so
it sidesteps the framework-specific weirdness.

Pattern: Claude calls the `computer` tool (screenshot, left_click, type, key,
etc.) — we execute each action via Playwright on the page we control, then
return a fresh screenshot for the next turn. Two extra tools `task_complete`
and `task_failed` let Claude signal end-of-job vs giving up.

Cost: ~$0.15–0.50 per modal fill depending on turn count. Speed: 1–3 min.
"""
from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field
from typing import Callable, Optional

from anthropic import Anthropic
from playwright.sync_api import Page

from .config import get_api_key

log = logging.getLogger(__name__)

# Computer-use tool spec. The tool-type AND beta-header come paired per model:
#   computer_20251124 + computer-use-2025-11-24 → Opus 4.7/4.6/4.5, Sonnet 4.6  (current)
#   computer_20250124 + computer-use-2025-01-24 → Sonnet 4.5/4, Opus 4.1/4, Haiku 4.5, Sonnet 3.7
# `display_width_px`/`display_height_px` MUST match the actual viewport.
COMPUTER_TOOL_NAME = "computer"
COMPUTER_TOOL_TYPE = "computer_20251124"
COMPUTER_USE_BETA = "computer-use-2025-11-24"

# Custom tools Claude can call to signal completion / failure.
TASK_COMPLETE_TOOL = {
    "name": "task_complete",
    "description": "Call this when the requested task has been successfully completed.",
    "input_schema": {
        "type": "object",
        "properties": {"summary": {"type": "string", "description": "Brief description of what was done."}},
        "required": ["summary"],
    },
}

TASK_FAILED_TOOL = {
    "name": "task_failed",
    "description": "Call this when the task cannot be completed (UI in unexpected state, missing data, etc.).",
    "input_schema": {
        "type": "object",
        "properties": {"reason": {"type": "string", "description": "What blocked completion."}},
        "required": ["reason"],
    },
}


class ComputerUseError(Exception):
    pass


class TaskFailed(ComputerUseError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass
class DriveResult:
    summary: str
    turns: int


def drive_task(
    page: Page,
    *,
    goal: str,
    system_prompt: str = "",
    model: str = "claude-sonnet-4-6",
    max_turns: int = 30,
    api_key: Optional[str] = None,
    progress: Optional[Callable[[str], None]] = None,
) -> DriveResult:
    """Drive the given Playwright `page` toward `goal` using Claude computer-use.

    The model gets:
      - The system_prompt (operator instructions, conventions)
      - The goal (specific task)
      - An initial screenshot
      - Loop: each tool call executes on the page; result = new screenshot

    Raises TaskFailed if Claude calls `task_failed`, ComputerUseError on
    other issues (turn limit, etc).
    """
    cb = progress or (lambda s: None)

    # Match the computer tool's display size to the actual viewport so Claude's
    # coordinates land correctly.
    viewport = page.viewport_size or {"width": 1400, "height": 900}
    computer_tool = {
        "type": COMPUTER_TOOL_TYPE,
        "name": COMPUTER_TOOL_NAME,
        "display_width_px": viewport["width"],
        "display_height_px": viewport["height"],
        "display_number": 1,
    }

    client = Anthropic(api_key=api_key or get_api_key())

    # Initial user message with the goal + first screenshot
    initial_screenshot = _capture(page)
    messages: list[dict] = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": goal},
                _image_block(initial_screenshot),
            ],
        }
    ]

    final_summary: Optional[str] = None
    for turn in range(1, max_turns + 1):
        cb(f"computer-use turn {turn}/{max_turns}")
        response = client.beta.messages.create(
            model=model,
            max_tokens=4096,
            system=system_prompt or _DEFAULT_SYSTEM,
            tools=[computer_tool, TASK_COMPLETE_TOOL, TASK_FAILED_TOOL],
            messages=messages,
            betas=[COMPUTER_USE_BETA],
        )

        # Append the assistant response to history
        assistant_content = [b.model_dump() for b in response.content]
        messages.append({"role": "assistant", "content": assistant_content})

        # Walk the content blocks and execute any tool calls
        tool_results: list[dict] = []
        for block in response.content:
            if block.type == "text" and block.text.strip():
                cb(f"(claude: {block.text.strip()[:150]})")
            elif block.type == "tool_use":
                if block.name == "task_complete":
                    final_summary = block.input.get("summary", "(no summary)")
                    cb(f"✓ task_complete: {final_summary}")
                    return DriveResult(summary=final_summary, turns=turn)
                if block.name == "task_failed":
                    reason = block.input.get("reason", "(no reason)")
                    cb(f"✗ task_failed: {reason}")
                    raise TaskFailed(reason)
                if block.name == COMPUTER_TOOL_NAME:
                    result_blocks = _execute_action(page, block.input, cb)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_blocks,
                    })

        if response.stop_reason == "end_turn" and not tool_results:
            # Model ended without calling task_complete — treat as soft failure
            raise ComputerUseError(
                "Claude ended its turn without calling task_complete or task_failed. "
                "Likely confusion about the task."
            )

        if tool_results:
            messages.append({"role": "user", "content": tool_results})

    raise ComputerUseError(f"Task didn't complete within {max_turns} turns.")


def _execute_action(page: Page, action_input: dict, cb: Callable[[str], None]) -> list[dict]:
    """Run a single computer-use action via Playwright. Always returns a tool_result
    content list ending with a fresh screenshot.
    """
    action = action_input.get("action", "")
    cb(f"action: {action} {action_input.get('coordinate') or action_input.get('text') or ''}")
    try:
        if action == "screenshot":
            pass  # we return a screenshot below regardless
        elif action == "left_click":
            x, y = action_input["coordinate"]
            page.mouse.click(x, y)
            page.wait_for_timeout(400)
        elif action == "right_click":
            x, y = action_input["coordinate"]
            page.mouse.click(x, y, button="right")
            page.wait_for_timeout(400)
        elif action == "middle_click":
            x, y = action_input["coordinate"]
            page.mouse.click(x, y, button="middle")
            page.wait_for_timeout(400)
        elif action == "double_click":
            x, y = action_input["coordinate"]
            page.mouse.dblclick(x, y)
            page.wait_for_timeout(400)
        elif action == "triple_click":
            x, y = action_input["coordinate"]
            page.mouse.click(x, y, click_count=3)
            page.wait_for_timeout(400)
        elif action == "mouse_move":
            x, y = action_input["coordinate"]
            page.mouse.move(x, y)
        elif action == "left_click_drag":
            sx, sy = action_input["start_coordinate"]
            ex, ey = action_input["coordinate"]
            page.mouse.move(sx, sy)
            page.mouse.down()
            page.mouse.move(ex, ey, steps=12)
            page.mouse.up()
            page.wait_for_timeout(400)
        elif action == "type":
            text = action_input.get("text", "")
            page.keyboard.type(text, delay=15)
            page.wait_for_timeout(200)
        elif action == "key":
            key = _translate_key(action_input.get("text", ""))
            page.keyboard.press(key)
            page.wait_for_timeout(200)
        elif action == "hold_key":
            key = _translate_key(action_input.get("text", ""))
            duration = float(action_input.get("duration", 1.0))
            page.keyboard.down(key)
            page.wait_for_timeout(int(duration * 1000))
            page.keyboard.up(key)
        elif action == "wait":
            duration = float(action_input.get("duration", 1.0))
            page.wait_for_timeout(int(duration * 1000))
        elif action == "scroll":
            x, y = action_input.get("coordinate", [viewport_center(page)[0], viewport_center(page)[1]])
            direction = action_input.get("scroll_direction", "down")
            amount = int(action_input.get("scroll_amount", 3))
            dx, dy = 0, 0
            if direction == "down":
                dy = amount * 100
            elif direction == "up":
                dy = -amount * 100
            elif direction == "right":
                dx = amount * 100
            elif direction == "left":
                dx = -amount * 100
            page.mouse.move(x, y)
            page.mouse.wheel(dx, dy)
            page.wait_for_timeout(400)
        elif action == "cursor_position":
            pass  # we just return a screenshot; coordinate read isn't supported by Playwright directly
        else:
            cb(f"(unknown action {action!r} — returning screenshot)")
    except Exception as exc:
        cb(f"(action {action} failed: {exc})")

    return [_image_block(_capture(page))]


def _capture(page: Page) -> str:
    png_bytes = page.screenshot(type="png", full_page=False)
    return base64.standard_b64encode(png_bytes).decode("ascii")


def _image_block(b64: str) -> dict:
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": b64},
    }


def viewport_center(page: Page) -> tuple[int, int]:
    vp = page.viewport_size or {"width": 1400, "height": 900}
    return vp["width"] // 2, vp["height"] // 2


_KEY_MAP = {
    "Return": "Enter",
    "Enter": "Enter",
    "Tab": "Tab",
    "Escape": "Escape",
    "Backspace": "Backspace",
    "Delete": "Delete",
    "Home": "Home",
    "End": "End",
    "PageUp": "PageUp",
    "PageDown": "PageDown",
    "Up": "ArrowUp",
    "Down": "ArrowDown",
    "Left": "ArrowLeft",
    "Right": "ArrowRight",
}


def _translate_key(text: str) -> str:
    """Translate xdotool-style key names (used by Claude's computer-use) into
    Playwright's key names. Modifier+key chords pass through as-is.
    """
    if "+" in text:
        # Chord like "ctrl+a" — Playwright understands "Control+a"
        parts = [p.strip() for p in text.split("+")]
        translated = []
        for p in parts:
            low = p.lower()
            if low in ("ctrl", "control"):
                translated.append("Control")
            elif low == "alt":
                translated.append("Alt")
            elif low == "shift":
                translated.append("Shift")
            elif low in ("super", "meta", "cmd", "win"):
                translated.append("Meta")
            else:
                translated.append(_KEY_MAP.get(p, p))
        return "+".join(translated)
    return _KEY_MAP.get(text, text)


_DEFAULT_SYSTEM = """\
You are operating a Microsoft Edge browser via vision + tool calls to complete \
a specific task on a web application.

You have:
- A screenshot tool to see the current state of the page
- Mouse tools (left_click, double_click, etc.) — use these to interact with form fields, dropdowns, buttons
- A type tool to enter text in the focused field
- A key tool for special keys (Tab, Enter, Escape, Backspace, etc.)
- task_complete — call when the task is done
- task_failed — call if you can't complete the task

Conventions:
- Be efficient: don't take a screenshot if you don't need to, and don't perform unnecessary actions
- For dropdowns: click the dropdown to open it, then click the option you want
- For text inputs: click the field first to focus, then type
- If a dropdown is filtered or searchable, you may type to filter then click the matching option
- Wait briefly (1-2 seconds via a small action) if a page change is in progress before re-screenshotting
- Cascade dropdowns (where one field's value populates another) may take several seconds to fire — wait and re-screenshot if a dependent field is empty

When complete, call task_complete with a brief summary. If blocked, call task_failed with a clear reason.
"""

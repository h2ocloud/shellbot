"""Regression tests for Codex user-message normalization and pending matching."""

import json
import subprocess
from pathlib import Path

import agent_status as ag


ROOT = Path(__file__).resolve().parent


def check(name, condition, detail=None):
    if not condition:
        raise AssertionError(f"{name}: {detail!r}")
    print(f"PASS {name}")


def event(message, *, images=None, local_images=None, text_elements=None):
    payload = {"type": "user_message", "message": message}
    if images is not None:
        payload["images"] = images
    if local_images is not None:
        payload["local_images"] = local_images
    if text_elements is not None:
        payload["text_elements"] = text_elements
    return {"type": "event_msg", "timestamp": "2026-11-24T00:00:00Z", "payload": payload}


cases = [
    ("ordinary message", event("示範：一般訊息"), "示範：一般訊息"),
    ("message while a turn is active", event("示範：工作中送入的訊息"), "示範：工作中送入的訊息"),
    ("image message", event("[Image #7]示範：帶圖訊息", local_images=["image"], text_elements=[{}]),
     "[Image #7]示範：帶圖訊息"),
    ("long multiline message", event("示範：第一行\n第二行\n第三行"),
     "示範：第一行\n第二行\n第三行"),
]

for name, record, expected in cases:
    normalized = ag._norm_codex(record)
    check(name, normalized and normalized["kind"] == "user_msg"
          and normalized["text"] == expected, normalized)

# response_item role=user is an internal input assembly. The following event_msg
# carries the canonical user message, so it must not become a second bubble.
internal_input = {
    "type": "response_item",
    "timestamp": "2026-11-24T00:00:00Z",
    "payload": {
        "type": "message",
        "role": "user",
        "content": [
            {"type": "input_text", "text": "示範：內部組裝文字"},
            {"type": "input_image", "image_url": "data:image/png;base64,示範"},
        ],
    },
}
check("internal response_item user input is ignored", ag._norm_codex(internal_input) is None)

# Future event_msg payloads may carry blocks instead of a string; input_text is
# plain text and input_image has no text, so only the former should be returned.
block_message = event([
    {"type": "input_text", "text": "示範：區塊文字"},
    {"type": "input_image", "image_url": "data:image/png;base64,示範"},
])
normalized = ag._norm_codex(block_message)
check("input_text blocks are normalized", normalized
      and normalized["text"] == "示範：區塊文字", normalized)


node_script = r"""
const R = require('./web/chat.js');
const tests = [
  ['示範：一般訊息', '示範：一般訊息'],
  ['示範：工作中送入的訊息', '示範：工作中送入的訊息'],
  ['[Image #7]示範：帶圖訊息', '示範：帶圖訊息'],
  ['示範：第一行 第二行 第三行', '示範：第一行\n\n第二行\n第三行'],
];
for (const [recorded, sent] of tests) {
  if (!R.sameWords(recorded, sent)) process.exit(1);
}
if (R.sameWords('示範：不同訊息', '示範：一般訊息')) process.exit(1);
"""
result = subprocess.run(["node", "-e", node_script], cwd=ROOT, text=True,
                        capture_output=True)
check("chat.js sameWords matches normalized records", result.returncode == 0,
      result.stderr or result.stdout)

print("Codex pending tests passed")

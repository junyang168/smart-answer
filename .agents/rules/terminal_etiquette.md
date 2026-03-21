---
description: Terminal Output Quota Protection Rule
---

# Terminal Usage and Etiquette

Whenever running commands that output repetitive progress bars, loading indicators, or massive logs (e.g., MoviePy rendering, FFmpeg processing, Docker builds, heavy loops, or npm module installations), you MUST protect the user's conversational token quota.

**Strict Requirements:**
1. **Never run noisy loops synchronously**: Do NOT allow thousands of lines of terminal output to stream into your AI context window.
2. **Use Background Tasks**: If a command is expected to take a long time or produce a lot of terminal output, you must set `WaitMsBeforeAsync` in the `run_command` tool to a small value (e.g., 5000ms or 8000ms) so that it detaches and runs efficiently in the background without spamming the chat history.
3. **Redirect Output if needed**: If you must wait for synchronous completion, redirect the noisy `stdout` into a file or `/dev/null` (e.g., `> output.log 2>&1`). Only capture the final result or error.

Adhering to this ensures the user's Token Quota and Context Window are never unnecessarily consumed by garbage terminal output.

"""Exercise script: Alpha4Gate command panel interaction.

Submits a command via the Command Panel, waits for the UI to update,
and verifies the command appears in the history feed.

Loaded by capture_evidence.py via importlib — must export:
    async def run(page: Page) -> None
"""

from __future__ import annotations

from playwright.async_api import Page


async def run(page: Page) -> None:
    """Type a command, click Send, verify it appears in history."""
    # Wait for the command input to be visible (panel may still be loading).
    input_sel = 'input[placeholder*="Type a command"]'
    await page.wait_for_selector(input_sel, timeout=10_000)

    # Type a command into the input field.
    await page.fill(input_sel, "build stalkers")

    # Click Send.
    await page.click("button:has-text('Send')")

    # Wait for the command to appear in the history list.
    # The history list renders <ul class="command-history-list"> with <li> entries.
    # A successful submit replaces "No commands yet." with at least one entry.
    await page.wait_for_selector(".command-history-list li", timeout=10_000)

    # Give WebSocket events time to update status badges (queued → executed).
    await page.wait_for_timeout(3000)

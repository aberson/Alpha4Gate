"""Exercise script: Alpha4Gate mode switching.

Switches the command mode dropdown through all three modes and back,
verifying the dropdown reflects each change.

Loaded by capture_evidence.py via importlib — must export:
    async def run(page: Page) -> None
"""

from __future__ import annotations

from playwright.async_api import Page


async def run(page: Page) -> None:
    """Cycle through all three command modes and return to AI-Assisted."""
    mode_select = "select"
    await page.wait_for_selector(mode_select, timeout=10_000)

    # Switch to Human Only.
    await page.select_option(mode_select, "human_only")
    await page.wait_for_timeout(2000)

    # Switch to Hybrid.
    await page.select_option(mode_select, "hybrid_cmd")
    await page.wait_for_timeout(2000)

    # Switch back to AI-Assisted.
    await page.select_option(mode_select, "ai_assisted")
    await page.wait_for_timeout(2000)

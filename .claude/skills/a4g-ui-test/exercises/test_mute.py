"""Exercise script: Alpha4Gate mute toggle.

Clicks the Mute Claude button twice (mute then unmute) and verifies the
button label changes between "Mute Claude" and "Claude Muted".

Loaded by capture_evidence.py via importlib — must export:
    async def run(page: Page) -> None
"""

from __future__ import annotations

from playwright.async_api import Page, expect


async def run(page: Page) -> None:
    """Toggle mute on and off, verify button label changes."""
    mute_btn = page.locator("button.mute-btn")
    await mute_btn.wait_for(timeout=10_000)

    # Verify initial state is unmuted.
    await expect(mute_btn).to_have_text("Mute Claude")

    # Click to mute.
    await mute_btn.click()
    await page.wait_for_timeout(1000)
    await expect(mute_btn).to_have_text("Claude Muted")

    # Click to unmute.
    await mute_btn.click()
    await page.wait_for_timeout(1000)
    await expect(mute_btn).to_have_text("Mute Claude")

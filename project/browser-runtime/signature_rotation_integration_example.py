# signature_rotation_integration_example.py
# Example showing how to wire signature_rotation.py into the
# existing hardened_orchestrator.py + StickyProxyResolver.
#
# This is REFERENCE code - it doesn't run standalone, it shows
# the integration points. Copy these snippets into your orchestrator.

import asyncio
from signature_rotation import (
    SignatureRotator,
    ProxyRotator,
    HumanizedSession,
    PersonaTimingProfile,
    install_signature_patches,
    create_default_profile,
    ActionType,
)


# ============================================================
# EXAMPLE 1: Initialize rotator and proxy rotator
# ============================================================
def init_rotation_layer(persona_id: str, timezone_hours: float,
                        sticky_resolver):
    """
    Called once per session start. Returns a dict of handles.
    """
    rotator = SignatureRotator()
    proxy_rotator = ProxyRotator(sticky_resolver, max_session_minutes=25)
    profile = create_default_profile(persona_id, timezone_hours)
    session = HumanizedSession(profile, proxy_rotator, rng_seed=None)

    return {
        "rotator": rotator,
        "proxy_rotator": proxy_rotator,
        "profile": profile,
        "session": session,
    }


# ============================================================
# EXAMPLE 2: Use in page setup (Playwright)
# ============================================================
async def setup_page(page, persona_id: str, rotator: SignatureRotator):
    """
    Install anti-detect patches BEFORE first navigation.
    Call this immediately after page creation.
    """
    session_id = await install_signature_patches(page, rotator, persona_id)
    return session_id


# ============================================================
# EXAMPLE 3: Main action loop
# ============================================================
async def action_loop(page, session: HumanizedSession,
                      urls: list, max_actions: int = 50):
    """
    Example of a humanized main loop. Real usage would have
    domain-specific action handlers.
    """
    action_count = 0

    while action_count < max_actions and not session.should_terminate():
        # Check if persona is in active hours; if not, idle
        if not session.is_active_hour():
            await asyncio.sleep(60)  # Sleep 1 min, re-check
            continue

        # Pick next action
        action = session.next_action()

        if action == ActionType.NAVIGATE:
            if urls:
                url = urls[action_count % len(urls)]
                await page.goto(url, wait_until="domcontentloaded")

        elif action == ActionType.CLICK:
            # Pick a random clickable element
            try:
                await page.click("a, button", timeout=2000)
            except Exception:
                pass  # No clickable, treat as idle

        elif action == ActionType.SCROLL:
            await page.mouse.wheel(0, 300)

        elif action == ActionType.IDLE:
            pass  # Just wait, do nothing

        elif action == ActionType.BACK:
            try:
                await page.go_back()
            except Exception:
                pass

        elif action == ActionType.HOVER:
            try:
                await page.hover("a, button", timeout=2000)
            except Exception:
                pass

        # Wait a log-normal delay
        delay = await session.wait()
        action_count += 1


# ============================================================
# EXAMPLE 4: Force proxy rotation on detection
# ============================================================
async def handle_detection(page, proxy_rotator: ProxyRotator,
                            profile: PersonaTimingProfile):
    """
    If a bot detection page is encountered (CAPTCHA, block page),
    rotate proxy and persona nonce.
    """
    new_proxy = proxy_rotator.force_rotate(profile)
    # Re-launch browser with new proxy
    # (caller handles this - the proxy_rotator just provides new creds)
    return new_proxy

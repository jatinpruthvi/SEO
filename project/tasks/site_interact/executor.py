"""
DC-38 (Pass 38): Site interaction task plugin.

Handles dynamic website interaction, logging in, and
chatting with AI-powered responses. This is the "active
persona" task that makes the account look real by
performing realistic user actions.

Task flow:
1. Navigate to target site
2. Log in with persona credentials
3. Browse content naturally
4. Engage in chat conversations
5. Perform realistic user actions
"""
# executor.py - Day 2: Dynamic Interaction, Logging-in, & Chatting Task Plugin

import asyncio
# C4 fix (Pass 15): Removed dead `import random` — was imported but
# never used in this file.
from loguru import logger  # C4 fix (Pass 15): moved to module top so
                            # it's available in except handlers below
                            # (the previous code imported logger INSIDE
                            # the except block, so a NameError in the
                            # logger import itself would be silently
                            # swallowed by the bare except Exception).
from tasks.base_task import BaseTask
from playwright.async_api import Page, BrowserContext
from ai_bridge import AIBridge
from keyboard_emulator import human_type_words
from humanized_movement import move_mouse_like_human
from hci_reading_model import HCIReadingModel
from dynamic_pacing import DynamicPacingEngine

class TaskExecutor(BaseTask):
    def __init__(self):
        self.pacing = DynamicPacingEngine()

    async def run(self, page: Page, context: BrowserContext, ai_bridge: AIBridge, task_payload: dict) -> bool:
        """
        Loads the validated persistent session cookies, logs into the active site,
        and uses the AI Bridge to generate and post conversational chat messages naturally.
        """
        chat_box_selector = task_payload.get("chat_box_selector", "textarea[name='chat']")
        target_topic = task_payload.get("topic", "Explain the benefits of eBPF socket monitoring.")
        
        # Resolve dynamic biometric helpers matching the persona_id context
        persona_id = getattr(context, "persona_id", "generic_bot")
        hci = HCIReadingModel(persona_id)

        # 1. Query the AI Bridge to generate a unique, contextually relevant chat message
        prompt = f"Write an interesting, conversational, 10-word chat message about: {target_topic}"
        chat_message = await ai_bridge.generate_text(prompt, system_prompt="You are an expert systems engineer chatting in a forum.")
        
        if not chat_message:
            chat_message = "eBPF socket monitoring is fantastic for enforcing container egress firewalls."
            
        # 2. Emulate an organic reading session on the target interactive page before chatting
        await hci.execute_saccadic_scroll(page)
        await self.pacing.sleep_human_delay(3.5, page=page)

        # 3. Locate chat area, scroll into view, focus, and target using Fitts's Law targeting
        try:
            await hci.move_with_fitts_overshoot(page, chat_box_selector)

            # 4. Perform humanized typewriter inputs
            await human_type_words(page, chat_box_selector, chat_message)
            await self.pacing.sleep_human_delay(1.5, page=page)

            # Press enter to submit naturally
            await page.keyboard.press("Enter")
            await page.wait_for_load_state("networkidle")
            await self.pacing.sleep_human_delay(2.5, page=page)
            return True
        except Exception as e:
            # C4 fix (Pass 15): logger is now imported at module top,
            # so a failure in the logger import itself (rare) won't
            # be silently swallowed by the bare except.
            logger.error(f"Interact Task: Failed humanized chat interaction on element '{chat_box_selector}': {e}")
            return False

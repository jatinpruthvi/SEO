# base_task.py - Abstract Base Task Interface

from abc import ABC, abstractmethod
from playwright.async_api import Page, BrowserContext
from ai_bridge import AIBridge

class BaseTask(ABC):
    @abstractmethod
    async def run(self, page: Page, context: BrowserContext, ai_bridge: AIBridge, task_payload: dict) -> bool:
        """
        Executes the specialized task logic.
        Every task plugin MUST implement this method.
        """
        pass

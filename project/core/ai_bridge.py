# ai_bridge.py - Unified AI Bridge Engine (Supports OpenAI, Anthropic, and local Ollama)

import os
import httpx
from loguru import logger

class AIBridge:
    def __init__(self, provider: str = None, api_key: str = None, model: str = None):
        """
        Provides a single, pluggable interface to query multiple LLMs natively.
        Automatically falls back to secure environment variables if not passed explicitly.
        """
        self.provider = provider or os.getenv("LLM_PROVIDER", "ollama")
        self.api_key = api_key or os.getenv("LLM_API_KEY", "")
        self.model = model or os.getenv("LLM_MODEL", "llama3")

    def _get_hardened_proxies(self) -> dict:
        """
        1.6 DNS Leak Protection: Resolves the upstream residential proxy and converts 
        socks5:// to socks5h:// to enforce remote name resolution and prevent system DNS leaks.
        """
        resolved_proxy = os.getenv("UPSTREAM_RES_PROXY")
        if resolved_proxy:
            if resolved_proxy.startswith("socks5://"):
                resolved_proxy = resolved_proxy.replace("socks5://", "socks5h://")
            return {
                "http://": resolved_proxy,
                "https://": resolved_proxy
            }
        return None

    async def generate_text(self, prompt: str, system_prompt: str = "You are a helpful assistant.") -> str:
        """Standardized interface to query multiple LLMs natively."""
        logger.info(f"AI Bridge: Sending completion request via {self.provider} ({self.model})")
        
        # Enforce remote SOCKS5 DNS proxies strictly for external WAN queries
        proxies = self._get_hardened_proxies()
        
        try:
            if self.provider == "openai":
                async with httpx.AsyncClient(proxies=proxies, timeout=30.0) as client:
                    headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
                    payload = {
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": prompt}
                        ]
                    }
                    response = await client.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload)
                    return response.json()["choices"][0]["message"]["content"].strip()

            elif self.provider == "anthropic":
                async with httpx.AsyncClient(proxies=proxies, timeout=30.0) as client:
                    headers = {
                        "x-api-key": self.api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json"
                    }
                    payload = {
                        "model": self.model,
                        "max_tokens": 1024,
                        "system": system_prompt,
                        "messages": [{"role": "user", "content": prompt}]
                    }
                    response = await client.post("https://api.anthropic.com/v1/messages", headers=headers, json=payload)
                    return response.json()["content"][0]["text"].strip()

            elif self.provider == "ollama":
                # Local zero-cost fallback running natively on the OCI VM
                async with httpx.AsyncClient(timeout=30.0) as client:
                    payload = {
                        "model": self.model,
                        "system": system_prompt,
                        "prompt": prompt,
                        "stream": False
                    }
                    response = await client.post("http://localhost:11434/api/generate", json=payload)
                    return response.json()["response"].strip()

            else:
                raise ValueError(f"Unsupported AI provider: {self.provider}")
        except Exception as e:
            logger.error(f"AI Bridge: Generation failed: {e}")
            return ""

#!/usr/bin/env python3
"""
Universal LLM Connector - Fetch ACTUAL models from APIs using the user's API key
"""

import os
import json
import requests
from typing import Dict, List, Optional, Any
from urllib.parse import urlparse
from opencode_bridge import (
    chat_with_opencode_openai,
    get_local_chatgpt_bridge_status,
    list_openai_models_via_opencode,
)


def _extract_openai_style_error(data: Dict[str, Any]) -> str:
    error = data.get("error")
    if isinstance(error, dict):
        message = error.get("message") or error.get("type") or "Unknown API error"
        code = error.get("code")
        if code == "insufficient_quota":
            return "OpenAI account connected, but API access is unavailable: insufficient_quota. ChatGPT subscription access is not the same as Platform API credits."
        if code and code not in str(message):
            return f"{message} ({code})"
        return str(message)
    return ""


def _looks_like_oauth_token(value: str) -> bool:
    token = (value or "").strip()
    return token.startswith("eyJ") and token.count(".") >= 2


def _is_http_base_url(value: str) -> bool:
    parsed = urlparse((value or "").strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _friendly_request_error(provider: str, exc: Exception) -> str:
    if isinstance(exc, requests.exceptions.MissingSchema):
        return f"URL invalide pour {provider}. Utilise une URL complète en http(s)."
    if isinstance(exc, requests.exceptions.InvalidURL):
        return f"URL invalide pour {provider}."
    if isinstance(exc, requests.exceptions.Timeout):
        return f"{provider} ne répond pas assez vite. Réessaie ou choisis un autre modèle."
    if isinstance(exc, requests.exceptions.ConnectionError):
        return f"Impossible de joindre {provider}. Vérifie l’URL, le réseau ou le service local."
    return f"Erreur réseau {provider}: {exc}"


class LLMProvider:
    def chat(self, messages: List[Dict], tools: List[Dict] = None, files: List[str] = None) -> Dict:
        pass
    def get_models(self, api_key: str = "") -> List[str]:
        pass


class OllamaProvider(LLMProvider):
    """Ollama - Modèles locaux"""
    def __init__(self, model: str = "llama3", base_url: str = "http://localhost:11434"):
        self.model = model
        self.base_url = (base_url or "http://localhost:11434").rstrip("/")
        if self.base_url.endswith("/api/chat"):
            self.base_url = self.base_url[: -len("/api/chat")]
    
    def chat(self, messages: List[Dict], tools: List[Dict] = None, files: List[str] = None) -> Dict:
        url = f"{self.base_url}/api/chat"
        payload = {"model": self.model, "messages": messages, "stream": False}
        try:
            resp = requests.post(url, json=payload, timeout=120)
            return {"type": "text", "content": resp.json()["message"]["content"]}
        except requests.exceptions.Timeout:
            return {"type": "error", "content": "Ollama met trop de temps à répondre. Vérifie le modèle local puis réessaie."}
        except requests.exceptions.ConnectionError:
            return {"type": "error", "content": "Ollama ne répond pas sur localhost:11434. Lance Ollama ou choisis un autre provider."}
        except requests.exceptions.RequestException as e:
            return {"type": "error", "content": f"Erreur réseau Ollama: {e}"}
        except Exception as e:
            return {"type": "error", "content": str(e)}
    
    def get_models(self, api_key: str = "") -> List[str]:
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=5)
            return [m["name"] for m in resp.json().get("models", [])]
        except:
            return []


class LMStudioProvider(LLMProvider):
    """LM Studio - Modèles locaux via API compatible OpenAI"""
    def __init__(self, model: str = "local-model", base_url: str = "http://localhost:1234/v1"):
        self.model = model
        self.base_url = (base_url or "http://localhost:1234/v1").rstrip("/")
        if self.base_url.endswith("/chat/completions"):
            self.base_url = self.base_url[: -len("/chat/completions")]
    
    def chat(self, messages: List[Dict], tools: List[Dict] = None, files: List[str] = None) -> Dict:
        url = f"{self.base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        payload = {"model": self.model, "messages": messages}
        if tools:
            payload["tools"] = tools
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=120)
            msg = resp.json()["choices"][0]["message"]
            if msg.get("tool_calls"):
                tc = msg["tool_calls"][0]["function"]
                return {"type": "tool_call", "name": tc["name"], "arguments": json.loads(tc["arguments"])}
            return {"type": "text", "content": msg.get("content", "")}
        except Exception as e:
            return {"type": "error", "content": str(e)}
    
    def get_models(self, api_key: str = "") -> List[str]:
        try:
            resp = requests.get(f"{self.base_url}/models", timeout=5)
            return [m["id"] for m in resp.json().get("data", [])]
        except:
            return []


class OpenAIProvider(LLMProvider):
    """OpenAI - GPT models"""
    def __init__(
        self,
        api_key: str = "",
        model: str = "gpt-4",
        base_url: str = "https://api.openai.com/v1",
        requires_api_key: bool = True,
        provider_label: str = "OpenAI",
    ):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if _looks_like_oauth_token(self.api_key):
            self.api_key = ""
        self.model = model
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        self.requires_api_key = requires_api_key
        self.provider_label = provider_label
        if self.base_url.endswith("/chat/completions"):
            self.base_url = self.base_url[: -len("/chat/completions")]
    
    def chat(self, messages: List[Dict], tools: List[Dict] = None, files: List[str] = None) -> Dict:
        if self.requires_api_key and not self.api_key:
            return {"type": "error", "content": f"Ajoute une clé API {self.provider_label}."}
        if not _is_http_base_url(self.base_url):
            return {"type": "error", "content": f"URL invalide pour {self.provider_label}. Utilise une URL complète en http(s)."}

        url = f"{self.base_url}/chat/completions"
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}
        if not self.api_key:
            headers.pop("Authorization", None)
        payload = {"model": self.model, "messages": messages}
        if files:
            user_message = payload["messages"][-1]
            content = []
            text_value = user_message.get("content", "")
            if isinstance(text_value, str) and text_value:
                content.append({"type": "text", "text": text_value})
            for file_path in files:
                if not file_path:
                    continue
                lowered = file_path.lower()
                if lowered.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".heic")):
                    try:
                        import base64
                        mime = "image/png"
                        if lowered.endswith(".jpg") or lowered.endswith(".jpeg"):
                            mime = "image/jpeg"
                        elif lowered.endswith(".webp"):
                            mime = "image/webp"
                        elif lowered.endswith(".gif"):
                            mime = "image/gif"
                        elif lowered.endswith(".heic"):
                            mime = "image/heic"
                        with open(file_path, "rb") as handle:
                            encoded = base64.b64encode(handle.read()).decode("ascii")
                        content.append({
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{encoded}"}
                        })
                    except Exception:
                        continue
            if content:
                user_message["content"] = content
        if tools:
            payload["tools"] = tools
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=120)
            data = resp.json()
            if resp.status_code >= 400:
                return {"type": "error", "content": _extract_openai_style_error(data) or f"{self.provider_label} HTTP {resp.status_code}"}
            choices = data.get("choices")
            if not choices:
                return {"type": "error", "content": _extract_openai_style_error(data) or f"{self.provider_label} a renvoyé une réponse inattendue."}
            msg = choices[0].get("message", {})
            if msg.get("tool_calls"):
                tc = msg["tool_calls"][0]["function"]
                return {"type": "tool_call", "name": tc["name"], "arguments": json.loads(tc["arguments"])}
            return {"type": "text", "content": msg.get("content", "")}
        except ValueError:
            return {"type": "error", "content": f"{self.provider_label} a renvoyé une réponse non JSON."}
        except requests.RequestException as e:
            return {"type": "error", "content": _friendly_request_error(self.provider_label, e)}
        except Exception as e:
            return {"type": "error", "content": str(e)}
    
    def get_models(self, api_key: str = "") -> List[str]:
        """Fetch REAL models from OpenAI API using the user's key"""
        key = api_key or self.api_key
        if self.requires_api_key and not key:
            raise ValueError(f"Ajoute une clé API {self.provider_label}.")
        if not _is_http_base_url(self.base_url):
            raise ValueError(f"URL invalide pour {self.provider_label}. Utilise une URL complète en http(s).")
        
        try:
            url = f"{self.base_url}/models"
            headers = {"Authorization": f"Bearer {key}"} if key else {}
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code == 200:
                models = [m["id"] for m in resp.json().get("data", [])]
                if models:
                    return sorted(set(models))
            if resp.status_code >= 400:
                try:
                    data = resp.json()
                except Exception:
                    data = {}
                raise RuntimeError(_extract_openai_style_error(data) or f"{self.provider_label} HTTP {resp.status_code}")
        except requests.RequestException as e:
            raise RuntimeError(_friendly_request_error(self.provider_label, e)) from e
        except Exception as e:
            raise RuntimeError(str(e)) from e
        raise RuntimeError(f"Aucun modèle disponible pour {self.provider_label}.")


class OpenAICompatibleProvider(OpenAIProvider):
    """Custom OpenAI-compatible endpoint."""
    def __init__(self, api_key: str = "", model: str = "local-model", base_url: str = ""):
        if not _is_http_base_url(base_url):
            raise ValueError("URL invalide pour Custom OpenAI-compatible. Utilise une URL complète en http(s).")
        super().__init__(
            api_key=api_key,
            model=model or "local-model",
            base_url=base_url,
            requires_api_key=False,
            provider_label="Custom OpenAI-compatible",
        )


class LocalChatGPTCodexBridgeProvider(LLMProvider):
    """OpenCode/Codex local bridge using official local CLI auth state."""
    def __init__(self, model: str = "openai/gpt-5.4-mini"):
        self.model = model or "openai/gpt-5.4-mini"

    def chat(self, messages: List[Dict], tools: List[Dict] = None, files: List[str] = None) -> Dict:
        return chat_with_opencode_openai(messages, model=self.model, tools=tools, files=files)

    def get_models(self, api_key: str = "") -> List[str]:
        status = get_local_chatgpt_bridge_status()
        if not status.get("installed") or not status.get("connected"):
            return []
        return list_openai_models_via_opencode()


class AnthropicProvider(LLMProvider):
    """Anthropic - Claude models"""
    def __init__(self, api_key: str = "", model: str = "claude-sonnet-4-20250514"):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        self.model = model
    
    def chat(self, messages: List[Dict], tools: List[Dict] = None, files: List[str] = None) -> Dict:
        url = "https://api.anthropic.com/v1/messages"
        headers = {"Content-Type": "application/json", "x-api-key": self.api_key, "anthropic-version": "2023-06-01"}
        
        system = ""
        anthropic_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system = msg["content"]
            else:
                anthropic_messages.append(msg)
        
        payload = {"model": self.model, "messages": anthropic_messages, "max_tokens": 4096}
        if system:
            payload["system"] = [{"type": "text", "text": system}]
        
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=120)
            data = resp.json()
            if data.get("content"):
                return {"type": "text", "content": data["content"][0]["text"]}
            return {"type": "text", "content": ""}
        except Exception as e:
            return {"type": "error", "content": str(e)}
    
    def get_models(self, api_key: str = "") -> List[str]:
        """Return known Claude models - Anthropic doesn't have a list models endpoint"""
        key = api_key or self.api_key
        # Show all known Claude models
        return [
            # Claude 4
            "claude-opus-4-20250514", "claude-sonnet-4-20250514", "claude-sonnet-4-20250501",
            "claude-4-sonnet-20250514", "claude-4-opus-20250514", "claude-4-haiku-20250514",
            # Claude 3.5
            "claude-3-5-sonnet-20241022", "claude-3-5-sonnet-20240620",
            "claude-3-5-haiku-20240307",
            # Claude 3
            "claude-3-opus-20240229", "claude-3-sonnet-20240229",
            "claude-3-haiku-20240307",
            # Claude 2
            "claude-2.1", "claude-2.0", "claude-2",
            # Claude 1
            "claude-1.2", "claude-1.1", "claude-1.0",
            # Legacy
            "claude-instant-1.2", "claude-instant-1.1", "claude-instant"
        ]


class GoogleGeminiProvider(LLMProvider):
    """Google Gemini - Gemini models"""
    def __init__(self, api_key: str = "", model: str = "gemini-2.0-flash-exp"):
        self.api_key = api_key or os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        self.model = model
    
    def chat(self, messages: List[Dict], tools: List[Dict] = None, files: List[str] = None) -> Dict:
        key = self.api_key
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={key}"
        
        contents = []
        for msg in messages:
            if msg["role"] == "system":
                continue
            role = "user" if msg["role"] == "user" else "model"
            contents.append({"role": role, "parts": [{"text": msg["content"]}]})
        
        payload = {"contents": contents}
        
        try:
            resp = requests.post(url, json=payload, timeout=120)
            data = resp.json()
            if data.get("candidates"):
                content = data["candidates"][0]["content"]
                if content.get("parts"):
                    return {"type": "text", "content": content["parts"][0].get("text", "")}
            return {"type": "text", "content": ""}
        except Exception as e:
            return {"type": "error", "content": str(e)}
    
    def get_models(self, api_key: str = "") -> List[str]:
        """Fetch REAL models from Google Gemini API"""
        key = api_key or self.api_key
        if not key:
            return [
                # Gemini 2.5 (latest)
                "gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.5-pro",
                "gemini-2.5-flash-image", "gemini-2.5-flash-native-audio-latest",
                "gemini-2.5-computer-use-preview-10-2025",
                # Gemini 2.0
                "gemini-2.0-flash", "gemini-2.0-flash-001", "gemini-2.0-flash-lite", "gemini-2.0-flash-lite-001",
                "gemini-2.0-pro",
                # Gemini 1.5
                "gemini-1.5-pro", "gemini-1.5-flash", "gemini-1.5-flash-8b", "gemini-1.5-pro-vision",
                # Gemini 1.0
                "gemini-pro", "gemini-pro-vision", "gemini-ultra",
                # Gemma
                "gemma-3-27b-it", "gemma-3-4b-it", "gemma-4-31b-it",
                # Experimental
                "gemini-3-flash-preview", "gemini-3-pro-preview",
                # Vision/Audio/Video
                "imagen-4.0-generate-001", "veo-3.0-generate-001"
            ]
        
        all_models = set()
        
        try:
            # First try the models endpoint
            url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                for m in data.get("models", []):
                    model_name = m["name"].replace("models/", "")
                    all_models.add(model_name)
            
            # Also try tuned models endpoint
            tuned_url = f"https://generativelanguage.googleapis.com/v1beta/tunedModels?key={key}"
            tuned_resp = requests.get(tuned_url, timeout=10)
            if tuned_resp.status_code == 200:
                tuned_data = tuned_resp.json()
                for m in tuned_data.get("tunedModels", []):
                    model_name = m["name"].replace("tunedModels/", "")
                    all_models.add(model_name)
                    
        except Exception as e:
            print(f"Error fetching Gemini models: {e}")
        
        if all_models:
            return sorted(all_models)
        
        return [
            "gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash",
            "gemini-1.5-pro", "gemini-1.5-flash"
        ]


class MistralProvider(LLMProvider):
    """Mistral AI"""
    def __init__(self, api_key: str = "", model: str = "mistral-large-latest"):
        self.api_key = api_key or os.getenv("MISTRAL_API_KEY")
        self.model = model
    
    def chat(self, messages: List[Dict], tools: List[Dict] = None, files: List[str] = None) -> Dict:
        url = "https://api.mistral.ai/v1/chat/completions"
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}
        payload = {"model": self.model, "messages": messages}
        
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=120)
            msg = resp.json()["choices"][0]["message"]
            return {"type": "text", "content": msg.get("content", "")}
        except Exception as e:
            return {"type": "error", "content": str(e)}
    
    def get_models(self, api_key: str = "") -> List[str]:
        key = api_key or self.api_key
        if not key:
            return [
                "mistral-large-latest", "mistral-large-2", "mistral-large-2-2411",
                "mistral-small-latest", "mistral-small-2505",
                "mistral-medium-latest", "mistral-medium-2312",
                "mistral-tiny", "mistral-tiny-2312",
                "mixtral-8x7b", "mixtral-8x7b-32768", "mixtral-8x22b",
                "ministral-3b", "ministral-8b"
            ]
        
        try:
            # Mistral API - list models
            url = "https://api.mistral.ai/v1/models"
            headers = {"Authorization": f"Bearer {key}"}
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code == 200:
                return [m["id"] for m in resp.json().get("data", [])]
        except:
            pass
        return [
            "mistral-large-latest", "mistral-large-2", "mistral-large-2-2411",
            "mistral-small-latest", "mistral-small-2505",
            "mistral-medium-latest", "mistral-medium-2312",
            "mistral-tiny", "mistral-tiny-2312",
            "mixtral-8x7b", "mixtral-8x7b-32768", "mixtral-8x22b",
            "ministral-3b", "ministral-8b"
        ]


class GroqProvider(LLMProvider):
    """Groq - Ultra fast inference"""
    def __init__(self, api_key: str = "", model: str = "llama-3.1-70b-versatile"):
        self.api_key = api_key or os.getenv("GROQ_API_KEY")
        self.model = model
    
    def chat(self, messages: List[Dict], tools: List[Dict] = None, files: List[str] = None) -> Dict:
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}
        payload = {"model": self.model, "messages": messages}
        
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=120)
            msg = resp.json()["choices"][0]["message"]
            return {"type": "text", "content": msg.get("content", "")}
        except Exception as e:
            return {"type": "error", "content": str(e)}
    
    def get_models(self, api_key: str = "") -> List[str]:
        key = api_key or self.api_key
        if not key:
            return [
                # Llama 4
                "llama-4-scout-17b-8e-instruct-maidev", "llama-4-scout-17b-8e-instruct",
                # Llama 3.1
                "llama-3.1-70b-versatile", "llama-3.1-8b-instant",
                # Llama 3
                "llama-3-70b-versatile", "llama-3-8b-instant",
                # Mixtral
                "mixtral-8x7b-32768", "mixtral-8x22b-32768",
                # Gemma
                "gemma2-9b-it", "gemma2-27b-it",
                # Others
                "llama-3-8b-8192-istructions"
            ]
        
        try:
            url = "https://api.groq.com/openai/v1/models"
            headers = {"Authorization": f"Bearer {key}"}
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code == 200:
                return [m["id"] for m in resp.json().get("data", [])]
        except:
            pass
        return [
            # Llama 4
            "llama-4-scout-17b-8e-instruct-maidev", "llama-4-scout-17b-8e-instruct",
            # Llama 3.1
            "llama-3.1-70b-versatile", "llama-3.1-8b-instant",
            # Llama 3
            "llama-3-70b-versatile", "llama-3-8b-instant",
            # Mixtral
            "mixtral-8x7b-32768", "mixtral-8x22b-32768",
            # Gemma
            "gemma2-9b-it", "gemma2-27b-it"
        ]


class CohereProvider(LLMProvider):
    """Cohere - Command R+"""
    def __init__(self, api_key: str = "", model: str = "command-r-plus"):
        self.api_key = api_key or os.getenv("COHERE_API_KEY")
        self.model = model
    
    def chat(self, messages: List[Dict], tools: List[Dict] = None, files: List[str] = None) -> Dict:
        url = "https://api.cohere.com/v1/chat"
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}
        
        chat_history = []
        for msg in messages:
            if msg["role"] != "system":
                chat_history.append({"role": msg["role"], "message": msg["content"]})
        
        payload = {"model": self.model, "chat_history": chat_history, "message": messages[-1]["content"] if messages else ""}
        
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=120)
            return {"type": "text", "content": resp.json().get("text", "")}
        except Exception as e:
            return {"type": "error", "content": str(e)}
    
    def get_models(self, api_key: str = "") -> List[str]:
        return [
            # Command R+
            "command-r-plus", "command-r-plus-08-2024",
            # Command R
            "command-r", "command-r-08-2024", "command-r-07-2024",
            # Command
            "command", "command-light",
            # Embeddings
            "embed-multilingual-v3.0", "embed-english-v3.0",
            "embed-multilingual-v2.0", "embed-english-v2.0"
        ]


class HuggingFaceProvider(OpenAIProvider):
    """Hugging Face Inference Providers via OpenAI-compatible router."""
    def __init__(self, api_key: str = "", model: str = "openai/gpt-oss-120b", base_url: str = "https://router.huggingface.co/v1"):
        super().__init__(
            api_key=api_key,
            model=model,
            base_url=base_url or "https://router.huggingface.co/v1",
            requires_api_key=True,
            provider_label="Hugging Face",
        )

    def chat(self, messages: List[Dict], tools: List[Dict] = None, files: List[str] = None) -> Dict:
        # Hugging Face must not fallback to local OpenCode auth.
        if not self.api_key:
            return {"type": "error", "content": "Ajoute un token Hugging Face."}
        return super().chat(messages, tools=tools, files=files)

    def get_models(self, api_key: str = "") -> List[str]:
        key = api_key or self.api_key
        if not key:
            raise ValueError("Ajoute un token Hugging Face.")
        try:
            url = f"{self.base_url}/models"
            headers = {"Authorization": f"Bearer {key}"}
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code == 200:
                models = [m.get("id", "") for m in resp.json().get("data", [])]
                models = sorted(set([m for m in models if m]))
                if models:
                    return models
        except Exception:
            raise
        raise RuntimeError("Aucun modèle Hugging Face disponible pour ce token.")


class AzureOpenAIProvider(LLMProvider):
    """Azure OpenAI"""
    def __init__(self, api_key: str = "", endpoint: str = "", model: str = "gpt-4"):
        self.api_key = api_key
        self.endpoint = endpoint
        self.model = model
    
    def chat(self, messages: List[Dict], tools: List[Dict] = None) -> Dict:
        url = f"{self.endpoint}openai/deployments/{self.model}/chat/completions?api-version=2024-02-01"
        headers = {"Content-Type": "application/json", "api-key": self.api_key}
        payload = {"model": self.model, "messages": messages}
        
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=120)
            return {"type": "text", "content": resp.json()["choices"][0]["message"]["content"]}
        except Exception as e:
            return {"type": "error", "content": str(e)}
    
    def get_models(self, api_key: str = "") -> List[str]:
        # Azure - can't easily list without more config
        return ["gpt-4", "gpt-4-turbo", "gpt-35-turbo"]


# Factory
PROVIDERS = {
    "ollama": OllamaProvider,
    "lmstudio": LMStudioProvider,
    "openai": OpenAIProvider,
    "local_chatgpt_codex": LocalChatGPTCodexBridgeProvider,
    "openai_compatible": OpenAICompatibleProvider,
    "anthropic": AnthropicProvider,
    "gemini": GoogleGeminiProvider,
    "google": GoogleGeminiProvider,
    "mistral": MistralProvider,
    "cohere": CohereProvider,
    "groq": GroqProvider,
    "huggingface": HuggingFaceProvider,
    "azure": AzureOpenAIProvider,
}


def get_provider(provider: str, api_key: str = "", model: str = "", **kwargs) -> LLMProvider:
    provider = provider.lower()
    if provider not in PROVIDERS:
        raise ValueError(f"Provider non supporté: {provider}")
    
    provider_class = PROVIDERS[provider]
    config = {"model": model} if model else {}
    
    if provider in ["openai", "openai_compatible", "anthropic", "gemini", "google", "mistral", "cohere", "groq", "huggingface", "azure"]:
        config["api_key"] = api_key

    if provider in ["openai", "openai_compatible", "lmstudio", "ollama", "groq", "mistral"]:
        base_url = kwargs.get("base_url", "")
        if base_url:
            config["base_url"] = base_url

    if provider == "azure":
        config["endpoint"] = kwargs.get("base_url", "")
    
    return provider_class(**config)


def get_all_providers_info() -> List[Dict]:
    return [
        {"id": "ollama", "name": "Ollama", "description": "Modèles locaux", "requires_api_key": False},
        {"id": "lmstudio", "name": "LM Studio", "description": "Modèles locaux (API compatible)", "requires_api_key": False},
        {"id": "openai", "name": "OpenAI", "description": "GPT-4, GPT-4o, GPT-3.5", "requires_api_key": True},
        {"id": "local_chatgpt_codex", "name": "Local ChatGPT/Codex Bridge", "description": "CLI local bridge using Codex/OpenCode auth", "requires_api_key": False},
        {"id": "openai_compatible", "name": "OpenAI-Compatible", "description": "Endpoint compatible OpenAI", "requires_api_key": False},
        {"id": "anthropic", "name": "Anthropic", "description": "Claude 3.5 Sonnet, Claude 3", "requires_api_key": True},
        {"id": "gemini", "name": "Google Gemini", "description": "Gemini 2.0, Gemini 1.5", "requires_api_key": True},
        {"id": "mistral", "name": "Mistral AI", "description": "Mistral Large, Mixtral", "requires_api_key": True},
        {"id": "cohere", "name": "Cohere", "description": "Command R+", "requires_api_key": True},
        {"id": "groq", "name": "Groq", "description": "Inference ultra-rapide", "requires_api_key": True},
        {"id": "huggingface", "name": "HuggingFace", "description": "Inference API", "requires_api_key": True},
        {"id": "azure", "name": "Azure OpenAI", "description": "GPT-4 sur Azure", "requires_api_key": True},
    ]


if __name__ == "__main__":
    print("🔌 Universal LLM Connector")
    print("=" * 50)
    for p in get_all_providers_info():
        print(f"  • {p['id']}: {p['name']}")
    print("\n✅ Prêt!")

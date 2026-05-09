import os
import json
import requests
from dotenv import load_dotenv
from opencode_bridge import chat_with_opencode_openai
from urllib.parse import urlparse

# Charger les variables d'environnement (clés API)
load_dotenv()


def _extract_api_error(data):
    error = data.get("error")
    if isinstance(error, dict):
        message = error.get("message") or error.get("type") or "Unknown API error"
        code = error.get("code")
        if code == "insufficient_quota":
            return "OpenAI account connected, but API access is unavailable: insufficient_quota. ChatGPT subscription access is not the same as Platform API credits."
        if code and code not in message:
            return f"{message} ({code})"
        return str(message)
    return ""


def _looks_like_oauth_token(value):
    token = (value or "").strip()
    return token.startswith("eyJ") and token.count(".") >= 2


def _is_http_url(value):
    parsed = urlparse((value or "").strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _chat_completions_url(base_url):
    base = (base_url or "").strip().rstrip("/")
    if not base:
        return ""
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def _friendly_network_error(provider, exc):
    if isinstance(exc, requests.exceptions.MissingSchema):
        return f"URL invalide pour {provider}. Utilise une URL complète en http(s)."
    if isinstance(exc, requests.exceptions.InvalidURL):
        return f"URL invalide pour {provider}."
    if isinstance(exc, requests.exceptions.Timeout):
        return f"{provider} ne répond pas assez vite. Réessaie ou choisis un autre modèle."
    if isinstance(exc, requests.exceptions.ConnectionError):
        return f"Impossible de joindre {provider}. Vérifie l’URL, le réseau ou le service local."
    return f"Erreur réseau {provider}: {exc}"

class LLMConnector:
    def __init__(self, provider="ollama", model="llama3", api_key=None, base_url=None):
        """
        Initialise la connexion au LLM (Local ou API).
        :param provider: "ollama", "openai", "anthropic", "lmstudio"
        :param model: Le nom du modèle (ex: "llama3", "gpt-4o", "claude-3-opus")
        :param api_key: Clé API (optionnelle pour le local)
        :param base_url: URL de l'API (utile pour LM Studio ou Ollama distant)
        """
        self.provider = provider.lower()
        self.model = model
        self.api_key = os.getenv(f"{self.provider.upper()}_API_KEY") if api_key is None else api_key
        if self.provider == "openai" and _looks_like_oauth_token(self.api_key):
            self.api_key = ""
        
        # Configuration par défaut selon le provider
        if self.provider == "ollama":
            self.base_url = base_url or "http://localhost:11434/api/chat"
        elif self.provider == "lmstudio":
            self.base_url = _chat_completions_url(base_url or "http://localhost:1234/v1")
        elif self.provider == "openai":
            self.base_url = _chat_completions_url(base_url or "https://api.openai.com/v1")
        elif self.provider == "openai_compatible":
            self.base_url = _chat_completions_url(base_url or "")
        elif self.provider == "local_chatgpt_codex":
            self.base_url = ""
        elif self.provider == "anthropic":
            self.base_url = base_url or "https://api.anthropic.com/v1/messages"
        elif self.provider in ["gemini", "google"]:
            self.base_url = base_url or "https://generativelanguage.googleapis.com/v1beta"
        elif self.provider == "mistral":
            self.base_url = base_url or "https://api.mistral.ai/v1/chat/completions"
        elif self.provider == "groq":
            self.base_url = base_url or "https://api.groq.com/openai/v1/chat/completions"
        elif self.provider == "cohere":
            self.base_url = base_url or "https://api.cohere.com/v1/chat"
        elif self.provider == "huggingface":
            self.base_url = _chat_completions_url(base_url or "https://router.huggingface.co/v1")
        elif self.provider == "azure":
            self.base_url = base_url or ""
        else:
            raise ValueError(f"Provider non supporté : {self.provider}")

    def _format_tools_for_openai(self, tools_description):
        """Convertit notre description d'outils au format OpenAI/LMStudio Functions."""
        formatted_tools = []
        for tool_name, tool_info in tools_description.items():
            formatted_tools.append({
                "type": "function",
                "function": {
                    "name": tool_name,
                    "description": tool_info["description"],
                    "parameters": {
                        "type": "object",
                        "properties": tool_info["parameters"],
                        "required": list(tool_info["parameters"].keys())
                    }
                }
            })
        return formatted_tools

    def ask(self, system_prompt, user_message, tools_description=None, model_override=None):
        """
        Envoie un message au LLM et récupère sa réponse (texte ou appel d'outil).
        """
        if self.provider in ["ollama"]:
            return self._ask_ollama(system_prompt, user_message, tools_description, model_override=model_override)
        elif self.provider in ["openai", "openai_compatible", "lmstudio", "groq", "mistral"]:
            return self._ask_openai_format(system_prompt, user_message, tools_description, model_override=model_override)
        elif self.provider == "local_chatgpt_codex":
            selected_model = model_override or self.model
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ]
            return chat_with_opencode_openai(messages, model=selected_model)
        elif self.provider == "anthropic":
            return self._ask_anthropic(system_prompt, user_message, tools_description, model_override=model_override)
        elif self.provider in ["gemini", "google"]:
            return self._ask_gemini(system_prompt, user_message, model_override=model_override)
        elif self.provider == "cohere":
            return self._ask_cohere(system_prompt, user_message, model_override=model_override)
        elif self.provider == "huggingface":
            return self._ask_huggingface(system_prompt, user_message, model_override=model_override)
        else:
            return {"type": "error", "content": f"Provider {self.provider} non supporté"}

    def _ask_ollama(self, system_prompt, user_message, tools_description, model_override=None):
        """Appel à l'API locale d'Ollama."""
        # Ollama gère les outils différemment selon les modèles, on injecte la description dans le prompt système
        full_system = system_prompt
        if tools_description:
            full_system += "\n\nVOUS AVEZ ACCÈS AUX OUTILS SUIVANTS. Pour utiliser un outil, répondez UNIQUEMENT avec un JSON valide au format : {\"tool\": \"nom_outil\", \"args\": {\"param1\": \"valeur\"}}\n"
            full_system += json.dumps(tools_description, indent=2)

        payload = {
            "model": model_override or self.model,
            "messages": [
                {"role": "system", "content": full_system},
                {"role": "user", "content": user_message}
            ],
            "stream": False
        }
        
        try:
            response = requests.post(self.base_url, json=payload, timeout=120)
            response.raise_for_status()
            data = response.json()
            message = data.get("message") or {}
            result = message.get("content")

            if result is None:
                if data.get("error"):
                    return {"type": "error", "content": f"Ollama: {data['error']}"}
                return {"type": "error", "content": "Ollama returned an unexpected response."}
            
            # Essayer de parser si c'est un appel d'outil (JSON)
            try:
                tool_call = json.loads(result)
                if "tool" in tool_call and "args" in tool_call:
                    return {"type": "tool_call", "name": tool_call["tool"], "arguments": tool_call["args"]}
            except json.JSONDecodeError:
                pass # C'est juste du texte normal
                
            return {"type": "text", "content": result}
            
        except requests.HTTPError as e:
            error_body = ""
            try:
                error_json = e.response.json()
                error_body = error_json.get("error", "")
            except Exception:
                error_body = e.response.text[:300] if e.response is not None else ""
            if error_body:
                return {"type": "error", "content": f"Ollama error: {error_body}"}
            return {"type": "error", "content": str(e)}
        except requests.RequestException as e:
            return {"type": "error", "content": f"Unable to reach Ollama: {e}"}
        except Exception as e:
            return {"type": "error", "content": str(e)}

    def _ask_openai_format(self, system_prompt, user_message, tools_description, model_override=None):
        """Appel à l'API OpenAI ou LM Studio (qui utilise le même format)."""
        selected_model = model_override or self.model
        if self.provider == "openai" and not self.api_key:
            return {"type": "error", "content": "Ajoute une clé API OpenAI Platform ou sélectionne ChatGPT / Codex Bridge."}
        if self.provider == "openai_compatible" and not _is_http_url(self.base_url):
            return {"type": "error", "content": "URL invalide pour Custom OpenAI-compatible. Utilise une URL complète en http(s)."}

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": selected_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ]
        }

        if tools_description:
            payload["tools"] = self._format_tools_for_openai(tools_description)
            payload["tool_choice"] = "auto"

        try:
            response = requests.post(self.base_url, headers=headers, json=payload, timeout=120)
            data = response.json()

            if response.status_code >= 400:
                error_message = _extract_api_error(data) or f"{self.provider} HTTP {response.status_code}"
                return {"type": "error", "content": error_message}

            choices = data.get("choices")
            if not choices:
                error_message = _extract_api_error(data) or f"{self.provider} returned an unexpected response."
                return {"type": "error", "content": error_message}

            message = choices[0].get("message", {})
            
            # Vérifier si le LLM a décidé d'appeler un outil
            if "tool_calls" in message and message["tool_calls"]:
                tool_call = message["tool_calls"][0]["function"]
                return {
                    "type": "tool_call", 
                    "name": tool_call["name"], 
                    "arguments": json.loads(tool_call["arguments"])
                }
            
            return {"type": "text", "content": message.get("content", "")}
            
        except requests.RequestException as e:
            return {"type": "error", "content": _friendly_network_error(self.provider, e)}
        except ValueError:
            return {"type": "error", "content": f"{self.provider} a renvoyé une réponse non JSON."}
        except Exception as e:
            return {"type": "error", "content": str(e)}

    def _ask_anthropic(self, system_prompt, user_message, tools_description, model_override=None):
        """Appel à l'API Anthropic (Claude)."""
        # Implémentation similaire à OpenAI mais avec le format spécifique d'Anthropic
        pass # (À implémenter si vous utilisez Claude)

    def _ask_gemini(self, system_prompt, user_message, model_override=None):
        """Appel à l'API Google Gemini."""
        if not self.api_key:
            return {"type": "error", "content": "Clé API Gemini requise"}
        
        selected_model = model_override or self.model
        url = f"{self.base_url}/models/{selected_model}:generateContent?key={self.api_key}"
        
        contents = [
            {"role": "user", "parts": [{"text": user_message}]}
        ]
        
        # Ajouter le system prompt dans le premier message
        if system_prompt:
            contents.insert(0, {"role": "model", "parts": [{"text": system_prompt}]})
        
        payload = {"contents": contents}
        
        try:
            response = requests.post(url, json=payload, timeout=120)
            data = response.json()
            
            if data.get("candidates"):
                content = data["candidates"][0]["content"]
                if content.get("parts"):
                    return {"type": "text", "content": content["parts"][0].get("text", "")}
            return {"type": "text", "content": ""}
        except Exception as e:
            return {"type": "error", "content": str(e)}

    def _ask_cohere(self, system_prompt, user_message, model_override=None):
        """Appel à l'API Cohere."""
        if not self.api_key:
            return {"type": "error", "content": "Clé API Cohere requise"}
        
        url = f"{self.base_url}"
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        
        payload = {
            "model": model_override or self.model,
            "message": user_message,
            "system_prompt": system_prompt
        }
        
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=120)
            return {"type": "text", "content": response.json().get("text", "")}
        except Exception as e:
            return {"type": "error", "content": str(e)}

    def _ask_huggingface(self, system_prompt, user_message, model_override=None):
        """Appel à Hugging Face via le routeur OpenAI-compatible."""
        if not self.api_key:
            return {"type": "error", "content": "Ajoute un token Hugging Face."}
        return self._ask_openai_format(system_prompt, user_message, None, model_override=model_override)

# --- TEST DU CONNECTEUR ---
if __name__ == "__main__":
    print("🔌 Test du Connecteur LLM Universel...")
    
    # Exemple de définition d'outils que l'on donnera au LLM
    AVAILABLE_TOOLS = {
        "execute_bash": {
            "description": "Exécute une commande dans le terminal du Mac.",
            "parameters": {
                "command": {"type": "string", "description": "La commande bash à exécuter (ex: ls -la)"}
            }
        },
        "open_application": {
            "description": "Ouvre une application sur le Mac.",
            "parameters": {
                "app_name": {"type": "string", "description": "Le nom de l'application (ex: Safari)"}
            }
        }
    }
    
    system_prompt = "Tu es un assistant IA qui contrôle un Mac. Utilise les outils à ta disposition pour répondre aux demandes de l'utilisateur."
    user_request = "Peux-tu ouvrir Safari pour moi ?"
    
    print(f"\n🗣️ Utilisateur : {user_request}")
    
    # Test avec Ollama (Local)
    print("\n🤖 Test avec Ollama (modèle: llama3) :")
    llm_ollama = LLMConnector(provider="ollama", model="llama3")
    reponse_ollama = llm_ollama.ask(system_prompt, user_request, AVAILABLE_TOOLS)
    print(reponse_ollama)
    
    # Test avec LM Studio (Local - API compatible OpenAI)
    print("\n🤖 Test avec LM Studio (modèle local en cours d'exécution) :")
    llm_lmstudio = LLMConnector(provider="lmstudio", model="local-model")
    # reponse_lmstudio = llm_lmstudio.ask(system_prompt, user_request, AVAILABLE_TOOLS)
    # print(reponse_lmstudio)

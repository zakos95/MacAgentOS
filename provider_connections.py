from dataclasses import asdict, dataclass
from typing import List


@dataclass(frozen=True)
class ProviderConnectionSpec:
    id: str
    label: str
    auth_mode: str
    enabled: bool
    supports_api_key: bool
    supports_base_url: bool
    supports_model_listing: bool
    supports_connection_test: bool
    message: str = ""


SPECS: List[ProviderConnectionSpec] = [
    ProviderConnectionSpec(
        id="ollama",
        label="Ollama (local)",
        auth_mode="local",
        enabled=True,
        supports_api_key=False,
        supports_base_url=True,
        supports_model_listing=True,
        supports_connection_test=True,
        message="Connexion locale à Ollama sur la machine ou via une URL personnalisée.",
    ),
    ProviderConnectionSpec(
        id="openai",
        label="OpenAI Platform (API key)",
        auth_mode="api_key",
        enabled=True,
        supports_api_key=True,
        supports_base_url=False,
        supports_model_listing=True,
        supports_connection_test=True,
        message="Utilise une clé API OpenAI Platform.",
    ),
    ProviderConnectionSpec(
        id="local_chatgpt_codex",
        label="ChatGPT / Codex Bridge",
        auth_mode="local_cli_auth",
        enabled=True,
        supports_api_key=False,
        supports_base_url=False,
        supports_model_listing=True,
        supports_connection_test=True,
        message="Utilise le bridge ChatGPT embarqué comme provider IA normal.",
    ),
    ProviderConnectionSpec(
        id="anthropic",
        label="Anthropic",
        auth_mode="api_key",
        enabled=True,
        supports_api_key=True,
        supports_base_url=False,
        supports_model_listing=True,
        supports_connection_test=True,
        message="Utilise une clé API Anthropic.",
    ),
    ProviderConnectionSpec(
        id="gemini",
        label="Gemini",
        auth_mode="api_key",
        enabled=True,
        supports_api_key=True,
        supports_base_url=False,
        supports_model_listing=True,
        supports_connection_test=True,
        message="Utilise une clé API Google Gemini.",
    ),
    ProviderConnectionSpec(
        id="huggingface",
        label="Hugging Face",
        auth_mode="api_key",
        enabled=True,
        supports_api_key=True,
        supports_base_url=False,
        supports_model_listing=True,
        supports_connection_test=True,
        message="Utilise les Inference Providers Hugging Face via token HF.",
    ),
    ProviderConnectionSpec(
        id="openai_compatible",
        label="Custom OpenAI-compatible endpoint",
        auth_mode="custom_endpoint",
        enabled=True,
        supports_api_key=True,
        supports_base_url=True,
        supports_model_listing=True,
        supports_connection_test=True,
        message="Compatible OpenAI avec URL personnalisée.",
    ),
    ProviderConnectionSpec(
        id="chatgpt_account",
        label="ChatGPT Account (Coming Soon)",
        auth_mode="oauth_future",
        enabled=False,
        supports_api_key=False,
        supports_base_url=False,
        supports_model_listing=False,
        supports_connection_test=False,
        message="Direct ChatGPT account connection is not currently supported in this app",
    ),
]


def get_provider_connection_specs() -> List[dict]:
    return [asdict(spec) for spec in SPECS]

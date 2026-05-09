# Mac Agent OS - lancement et QA dev

Ce dossier est la copie autonome de travail de Mac Agent OS.

## 1. Préparer le backend

Depuis ce dossier :

```bash
cd "$PROJECT_ROOT"
python3.12 -m venv .venv312
source .venv312/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Python 3.10+ est requis. L'environnement attendu pour cette copie est `.venv312`.

## 2. Lancer le backend

```bash
cd "$PROJECT_ROOT"
.venv312/bin/python server.py
```

Le backend démarre sur `http://127.0.0.1:8000`.

Vérifications :

```bash
curl -sS http://127.0.0.1:8000/health
curl -sS -H "Authorization: Bearer $(cat "$HOME/Library/Application Support/MacAgentOS/data/api_key.txt")" http://127.0.0.1:8000/api/diagnostics
curl -sS -H "Authorization: Bearer $(cat "$HOME/Library/Application Support/MacAgentOS/data/api_key.txt")" http://127.0.0.1:8000/api/provider-connections
```

Si le port 8000 est déjà occupé, le backend affiche une erreur claire. Ferme l'ancien processus ou change `config/dev.json`.

```bash
lsof -nP -iTCP:8000 -sTCP:LISTEN
kill <PID>
```

## 3. Lancer l'app SwiftUI

```bash
cd "$PROJECT_ROOT"
CLANG_MODULE_CACHE_PATH=/private/tmp/macagent-clang-cache swift build --package-path "NativeMacApp"
CLANG_MODULE_CACHE_PATH=/private/tmp/macagent-clang-cache swift run --package-path "NativeMacApp"
```

En dev, lance d'abord `server.py`. L'app lit le token local dans :

```text
~/Library/Application Support/MacAgentOS/data/api_key.txt
```

## 3 bis. Bundle final avec backend embarqué

Le bundle final embarque le backend PyInstaller dans :

```text
Mac Agent OS.app/Contents/Resources/MacAgentServer
```

Build reproductible :

```bash
cd "$PROJECT_ROOT"
.venv312/bin/python -m pip install -r requirements.txt
.venv312/bin/pyinstaller --clean server.spec

cd NativeMacApp
ENVIRONMENT=prod \
SIGN_IDENTITY='' \
CLANG_MODULE_CACHE_PATH=/private/tmp/macagent-clang-cache \
BACKEND_BINARY="$PROJECT_ROOT/dist/MacAgentServer" \
zsh script/build_and_bundle.sh

open "/tmp/MacAgentOS-build/prod/Mac Agent OS.app"
```

Au lancement, l'app teste `http://127.0.0.1:8000/health`.

- Si un backend Mac Agent OS valide répond déjà, l'app le réutilise.
- Si aucun backend ne répond, elle lance `Contents/Resources/MacAgentServer` et attend `/health`.
- À la fermeture, elle arrête uniquement le backend qu'elle a lancé elle-même.
- Si le port 8000 répond mais n'est pas Mac Agent OS, elle affiche une erreur claire et ne lance pas de second backend.

Les logs du backend embarqué sont écrits ici :

```text
~/Library/Application Support/MacAgentOS/data/logs/backend-bundle.log
```

## 4. Providers IA

Mac Agent OS expose les providers suivants :

- `openai` : OpenAI Platform avec clé API. Sans clé, l'app demande d'ajouter une clé API.
- `local_chatgpt_codex` : ChatGPT / Codex Bridge. C'est un provider normal, séparé d'OpenAI API. En app finale, il utilise le runtime bridge embarqué `codex`, `CodexBridge` ou `ChatGPTBridge`; en dev seulement, il peut retomber sur `opencode` s'il est disponible.
- `huggingface` : Hugging Face via token HF et routeur OpenAI-compatible `https://router.huggingface.co/v1`.
- `ollama` : modèles locaux via `http://localhost:11434`. Ollama est optionnel et ne bloque jamais l'app.
- `anthropic`, `gemini`, `openai_compatible` : providers configurables par clé et/ou URL.

Hugging Face peut proposer un free tier ou des crédits selon le compte et le modèle, mais ce n'est pas gratuit illimité ni garanti.

## 5. Ollama

Ollama est détecté via :

```bash
curl -sS http://localhost:11434/api/tags
ollama list
```

Si Ollama n'est pas lancé, l'app reste utilisable avec un autre provider. Si Ollama répond sans modèle, installe un modèle, par exemple depuis l'app Ollama ou avec la CLI.

## 6. MCP et actions locales

Au démarrage, le backend tente de connecter les MCP configurés dans `mcp_servers.json`.

- `mac-control` et `safari` sont les MCP locaux principaux.
- `filesystem` est optionnel et nécessite `npx`.
- Les MCP dynamiques absents sont marqués skipped et ne bloquent pas le backend.

Les diagnostics affichent les MCP actifs et skipped. Les actions locales testées incluent `ouvre Safari` et `ouvre les réglages système`.

## 7. Tests release candidate

Commandes minimales :

```bash
PYTHONPYCACHEPREFIX=/private/tmp/macagent-pycache .venv312/bin/python -m py_compile server.py mcp_hub.py llm_universal.py provider_connections.py
PYTHONPYCACHEPREFIX=/private/tmp/macagent-pycache .venv312/bin/python -m unittest discover -s tests
CLANG_MODULE_CACHE_PATH=/private/tmp/macagent-clang-cache swift build --package-path "NativeMacApp"
```

## 8. Troubleshooting rapide

- Backend introuvable : vérifie que `.venv312/bin/python server.py` tourne.
- Port 8000 occupé : utilise `lsof -nP -iTCP:8000 -sTCP:LISTEN`, puis ferme l'ancien processus.
- Token local absent : démarre le backend une fois, puis relance l'app SwiftUI.
- Bridge non connecté : clique sur “Se connecter avec ChatGPT”. Si l'app indique que le bridge est absent, rebuild le bundle avec `CHATGPT_BRIDGE_BINARY=/chemin/vers/runtime-compatible`.
- Ollama n'apparaît pas : lance Ollama, vérifie `curl http://localhost:11434/api/tags`, puis recharge les modèles.
- Hugging Face échoue : ajoute un token HF valide et vérifie le quota/crédits du compte.
- MCP skipped : installe la dépendance optionnelle indiquée, par exemple Node.js/`npx` pour filesystem, ou ignore le MCP si tu n'en as pas besoin.

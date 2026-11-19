# MacAgent OS — bêta publique 1.1.0-beta.1

[English](README.md) · [Français](README.fr.md) · [Русский](README.ru.md) · [简体中文](README.zh-CN.md)

Téléchargez le DMG Apple Silicon dans les [versions GitHub](https://github.com/zakos95/MacAgentOS/releases/tag/v1.1.0-beta.1). Ouvrez le DMG puis copiez Mac Agent OS dans Applications. Le backend Python est embarqué. Choisissez un fournisseur dans les réglages. Ollama nécessite son installation et un modèle local. Les fournisseurs distants nécessitent votre propre accès.

La signature est ad hoc. Cette bêta n’est pas notariée par Apple. Gatekeeper peut demander une autorisation dans Réglages Système → Confidentialité et sécurité. Le paquet cible macOS 14+, avec validation sur Apple Silicon sous macOS 27.2.1. Intel et les autres versions de macOS restent à valider.

## Fonctions vérifiées

- Chat local avec Ollama et historique persistant.
- Import explicite de configurations MCP Claude Desktop ou projet. Transports stdio, HTTP et SSE, outils, ressources et prompts. [Guide MCP](docs/MCP.md).
- Skills Markdown personnels et de projet, invocation manuelle, arguments et références texte. [Skills et équipes](docs/SKILLS-AND-TEAMS.md).
- Équipes de plusieurs agents texte avec contextes indépendants et synthèse.
- Authentification du backend et du chat Web, données stockées dans Application Support.

Les tests automatisés couvrent 55 scénarios Python et 2 scénarios Swift. Des essais avec qwen3:8b ont validé le chat, un skill utilisant son fichier de référence et une équipe de deux agents. Les serveurs MCP de test exécutent de vraies connexions stdio, HTTP et SSE.

## Limites de cette bêta

La compatibilité avec tous les skills et connecteurs Claude reste un objectif. Les hooks, l’exécution shell des skills, les plugins hébergés, les agents disposant d’outils et l’authentification OAuth MCP intégrée restent à développer. Les équipes actuelles produisent du texte. Les fournisseurs cloud et les permissions macOS demandent des essais avec les comptes et autorisations des testeurs.

Aucun benchmark comparatif ne démontre une supériorité sur Claude. La qualité dépend du modèle choisi. Cette publication permet de recueillir des retours et d’étendre la couverture des tests.

Développeurs et testeurs, consultez le [guide de contribution](CONTRIBUTING.md), choisissez une [issue pour commencer](https://github.com/zakos95/MacAgentOS/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22) ou ouvrez une [discussion](https://github.com/zakos95/MacAgentOS/discussions). Signalez les problèmes avec la version du système, le modèle et les étapes de reproduction dans les [issues](https://github.com/zakos95/MacAgentOS/issues).

## Développement

```sh
python3.12 -m venv .venv312
.venv312/bin/python -m pip install -r requirements.txt
.venv312/bin/python server.py
```

Le backend écoute sur 127.0.0.1:8000. Le chat Web demande le jeton local du fichier `~/Library/Application Support/MacAgentOS/data/api_key.txt`. Pour isoler les données des tests, définissez `MACAGENT_APP_SUPPORT_DIR`.

```sh
MACAGENT_APP_SUPPORT_DIR=/tmp/macagent-tests .venv312/bin/python -m unittest discover -s tests
swift test --package-path NativeMacApp
```

Les instructions de fabrication figurent dans [Déploiement](NativeMacApp/DEPLOYMENT.md).

## Licence

Le code du projet est publié sous [licence MIT](LICENSE).

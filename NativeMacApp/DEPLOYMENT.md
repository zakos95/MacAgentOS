# Construire la bêta macOS

L’application utilise SwiftUI et embarque son serveur Python. Le paquet publié cible Apple Silicon et macOS 14 ou ultérieur. La version minimale déclarée se distingue de la version effectivement testée, indiquée dans les notes de publication.

## Compilation

Depuis la racine du dépôt, avec les outils de développement Swift 6 et Python 3.12 installés :

```sh
python3.12 -m venv .venv312
.venv312/bin/python -m pip install -r requirements.txt
.venv312/bin/python -m pip install pyinstaller
.venv312/bin/pyinstaller --clean server.spec
swift test --package-path NativeMacApp
RELEASE_VERSION=1.1.0-beta.1 BUILD_NUMBER=2 zsh NativeMacApp/script/release.sh
```

Le profil `prod` exige le binaire `dist/MacAgentServer`. `BACKEND_BINARY` permet d’indiquer un autre chemin. Le script produit un DMG, un ZIP, un fichier d’empreintes SHA-256 et des métadonnées dans `NativeMacApp/dist/releases/1.1.0-beta.1/prod`. `NativeMacApp/VERSION` contient la version numérique du bundle. `RELEASE_VERSION` contrôle le nom public des archives.

```sh
RELEASE_VERSION=1.1.0-beta.1 zsh NativeMacApp/script/verify_release.sh
RELEASE_VERSION=1.1.0-beta.1 zsh NativeMacApp/script/verify_dmg.sh
(cd NativeMacApp/dist/releases/1.1.0-beta.1/prod && shasum -a 256 -c SHA256SUMS.txt)
```

La signature par défaut est ad hoc. L’ouverture d’un téléchargement sur un autre Mac peut être bloquée par Gatekeeper. La procédure Apple figure dans [Ouverture d’apps en toute sécurité sur votre Mac](https://support.apple.com/fr-fr/102445). Une publication notariée nécessitera une identité Developer ID Application et une soumission à Apple.

## Exécution et isolation

Au lancement, Mac Agent OS cherche son service sur `127.0.0.1:8000`. Si le port est libre, le backend embarqué démarre. Si un service incompatible utilise ce port, l’app affiche le conflit. À la fermeture, elle arrête le backend qu’elle a lancé. Pour utiliser le backend source, lancer `.venv312/bin/python server.py` avant l’app.

Les conversations, connexions et réglages se trouvent dans `~/Library/Application Support/MacAgentOS`. Le journal du backend embarqué se trouve dans `data/logs/backend-bundle.log` sous ce dossier. Pour tester avec des données séparées, lancer le binaire de l’application avec `MACAGENT_APP_SUPPORT_DIR` pointant vers un dossier de test. Les modèles Ollama et les skills personnels de Claude appartiennent à leurs emplacements habituels.

Dans Diagnostics, **Importer une configuration…** accepte les fichiers Claude Desktop et projet dotés d’un objet `mcpServers`. Le dialogue affiche les commandes ou les hôtes avant le remplacement de la liste importée. Les connecteurs s’activent après redémarrage du backend. Dans le chat, `/mcp` liste les outils et `/team` suivi d’un objectif lance une équipe.

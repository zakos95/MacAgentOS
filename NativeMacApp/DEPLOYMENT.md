# Mac Agent OS Deployment

## Profils

- `Config/dev.json`
- `Config/test.json`
- `Config/prod.json`

Le packaging lit le profil via `ENVIRONMENT`.

## Build local

```bash
cd "$PROJECT_ROOT/NativeMacApp"
ENVIRONMENT=dev zsh script/build_and_bundle.sh
```

Pour un build SwiftPM simple en dev :

```bash
cd "$PROJECT_ROOT"
CLANG_MODULE_CACHE_PATH=/private/tmp/macagent-clang-cache swift build --package-path "NativeMacApp"
```

## Bundle final avec backend embarqué

Construire d'abord le backend PyInstaller depuis la racine du projet :

```bash
cd "$PROJECT_ROOT"
.venv312/bin/python -m pip install -r requirements.txt
.venv312/bin/pyinstaller --clean server.spec
```

Créer ensuite l'app avec le backend embarqué :

```bash
cd "$PROJECT_ROOT/NativeMacApp"
ENVIRONMENT=prod \
SIGN_IDENTITY='' \
CLANG_MODULE_CACHE_PATH=/private/tmp/macagent-clang-cache \
BACKEND_BINARY="$PROJECT_ROOT/dist/MacAgentServer" \
zsh script/build_and_bundle.sh
```

Sortie locale :

```text
/tmp/MacAgentOS-build/prod/Mac Agent OS.app
```

Au lancement, l'app réutilise un backend Mac Agent OS déjà disponible sur `127.0.0.1:8000`; sinon elle lance `Contents/Resources/MacAgentServer`. À la fermeture, elle arrête seulement le backend lancé par l'app, jamais un `python server.py` externe.

Si le port 8000 est occupé par un autre service, l'app affiche `Port 8000 occupé par un autre service.` et ne démarre pas de backend embarqué supplémentaire.

Les logs du backend embarqué sont dans :

```text
~/Library/Application Support/MacAgentOS/data/logs/backend-bundle.log
```

## Release versionnée

```bash
cd "$PROJECT_ROOT/NativeMacApp"
ENVIRONMENT=prod VERSION=$(cat VERSION) BUILD_NUMBER=1 zsh script/release.sh
```

Sortie attendue :

- `dist/releases/<version>/<environment>/Mac Agent OS.app`
- `dist/releases/<version>/<environment>/Mac Agent OS-<version>-<environment>.zip`
- `dist/releases/<version>/<environment>/Mac Agent OS-<version>-<environment>.dmg`
- `dist/releases/<version>/<environment>/SHA256SUMS.txt`
- `dist/releases/<version>/<environment>/build-info.json`

## Vérification

```bash
cd "$PROJECT_ROOT/NativeMacApp"
ENVIRONMENT=prod VERSION=$(cat VERSION) zsh script/verify_release.sh
ENVIRONMENT=prod VERSION=$(cat VERSION) zsh script/verify_dmg.sh
```

## Signature

Le script utilise `SIGN_IDENTITY` si elle est fournie, sinon la valeur par défaut du projet.

Exemple :

```bash
SIGN_IDENTITY="Developer ID Application: Example Corp (TEAMID)" ENVIRONMENT=prod zsh script/release.sh
```

## DMG signé vs distribution publique

Un DMG signé avec `Apple Development` est bien signé techniquement, mais ne passe pas forcément l’évaluation Gatekeeper pour une diffusion publique.

Pour une distribution publique propre, il faut :

- un certificat `Developer ID Application`
- idéalement notarization Apple

## Remarque produit

En dev, l'app peut toujours utiliser un backend externe lancé depuis la racine :

```bash
cd "$PROJECT_ROOT"
.venv312/bin/python server.py
```

Ce mode n'est pas cassé par le bundle final. Si `127.0.0.1:8000` est déjà occupé, l'app réutilise le service uniquement si `/health` répond comme Mac Agent OS; sinon elle affiche une erreur claire.

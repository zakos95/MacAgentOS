# Contribuer à MacAgent OS

Merci de contribuer à la bêta. Commencez par une [issue](https://github.com/zakos95/MacAgentOS/issues) décrivant le problème ou la fonction. Les issues portant le label `good first issue` ont un périmètre réduit. Indiquez dans l’issue que vous travaillez dessus pour éviter les doublons.

Le projet associe une application SwiftUI dans `NativeMacApp/` à un serveur Python FastAPI. Les intégrations MCP se trouvent dans `mcp_hub.py`, les skills Markdown dans `claude_skills.py` et l’exécution des équipes dans `team_runtime.py`. La documentation des fonctions et des limites figure dans [README](README.md), [MCP](docs/MCP.md) et [Skills et équipes](docs/SKILLS-AND-TEAMS.md).

## Lancer les tests

Python 3.12 et Swift 6 sont nécessaires pour le développement. Depuis la racine du dépôt :

```sh
python3.12 -m venv .venv312
.venv312/bin/python -m pip install -r requirements.txt
MACAGENT_APP_SUPPORT_DIR=/tmp/macagent-contributor .venv312/bin/python -m unittest discover -s tests
swift test --package-path NativeMacApp
```

Le serveur se lance avec `.venv312/bin/python server.py`. Ollama est facultatif pour les tests automatisés. Pour un essai réel, installez un modèle local et sélectionnez-le dans l’application. Les clés de fournisseurs cloud appartiennent à l’utilisateur et ne doivent jamais être ajoutées au dépôt ou aux issues.

## Proposer une modification

Créez une branche depuis `main`, limitez la modification à une issue et ouvrez une pull request avec un exemple de comportement et les vérifications effectuées. Ajoutez un test lorsqu’il protège un comportement susceptible de régresser. Pour une modification d’interface, joignez une capture. Signalez le modèle, la version de macOS et le type de Mac utilisés pour les essais réels.

Les fonctions annoncées comme compatibles avec Claude doivent être vérifiées contre la documentation actuelle de Claude et du protocole MCP. Décrivez précisément le format pris en charge et les cas non couverts.

## Signaler un bug

Utilisez le modèle d’issue et fournissez les étapes de reproduction, le résultat obtenu, le résultat attendu et les logs expurgés. Pour un défaut de sécurité, évitez de publier des secrets ou des données privées dans une issue publique. Contactez le mainteneur directement par son profil GitHub pour une divulgation privée.

Les contributions au code sont publiées sous la [licence MIT](LICENSE) du projet.

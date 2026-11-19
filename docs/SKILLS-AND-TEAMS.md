# Skills et équipes

Placez un fichier SKILL.md dans ~/.claude/skills/nom/ ou dans .claude/skills/nom/ du projet sélectionné. Appelez /nom suivi de vos arguments dans le chat. Le nom du dossier et l’alias YAML sont reconnus. Les arguments nommés, $ARGUMENTS et les arguments indexés sont substitués. Les références Markdown et texte relatives au dossier sont chargées dans une limite cumulée de 128 Kio.

Les extensions nécessitant des hooks, du shell, des outils d’agent ou un modèle imposé sont signalées comme incompatibles. Les instructions sont envoyées au fournisseur sélectionné.

La commande /team suivie d’un objectif lance deux agents texte indépendants, puis une synthèse. Des profils Markdown dans .claude/agents peuvent être sélectionnés par /team @auteur @relecteur objectif. Une équipe comporte de un à quatre rôles. Les profils nécessitant des outils ou des extensions Claude sont signalés comme incompatibles.

Le panneau Worktogether du chat Web affiche les contributions et la synthèse. Les routes /api/worktogether/start, /status, /stop et /chat permettent de démarrer, consulter, arrêter et poursuivre un travail. Un arrêt laisse terminer les requêtes fournisseur déjà parties.

La validation réelle avec Ollama qwen3:8b a obtenu le marqueur AZUR-739 depuis la référence d’un skill et une synthèse correcte pour une équipe analysant deux ateliers de 8 et 5 personnes.

Références officielles — [Skills Claude](https://code.claude.com/docs/en/skills) et [Sous-agents Claude](https://code.claude.com/docs/en/sub-agents).

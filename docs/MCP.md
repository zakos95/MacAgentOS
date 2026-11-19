# Connecter un serveur MCP

Dans **Diagnostics → MCP**, importez le fichier `.mcp.json` d’un projet ou `claude_desktop_config.json`. Examinez les serveurs et leurs commandes avant de confirmer. Au prochain démarrage du backend, MacAgent OS connecte les serveurs sélectionnés. Un serveur stdio lance sur le Mac le programme indiqué dans sa configuration, avec les droits du compte utilisateur.

MacAgent OS conserve une copie du champ `mcpServers` dans `~/Library/Application Support/MacAgentOS/mcp.json`. Une modification ultérieure du fichier d’origine nécessite un nouvel import. En mode source, vous pouvez aussi créer directement ce fichier privé, puis redémarrer `server.py`. Utilisez un chemin absolu pour les exécutables et les scripts locaux. Le lanceur, le serveur et ses dépendances doivent être disponibles sur votre Mac.

Par exemple, un serveur HTTP déjà déployé avec une authentification Bearer accepte une configuration de cette forme. Remplacez le domaine et fournissez une variable dédiée dans l’environnement du backend pour le jeton.

```json
{
  "mcpServers": {
    "notes": {
      "type": "http",
      "url": "https://notes.example.org/mcp",
      "headers": {"Authorization": "Bearer ${NOTES_MCP_TOKEN}"}
    }
  }
}
```

## Utiliser le chat

Envoyez `/mcp` pour voir les serveurs connectés et leurs outils. Indiquez le serveur pour distinguer des outils qui portent le même nom. Ces commandes fonctionnent sans configurer de modèle IA.

| Commande | Résultat |
| --- | --- |
| `/mcp mac-control get_battery_status` | Description et schéma de l’outil intégré. |
| `/mcp mac-control get_battery_status {}` | État de la batterie de ce Mac. |
| `/mcp notes @resources` | URI des ressources publiées par le serveur. |
| `/mcp notes @resources notes://guides/setup` | Contenu de la ressource à l’URI choisie. |
| `/mcp notes @templates` | Modèles d’URI acceptés. Remplacez leurs paramètres puis utilisez `@resources`. |
| `/mcp notes @prompts` | Noms et arguments des prompts disponibles. |
| `/mcp notes @prompts welcome {"name":"Léa"}` | Messages du prompt rendu, affichés dans la conversation. |

Les exemples pour `notes` supposent que votre connecteur publie les ressources et le prompt indiqués. Les actions s’exécutent dès l’envoi d’une commande d’outil accompagnée de son objet JSON.

## Compatibilité de la bêta

| Fonction | Portée |
| --- | --- |
| Serveur stdio | `command`, `args`, `env`, `cwd` et `type: stdio` facultatif. |
| Streamable HTTP | `type: http` ou `streamable-http`, `url` et `headers` statiques. |
| SSE historique | `type: sse`, `url` et `headers` statiques. |
| Variables | `${VAR}` et `${VAR:-défaut}`. Un serveur qui dépend d’une variable absente apparaît dans les diagnostics. Utilisez des noms dédiés pour les identifiants des serveurs distants. |
| Résultats | Le chat affiche le texte, les liens de ressources et les résultats structurés. Les médias binaires apparaissent avec leur type. |
| Authentification | Jetons transmis par les paramètres du serveur ou par en-tête. Les sessions OAuth interactives et les connecteurs gérés par un compte Claude restent hors du périmètre de cette bêta. |

Les transports WebSocket MCP, les extensions spécifiques à l’hôte Claude, les scripts `headersHelper`, le sampling et l’elicitation ne sont pas pris en charge dans cette version. La découverte a lieu au démarrage du backend. Après un changement de catalogue ou une coupure du serveur, redémarrez le backend.

Les essais d’intégration lancent des serveurs SDK locaux, appellent de vrais outils et vérifient les en-têtes, les ressources et les prompts. Ils peuvent être rejoués depuis l’environnement Python du projet avec `python -m unittest discover -s tests -p 'test_mcp*.py' -v`. Voir la [configuration MCP de Claude Code](https://code.claude.com/docs/en/mcp) et la [spécification MCP](https://modelcontextprotocol.io/specification/2025-11-25).

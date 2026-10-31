# MAESTRO Local Gateway — Guide accessible

**Le Gateway est le serveur local qui relie l'interface de MAESTRO au Core.** Il ne remplace pas le moteur IA et ne garantit pas, à lui seul, que le modèle local est prêt.

[← Guide débutant MAESTRO](https://github.com/eddarsglobal/maestro#readme) · [Interface](https://github.com/eddarsglobal/maestro-ui) · [Core](https://github.com/eddarsglobal/maestro)

> Projet en développement. Les fonctionnalités et la configuration exactes dépendent du commit. Ne pas interpréter `/health` comme une certification de production ou un test d'inférence.

## 1. À quoi sert ce dépôt ?

- écouter en local (par défaut `127.0.0.1:8787`) ;
- exposer les API de santé, de conversations et de missions disponibles dans la version ;
- communiquer avec le Core pour les contrôles de mission ;
- conserver les permissions et les frontières d'exécution hors du navigateur ;
- servir l'interface compilée sous `/app/` lorsque `MAESTRO_UI_DIST_DIR` est défini.

Une conversation peut exister même quand le moteur d'inférence n'est pas disponible. Une mission doit respecter les outils et droits réellement autorisés.

## 2. Pré-requis

Git, Python **3.11+** (3.12 recommandé), le dépôt Core frère `../maestro` et éventuellement le dépôt UI frère `../maestro-ui`. Pour l'inférence, un moteur local compatible doit être configuré séparément ; **les détails du modèle de l'équipe ne sont pas documentés publiquement**.

## 3. Installation pour débutants (macOS)

Si vous partez de zéro, suivez d'abord le [guide principal](https://github.com/eddarsglobal/maestro#installer-sur-macos--pas-à-pas).

```bash
mkdir -p "$HOME/MAESTRO"
cd "$HOME/MAESTRO"
git clone https://github.com/eddarsglobal/maestro.git
git clone https://github.com/eddarsglobal/maestro-gateway.git
git clone https://github.com/eddarsglobal/maestro-ui.git

python3 -m venv maestro-gateway/.venv
./maestro-gateway/.venv/bin/python -m pip install --upgrade pip setuptools wheel
./maestro-gateway/.venv/bin/python -m pip install -e ./maestro -e ./maestro-gateway
```

**Si les dossiers existent déjà**, ne les remplacez pas ; examinez leurs modifications avant toute mise à jour Git.

## 4. Construire l'interface (optionnel mais recommandé)

```bash
cd "$HOME/MAESTRO/maestro-ui"
npm ci
npm run build
```

Cela crée `dist/`. Le Gateway ne construit pas lui-même l'interface.

## 5. Démarrer le serveur

```bash
cd "$HOME/MAESTRO"
MAESTRO_UI_DIST_DIR="$HOME/MAESTRO/maestro-ui/dist" \
  ./maestro-gateway/.venv/bin/python -m maestro_gateway
```

Dans une seconde fenêtre Terminal :

```bash
curl -fsS http://127.0.0.1:8787/health
```

Dans le navigateur du même ordinateur : **http://127.0.0.1:8787/app/**

Pour arrêter le serveur lancé manuellement : **Ctrl+C** dans son Terminal. Il ne faut pas tuer arbitrairement d'autres processus utilisant le même port.

## 6. Paramètres utiles

| Variable d'environnement | Usage |
| --- | --- |
| `MAESTRO_GATEWAY_HOST` | Hôte local ; refus des interfaces non locales |
| `MAESTRO_GATEWAY_PORT` | Port HTTP, par défaut `8787` |
| `MAESTRO_RUNTIME_DIR` | Répertoire des données runtime, sous le dossier utilisateur par défaut |
| `MAESTRO_GATEWAY_ALLOWED_ORIGINS` | Origines navigateur explicitement autorisées pour certains modes de développement |
| `MAESTRO_UI_DIST_DIR` | Chemin du dossier `dist` construit par Vite |

**Ne configurez jamais `0.0.0.0`** pour exposer le Gateway sur un réseau. Pour le mode navigateur, privilégiez `/app/` sur le même port.

## 7. Conversations et données

Le Gateway possède un magasin de conversations et des API de gestion. Les historiques de conversations, les preuves et les journaux de mission ne sont **pas** interchangeables. Archiver n'est pas supprimer. Avant une opération de nettoyage, identifiez précisément les fichiers visés et réalisez une sauvegarde.

N'envoyez pas de captures de données privées ou de journaux contenant des secrets dans les Issues GitHub.

## 8. Dépannage

| Symptôme | Vérification |
| --- | --- |
| `ModuleNotFoundError` | Vérifiez l'environnement `.venv` et l'installation Core + Gateway |
| Port occupé | Identifiez le processus via `lsof -nP -iTCP:8787 -sTCP:LISTEN` |
| `/health` répond, mais pas l'interface | Vérifiez `MAESTRO_UI_DIST_DIR` et le dossier `dist` |
| UI ancienne | Reconstruisez l'UI puis rechargez la page sans cache |
| Aucune réponse de l'IA | Vérifiez le moteur autorisé, sa disponibilité et les erreurs de mission ; `/health` seul ne suffit pas |
| Permission refusée | Vérifiez les droits effectifs plutôt que de désactiver les contrôles |

## 9. Développement et sécurité

Le serveur reste local par défaut, contrôle l'en-tête Host, les origines et les opérations autorisées. La documentation ne garantit pas que tous les chemins de mission ou toutes les plateformes sont validés en production. Les tests de Gateway, du Core et les essais réels restent nécessaires.

Voir : [README du Core](https://github.com/eddarsglobal/maestro) · [Politique de sécurité](https://github.com/eddarsglobal/maestro/blob/main/SECURITY.md).

## English summary

The Gateway is MAESTRO's **localhost-only** API bridge between the UI and the Core. Install Core + Gateway in one Python virtual environment, build the UI separately and pass `MAESTRO_UI_DIST_DIR` when starting `python -m maestro_gateway`. The default browser URL is `http://127.0.0.1:8787/app/`. Gateway health is not proof that a local AI engine is configured or ready. This is development source, not a certified one-click deployment.

**by [EddarsGlobal](https://github.com/eddarsglobal)**

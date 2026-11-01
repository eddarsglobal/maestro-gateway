# RoadMapGuardian — Contrat de qualification réelle MAESTRO

**Mise à jour : 2026-11-01. Statut initial : BLOCKED.**
**Périmètre : Core, Gateway et UI sur le Mac réel.**

L'agent EXISTANT est `agents/Roadmap_guardian.py`, un **adaptateur Git en lecture seule**. Il n'est PAS aujourd'hui un certificateur autonome de missions. Le contrat suivant est obligatoire pour son développement et pour toute décision future. Ne pas confondre documentation et capacité implémentée.

## Mission du Guardian

Pour chaque jalon M0 à M7 des feuilles de route, appliquer strictement :

1. **INSPECT** — vérifier le code déjà présent, les droits, les trois HEAD Git et les versions effectivement lancées. Ne pas créer de doublon.
2. **AUTHORIZE** — avant toute écriture, outil ou exécution sensible, définir un espace de laboratoire et obtenir l'accord explicite du propriétaire.
3. **EXECUTE REAL** — vérifier l'exécution sur vrai Mac et véritables processus ou fichiers ; aucune simulation ou fixture ne peut constituer une preuve finale.
4. **VERIFY INDEPENDENTLY** — relire les effets réels indépendamment du modèle (sha256 avant/après, vrai code retour, contenu de fichiers, reçu transactionnel, journaux de mission et autorisations).
5. **NEGATIVE TESTS** — tester refus sans permission, interruption, timeout, double soumission, crash, reprise et tentative de fausse preuve. Toute lacune P0 bloque.
6. **REGRESSION** — exécuter les tests adaptés, conserver les logs bruts, distinguer `TESTS_PASS` et résultat fonctionnel réel.
7. **REVIEW / CERTIFY** — comparer empreintes des sources utilisées aux sources revues ; revue indépendante et accord humain du propriétaire obligatoires ; aucune auto-déclaration de certification.
8. **SYNC GIT** — publier UNIQUEMENT les modifications publiques approuvées sur branches contrôlées, vérifier Git local et GitHub Web, sans force-push, sans perdre les modifications non commitées, sans secrets.
9. **ORDER NEXT** — autoriser le jalon suivant **uniquement après la certification du courant**. En cas de modification de source, réouvrir les contrôles affectés.

## Sémantique des statuts

| Statut | Signification |
| --- | --- |
| `CODE_PRESENT` | Code présent, pas de preuve de travail |
| `TESTS_PASS` | Tests exécutés sur une version identifiée, pas de preuve LIVE globale |
| `LIVE_VERIFIED` | Effet réel observé et vérifié indépendamment |
| `REVIEW_REQUIRED` | Effet vérifié, contrôle externe/humain incomplet |
| `CERTIFIED` | LIVE réel + tests négatifs + revue indépendante + accord propriétaire + SHA identiques |
| `REMEDIATE` | Résultat négatif : corriger et retester le jalon |
| `BLOCK` | Permissions, preuves ou tests insuffisants, source divergente, résultat ambigu |
| `PRODUCTION_BLOCKED` | Un ou plusieurs jalons obligatoires restent non certifiés |

**Interdiction de convertir** `HTTP 200`, `/health=READY`, réponse du modèle, test mock, `1435 tests OK`, build ou manifeste auto-rédigé en `CERTIFIED`.

## Preuves privées à exiger pour chaque jalon

L'architecture de collecte des preuves est un travail M0 ouvert. Le lieu recommandé est `~/.maestro/roadmap_guardian/live/M<N>/` ; le simple fait de créer ce dossier ne vaut pas une preuve.

Chaque reçu doit rattacher :
- date UTC et poste cible, vrai processus et identifiant de requête ;
- SHA complets des trois dépôts, état des modifications locales et empreintes des sources réellement exécutées ;
- action réellement déclenchée, autorisation, commandes, codes de retour, logs originaux hachés ;
- état initial et état final **vérifiés par un outil distinct du texte produit par MAESTRO** ;
- résultats négatifs, erreurs, annulations et reprises ;
- validation de revue indépendante et consentement/approbation humaine ;
- preuve de comparaison des branches Web/local ; les secrets et modèles internes restent privés.

Un reçu signé uniquement par MAESTRO, un texte `PASS` ou un log fabriqué doit être refusé. Le mécanisme d'authentification de ces attestations reste à construire et tester sous M0.

## Matrice de certification ordonnée

| Jalon | Travail LIVE à vérifier |
| --- | --- |
| **M0** | inventaire trois dépôts/versions, sauvegardes SHA, mécanisme Guardian réel avec tests anti-fraude |
| **M1** | vraie mission UI → Gateway → Core → moteur local → AY/TX → réponse et trace cohérente |
| **M2** | concurrence, stop, annulation d'outils, crash, reprise sans double effet, transactions durables |
| **M3** | projet logiciel laboratoire : tests rouges, workspace autorisé, approbation, vrai diff appliqué, tests verts |
| **M4** | aucune fuite d'identifiant modèle utilisateur, sécurité réelle des API, session, Origin/Host et paths |
| **M5** | réponses utiles vérifiées en EN/FR/DE/AR/PT/ES/HR/IT, 20 tours et évaluation humaine |
| **M6** | application macOS installée, réglages/conversations conservés, reprises des essais et signature si distribution |
| **M7** | intégration trois dépôts, CI, revues et décisions de release sur les SHA exacts |

**Initialement : M0=OPEN/BLOCKED ; M1..M7=NOT_CERTIFIED ; NEXT_WORK=M0 ; NEXT_STAGE_AUTHORIZED=NO ; PRODUCTION=BLOCKED.**

## Contrôle exécutable actuel

```bash
python3 agents/Roadmap_guardian.py status
python3 agents/Roadmap_guardian.py git
python3 agents/Roadmap_guardian.py next
python3 agents/Roadmap_guardian.py live-health
```

`status` et `git` ne prouvent que les sources. `next` doit refuser tout passage avant que M0 ne dispose de véritables preuves certifiées. `live-health` teste seulement la connexion HTTP réelle à un Gateway local — **pas** l'inférence, l'effet d'une mission ou la production. `prepublish` reste fail-closed.

Ce document est un contrat de développement, **pas une certification du Guardian**.

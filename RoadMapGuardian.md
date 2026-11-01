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

<!-- GUARDIAN_EXISTENCE_FIRST_20261101 -->
## ORDRE IMPÉRATIF — VÉRIFIER L'EXISTANT AVANT TOUT DÉVELOPPEMENT

**Ordre du propriétaire, obligatoire pour chaque point M0–M7.** Après des semaines de développement, une case vide du Roadmap NE PROUVE PAS que la fonctionnalité est absente. **Aucun développement, patch, duplication ou remplacement n'est autorisé avant recherche dans GitHub ET dans les sources locales.**

### PRE-DEVELOPMENT DISCOVERY GATE — séquence non contournable

1. **Définir** le point exact, ses sous-exigences et la preuve d'acceptation attendue. Rechercher décisions, Roadmaps, Guardians, anciens correctifs, historiques, logs, tests et tickets liés ; une mention n'est pas une preuve fonctionnelle.
2. **Analyser GitHub** sur les trois dépôts officiels §eddarsglobal/maestro§, §eddarsglobal/maestro-gateway§ et §eddarsglobal/maestro-ui§ : branches §main§ et branche de développement, commits et PR, codes, routes, classes, fonctions, tests, documentation et intégrations. Enregistrer SHAs complets et fichiers probants. Si GitHub n'est pas accessible : §BLOCK_DISCOVERY_INCOMPLETE§, jamais « absent ».
3. **Analyser les trois worktrees locaux** : branche, HEAD, fichiers suivis/modifiés/non suivis, scripts, tests, dépendances et traces historiques. Vérifier l'exécutable actif sur le Mac ; une fonctionnalité présente localement mais absente de GitHub doit être **conservée**, jamais écrasée.
4. **Reconstituer l'intégration réelle** UI → Gateway → Core → outils/inférence → preuves, vérifier permissions, appels effectifs et chemins d'échec. Différencier source, mocks, tests déterministes, essais LIVE passés et effets LIVE vérifiés sur le commit actuel.
5. **Remplir une matrice d'existence** par sous-exigence avec preuve du chemin/fonction/commit : §PRESENT_VERIFIED§, §PRESENT_UNTESTED§, §PRESENT_PARTIAL§, §BROKEN§, §MISSING_CONFIRMED§ ou §UNKNOWN§. Les simples occurrences de mots ne certifient ni présence opérationnelle ni absence.
6. **Décider AVANT création** : §REUSE§ (déjà bon), §TEST_ONLY§ (présent mais non certifié), §REPAIR§ (défaut prouvé), §UPDATE§ (obsolète), §INTEGRATE§ (non raccordé) ; §CREATE§ est permis **uniquement** si absence démontrée par l'audit GitHub + local + runtime et approuvée explicitement. En cas de doute : §BLOCK/INVESTIGATE§.
7. **Appliquer le changement minimal** : ne pas recréer de module, route, test, Guardian, service ni architecture lorsqu'un mécanisme réutilisable existe ; sauvegarde, rollback, autorisation humaine et contrôle de non-régression obligatoires.
8. **Vérifier le travail réel** sur le Mac avec preuves indépendantes, tests positifs/négatifs, revue et approbation ; synchroniser GitHub/local sans écrasement, puis seulement ordonner le jalon suivant après §CERTIFIED§.

### Fiche requise : EXISTING_IMPLEMENTATION_AUDIT

Pour chaque point, conserver : §point_id§ ; objectif, critères ; §github_checked§ (trois dépôts, branches, SHA, fichiers et historique) ; §local_checked§ (HEAD, changements suivis et non suivis, code réellement exécuté) ; §found_implementations§ ; §gap_matrix§ ; §live_evidence§ ; §decision§ (§REUSE/TEST_ONLY/REPAIR/UPDATE/INTEGRATE/CREATE/BLOCK§) ; `minimal_change_plan` ; autorisations ; sauvegardes ; contrôle de régression ; `final_verdict`.

**INTERDICTION DE CRÉER AVANT VÉRIFICATION DE L'EXISTANT.** Aucun `PASS` de tests unitaires, rapport ou simulation ne remplace l'inspection GitHub/local ni les effets vérifiés. Ne jamais déduire `CREATE` d'une case Roadmap vide.

### Contrôle de l'agent

`python3 agents/Roadmap_guardian.py inspect-point M0` (ou M1…M7) doit rechercher les **traces déjà présentes** dans les arbres locaux et les branches GitHub, en affichant uniquement des chemins et des métadonnées sans divulguer de secret. Ce diagnostic n'est **pas** une certification fonctionnelle ; il échoue fermé si GitHub ou les fichiers sont indisponibles et **n'autorise jamais automatiquement du nouveau code**. Après ce diagnostic, revue humaine et décision documentée obligatoires.

`DISCOVERY=INCOMPLETE` ⇒ `DEVELOPMENT_AUTHORIZATION=BLOCK`. `DISCOVERY=REVIEW_REQUIRED` ⇒ `DEVELOPMENT_AUTHORIZATION=BLOCK_PENDING_OWNER_DECISION`. Seule une décision motivée et autorisée peut initier `REUSE/TEST_ONLY/REPAIR/UPDATE/INTEGRATE/CREATE`. Les jalons M0–M7 restent non certifiés jusqu'à preuve LIVE indépendante.

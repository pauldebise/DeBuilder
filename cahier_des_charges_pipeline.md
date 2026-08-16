# Cahier des Charges : Améliorations du Pipeline DeBuilder

**Contexte :** La boucle actuelle ouvre une seule session OpenCode par itération, chargée à la fois de comprendre le projet, choisir une tâche, implémenter, tester, committer et mettre à jour les fichiers de mémoire persistante. Cette surcharge dégrade le choix de tâche, l'implémentation et la fiabilité des mises à jour. Ce cahier des charges décrit le pipeline cible et les améliorations de fiabilité associées, notamment la détection de fin de mission.

## 1. Décisions d'Architecture

- **Découpage en 2 sessions par itération** (et non 3) :
  - Session **Plan** : compréhension du projet et définition de l'objectif de l'itération.
  - Session **Implement** : implémentation, tests, commits et mise à jour de la mémoire persistante.
- **Pas de session dédiée à la mémoire persistante** : c'est une tâche de synthèse, pas une tâche d'agent. Une session complète serait coûteuse (3× le contexte), dangereuse (accès en écriture au code) et de seconde main (hallucination de l'état). La mise à jour est une étape obligatoire de la session Implement, avec repli déterministe en cas d'échec (cf. 4.3).
- **Fichier de contrat de tâche** : le plan de la session Plan est matérialisé dans un fichier structuré et vérifiable (`TASK.md`) consommé par la session Implement.
- **La boucle ne croit pas l'agent sur parole** : les gates de validation (tests, complétude de tâche, mise à jour mémoire) sont déterministes, exécutées par la boucle elle-même.

## 2. Sessions Spécialisées

### 2.1 Session Plan (lecture seule)

- Lance une session OpenCode avec pour unique objectif : analyser l'état du projet (fichiers d'état, code, tests) et rédiger la prochaine tâche.
- **Sortie obligatoire : `TASK.md`** contenant :
  - l'objectif de l'itération,
  - les critères d'acceptation,
  - la commande exacte de test à exécuter,
  - une liste de sous-tâches sous forme de cases à cocher Markdown.
- **Permissions :** la session Plan est strictement lecture seule (refus des outils d'écriture `edit`/`write`/`bash` via la configuration OpenCode injectée par session).
- **Backlog persistant :** le planning est incrémental via un fichier `PLAN.md` (liste de tâches restantes, priorités), mis à jour à chaque itération, pour ne pas refaire le plan de zéro.
- **Anti "plan drift" :** avant de planifier, vérifier l'état des gates de l'itération précédente (arbre git propre, tests au vert, état des cases de `TASK.md`) afin de ne pas rédiger un plan sur une base périmée.


### 2.2 Session Implement

- Consomme `TASK.md` et les fichiers d'état ; ne relit pas intégralement le cahier des charges (celui-ci a déjà été traduit en tâche).
- Implémente, teste, committe par sous-tâche (petits commits, cf. règles existantes), coche chaque case de `TASK.md` en justifiant.
- **Convention Conventional Commits :** tous les commits de DeBuilder respectent le format Conventional Commits (`type(portée): description`), avec un type valide (`feat`, `fix`, `chore`, `docs`, `test`, `refactor`, `perf`, `ci`, `build`, `style`) et une description claire de l'objectif de la sous-tâche.
- **Dernière étape obligatoire :** mise à jour de `PROGRESS.md` (et `BENCHMARKS.md` le cas échéant) à partir de sa propre expérience (ground truth).

### 2.3 Gates déterministes exécutées par la boucle après la session Implement

- Toutes les cases de `TASK.md` sont cochées ?
- La commande de test du `TASK.md` relancée par la boucle passe-t-elle (cf. 5.3) ?
- `PROGRESS.md` a-t-il changé ?
- En cas d'échec d'une gate : la tâche n'est pas soldée, le motif est consigné et injecté à la session suivante.

## 3. Détection de Fin de Mission

- **Proposition par l'agent :** quand il estime la Definition of Done atteinte, l'agent écrit `FINISHED_REPORT.md` (checklist du cahier des charges → items réalisés, validés par tests). L'agent ne crée jamais `DONE` lui-même.
- **Validation par le pipeline :** une session courte de review (lecture seule) compare `FINISHED_REPORT.md` au cahier des charges. Si validée, **la boucle crée `DONE` elle-même** ; sinon, le feedback est écrit (ex: `REVIEW.md`) et la boucle repart. Le kill-switch manuel de l'interface reste prioritaire.
- **Ancre objective : `SPEC_COVERAGE.md`** : le planner maintient un mapping cahier des charges → item implémenté + test associé. La fin de mission devient « couverture 100 % + tests verts », un fait vérifiable plutôt qu'un jugement du modèle.
- **Détection de no-op :** si le diff d'une itération ne contient que des fichiers d'état (ou rien), l'itération est comptée comme no-op. Après N no-ops consécutifs (ex: 3), la boucle force le planner à déclarer la fin ou à justifier la poursuite, et notifie l'utilisateur.
- **Caps durs :** variable `DEBUILDER_MAX_ITERATIONS` et budget global de temps ; la boucle s'arrête proprement au dépassement avec notification.

## 4. Fiabilité de la Mémoire Persistante

### 4.1 Réparation déterministe des fichiers d'état

- Si `PROGRESS.md` est malformé (séparateurs de structure absents, sections tronquées), la boucle le répare à partir du template, en préservant le contenu au mieux, sans demander à l'agent de réparer.

### 4.2 `ARCHITECTURE.md` séparé

- Les décisions structurantes (stack, schéma de données, conventions) sont stockées dans un fichier dédié, immunisé contre la réécriture à fenêtre glissante de `PROGRESS.md`.
- Un budget de taille/compaction est appliqué à ce fichier pour ne pas grossir indéfiniment le prompt de chaque itération.

### 4.3 Repli de mise à jour via résumé LLM

- Si la session Implement n'a pas mis à jour `PROGRESS.md` (échec de la gate), la boucle génère l'entrée via un appel LLM de synthèse (réutiliser l'infrastructure de `log_summarizer.py`) à partir de `git log`/diff et de la fin du transcript — plus jamais l'injection du stdout brut.
- Sur échec de la synthèse LLM, repli sur le résumé heuristique existant.

### 4.4 Correction de la purge des suggestions

- `SUGGESTIONS.md` n'est vidé que si l'itération a réussi et que l'agent a justifié sa décision (acceptée/rejetée/reportée) dans `PROGRESS.md` — jamais après une itération interrompue ou en échec, pour ne pas perdre silencieusement le feedback utilisateur.

## 5. Vérification Déterministe et Observabilité

### 5.1 Tags git par itération

- Chaque itération pose un tag léger (ex: `debuilder/iter-0012`). Le rollback de l'interface devient possible à granularité d'itération (au lieu du seul dernier commit) et l'historique devient bisectable.

### 5.2 `ITERATIONS.jsonl` (journal machine-readable, append-only)

- Une ligne par itération : horodatage, type de session, modèle, durée, code de sortie, taille du diff, résultats de tests parsés, flag no-op.
- Exploité pour : graphes du dashboard, détection d'anomalies (burn rate), alertes (webhook) sans parser du texte.

### 5.3 Résultats de tests parsés

- La boucle relance elle-même la commande de test du `TASK.md` (avec `--junitxml=...` lorsque possible) et parse le résultat : le « tests passent » auto-déclaré par l'agent n'est plus le seul signal.

### 5.4 Hook pre-commit de tests dans le dépôt cible

- Installer un hook pre-commit qui exécute la suite de tests : garantie déterministe qu'aucun état cassé n'est commité (au lieu d'une simple consigne dans le template).

## 6. Continuité après Échec

- **Injection de la fin du transcript :** en cas d'échec/timeout, les ~200 dernières lignes du transcript sont injectées à la session suivante avec une consigne du type « la session précédente a été interrompue ici, vérifie l'état réel du repo avant de continuer » (l'agent peut croire avoir terminé sa tâche alors qu'il a été tué).
- **Typologie des échecs :** distinguer timeout, erreur API, sortie vide ; adapter le prompt de reprise à chaque cas.


## 7. Circuit Breaker API et Coût

- Après K échecs consécutifs de type API (clé épuisée, provider injoignable), la boucle se met en pause automatique et alerte (dashboard/webhook) au lieu de retenter indéfiniment.
- Bascule optionnelle sur un modèle de secours (`DEBUILDER_MODEL_FALLBACK`).

## 8. Priorités d'Implémentation

1. Tags git par itération (5.1)
2. Gate de tests déterministe (5.3)
3. Circuit breaker API (8)
4. Auto-redémarrage du pod (7)
5. Découpage Plan/Implement avec `TASK.md` (2)
6. Cycle de fin de mission proposition/review (3)
7. Repli mémoire via résumé LLM (4.3)
8. Observabilité `ITERATIONS.jsonl` (5.2)

Les priorités 1 à 4 évitent des pertes irréversibles d'argent ou de travail sur un pod sans surveillance.

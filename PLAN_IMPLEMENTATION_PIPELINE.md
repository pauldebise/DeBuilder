# Plan d'Implémentation : Améliorations du Pipeline DeBuilder

**Source :** `cahier_des_charges_pipeline.md` (découpage Plan/Implement, gates déterministes, fin de mission, fiabilité mémoire, observabilité, circuit breaker).

**Conventions :**
- Chaque tâche est atomique et testable (`python -m pytest`).
- Commits : **Conventional Commits** (`feat:`, `fix:`, `test:`, `chore:`, `docs:`) — voir cahier des charges §2.2.
- Les phases suivent l'ordre de priorité du §8 du cahier des charges. Les priorités 1 à 4 évitent des pertes irréversibles sur un pod sans surveillance ; les phases 5 à 8 transforment la boucle elle-même.
- Note : dans le §8 du cahier des charges, la priorité 3 référence la section « (8) » (lire §7, circuit breaker) et la priorité 4 la section « (7) » (lire §6, continuité après échec + résilience du pod). Ce plan applique cette correspondance.

---

## Phase 0 : Socle commun (prérequis des phases 1–4)

**Objectif :** rendre l'itération identifiable et son résultat structuré, sans changer le comportement.

- [ ] `src/loop/agent_loop.sh` : exporter `DEBUILDER_ITERATION` (compteur) et la passer à `run_iteration()` ; ne plus laisser `set -euo pipefail` tuer la boucle sur erreur (préparation phase 4).
- [ ] `src/loop/agent.py` : `run_iteration()` retourne un résultat structuré (`IterationResult` : code de sortie, type d'échec, durée, diff, no-op, tests) au lieu d'un booléen nu (compat : le booléen reste dérivé pour la boucle).
- [ ] `src/core/iterations.py` (nouveau) : squelette du journal `ITERATIONS.jsonl` (append-only, verrouillé) — écriture minimale dès maintenant (horodatage, numéro, code de sortie), enrichi en phase 8.
- [ ] Tests : `tests/test_iterations.py` (append + verrou), adaptation de `tests/test_agent.py` au nouveau retour.

**Commits :** `refactor:` / `feat: journal d'iteration minimal`.

---

## Phase 1 : Tags git par itération (cdc §5.1 — priorité 1)

**Objectif :** rollback à granularité d'itération, historique bisectable.

- [ ] `src/core/git.py` : `tag_iteration(repo_dir, tag_name)` → `git tag` léger ; `rollback_to_tag(repo_dir, tag_name)` ; `list_iteration_tags(repo_dir)`.
- [ ] `src/loop/agent.py` : après `stage_and_commit_all()` réussi avec un commit effectif, pose `debuilder/iter-NNNN`. Pas de tag sur itération no-op (pas de commit).
- [ ] `src/web/routes_control.py` : étendre le rollback — `GET /api/tags` (liste des tags d'itération) et `POST /api/control/rollback` accepte `{"to": "debuilder/iter-NNNN"}` (défaut : `HEAD~1`).
- [ ] `src/web/static/` : bouton "Rollback" enrichi (choix tag vs dernier commit).
- [ ] Tests : `tests/test_git.py` (tag créé, rollback vers tag, liste), isolation du dépôt DeBuilder.

**Commit type :** `feat:` + `test:`.

---

## Phase 2 : Gate de tests déterministe + hook pre-commit (cdc §5.3 et §5.4 — priorité 2)

**Objectif :** la boucle vérifie elle-même les tests ; aucun état cassé n'est commité.

- [ ] Source de la commande de test : `TASK.md` si présent, sinon section « Commande de test » d'`AGENTS.md`/variable `DEBUILDER_TEST_CMD`, sinon gate ignorée avec avertissement (TASK.md n'existe qu'à partir de la phase 5).
- [ ] `src/utils/test_results.py` (nouveau) : `run_test_gate(target_dir, cmd)` → exécute la commande avec `--junitxml=...` quand possible, parse le XML (failures/errors/skipped/tests) ; `parse_junit(path)`.
- [ ] `src/loop/agent.py` : exécution de la gate après la session Implement ; échec → l'itération n'est pas soldée : entrée `PROGRESS.md` « ECHEC (tests) », motif consigné, injection à la session suivante (cf. phase 4).
- [ ] `src/core/state.py` + `init_project_state()` : création du hook pre-commit (`.git/hooks/pre-commit`) exécutant la suite de tests ; installé uniquement en session vierge (jamais sur un clone, jamais en écrasant un hook existant).
- [ ] Tests : `tests/test_test_results.py` (junit valide/cassé/vide, commande absente), gate intégrée dans `tests/test_agent.py` (mock opencode), hook installé/absent selon le cas.

**Commit type :** `feat:` + `test:`.

---

## Phase 3 : Circuit breaker API et coût (cdc §7 — priorité 3)

**Objectif :** ne jamais retenter indéfiniment sur une API morte ; alerter ; bascule de secours.

- [ ] `src/core/circuit_breaker.py` (nouveau) : classification des échecs (API/quotas → « erreur API », timeout → « timeout », sortie vide → « vide », autre) ; compteur d'échecs API consécutifs ; après K (défaut 3, `DEBUILDER_CB_MAX_FAILURES`) → pause automatique de durée croissante (`DEBUILDER_CB_PAUSE_SECONDS`, ex. 600) avant la prochaine itération ; état persisté dans `$DEBUILDER_STATE_DIR/circuit_breaker.json`.
- [ ] `src/loop/agent.py` : consultation du breaker avant `_run_opencode` ; alimentation après chaque retour (code -1 = timeout, code ≠ 0 avec motifs clé/HTTP = erreur API, stdout vide = vide).
- [ ] Bascule de secours : `DEBUILDER_MODEL_FALLBACK` — si défini, l'itération suivant un déclenchement utilise le modèle de secours (`--model`), retour au principal après succès.
- [ ] Alertes : état du breaker exposé via `GET /api/dashboard` (champ `circuit_breaker`) et encadré d'alerte dans le tableau de bord ; webhook optionnel `DEBUILDER_WEBHOOK_URL` (POST JSON) sur déclenchement/pause.
- [ ] Tests : `tests/test_circuit_breaker.py` (transitions, pause, fallback, persistance).

**Commit type :** `feat:` + `test:`.

---

## Phase 4 : Continuité après échec et résilience du pod (cdc §6 — priorité 4)

**Objectif :** une itération tuée n'empoisonne pas la suivante ; la boucle survit aux pannes.

- [ ] `src/loop/agent.py` : en cas d'échec/timeout, injecter les ~200 dernières lignes d'`OPENCODE_LOG.txt` (sanitisées) dans le prompt suivant, avec une consigne « la session précédente a été interrompue ici, vérifie l'état réel du repo avant de continuer ».
- [ ] Typologie des échecs (partagée avec la phase 3) : message de reprise adapté — timeout (inactivité ou durée max), erreur API, sortie vide (modèle qui n'a rien produit).
- [ ] `src/loop/agent_loop.sh` : backoff exponentiel plafonné entre itérations en échec ; la boucle ne sort que sur `DONE` ou cap dur ; trap de sortie propre (commit d'urgence de l'état).
- [ ] Pod : vérification dans `start.sh` que `tmux` est disponible ; si le processus de la boucle meurt, l'utilisateur peut la relancer via `tmux attach` (documenter). Option : relance auto via `DEBUILDER_AUTO_RESTART=1` (restart de la session tmux).
- [ ] Tests : injection du transcript dans `tests/test_agent.py` (échec puis reprise), backoff testable via une fonction pure.

**Commit type :** `feat:` + `fix:` + `test:`.

---

## Phase 5 : Découpage Plan/Implement avec `TASK.md` et gates (cdc §2 — priorité 5)

**Objectif :** une itération = 2 sessions spécialisées + gates déterministes exécutées par la boucle.

- [ ] `src/core/state.py` : `init_project_state()` crée aussi `TASK.md`, `PLAN.md`, `ARCHITECTURE.md`, `SPEC_COVERAGE.md` (templates dans `templates/`). `STATE_FILES` enrichi.
- [ ] `src/loop/agent.py` : `run_iteration()` orchestrée en 2 appels OpenCode :
  - **Session Plan (lecture seule)** : `OPENCODE_CONFIG_CONTENT` avec `deny` sur `edit`/`write`/`bash` ; lit `PLAN.md`, `SPEC_COVERAGE.md`, état des gates précédentes (arbre git propre, tests verts, cases `TASK.md`) ; écrit `TASK.md` (objectif, critères d'acceptation, commande de test exacte, cases à cocher) et met à jour `PLAN.md`.
  - **Session Implement** : lit `TASK.md` (pas le cahier des charges), implémente, teste, committe par sous-tâche (Conventional Commits), coche les cases en justifiant ; dernière étape obligatoire : mise à jour de `PROGRESS.md` (et `BENCHMARKS.md`, `ARCHITECTURE.md` le cas échéant).
- [ ] `src/utils/task_parser.py` (nouveau) : `parse_task()` (objectif, critères, commande de test, cases) ; `all_boxes_checked()` ; `test_command()`.
- [ ] **Gates déterministes après Implement** (boucle, pas l'agent) : cases toutes cochées ? commande de test verte (phase 2) ? `PROGRESS.md` modifié ? Échec → tâche non soldée, motif consigné (fichier `GATE_FAILURE.md` + `ITERATIONS.jsonl`) et injecté à la session suivante.
- [ ] Purge `SUGGESTIONS.md` corrigée (cdc §4.4) : vidé seulement si itération réussie **et** justification (acceptée/rejetée/reportée) présente dans `PROGRESS.md` — jamais après échec/interruption.
- [ ] Tests : `tests/test_task_parser.py`, gates dans `tests/test_agent.py` (mock opencode écrivant TASK.md), lecture seule vérifiée (config injectée), purge des suggestions (4.4).

**Commit type :** `feat:` + `test:` (chaque sous-tâche = un commit).

---

## Phase 6 : Cycle de fin de mission proposition/review (cdc §3 — priorité 6)

**Objectif :** la fin de mission est un fait vérifiable, validé par le pipeline, pas par l'agent.

- [ ] `src/core/state.py` : templates `FINISHED_REPORT.md` (checklist cahier des charges → items réalisés, validés par tests) et `REVIEW.md` (feedback de rejet).
- [ ] `src/loop/agent.py` : quand l'agent estime la DoD atteinte, il écrit `FINISHED_REPORT.md` (jamais `DONE` lui-même). Une session **Review (lecture seule)** compare le rapport à `SPEC_COVERAGE.md` et au cahier des charges : validé → **la boucle crée `DONE`** ; sinon `REVIEW.md` écrit et la boucle repart (kill-switch manuel prioritaire).
- [ ] `SPEC_COVERAGE.md` : le planner maintient le mapping cahier des charges → item implémenté + test associé ; fin de mission = « couverture 100 % + tests verts ».
- [ ] **No-op :** diff de l'itération limité aux fichiers d'état (ou vide) → compteur no-op. Après N consécutifs (défaut 3, `DEBUILDER_MAX_NOOPS`) : le planner doit déclarer la fin ou justifier la poursuite ; notification utilisateur.
- [ ] **Caps durs :** `DEBUILDER_MAX_ITERATIONS` et budget de temps global (`DEBUILDER_MAX_HOURS`) ; arrêt propre au dépassement + notification (dashboard + webhook).
- [ ] Tests : `tests/test_agent.py` (review accepte/refuse, création de `DONE` par la boucle seule), détection no-op (diff simulé), caps.

**Commit type :** `feat:` + `test:`.

---

## Phase 7 : Fiabilité de la mémoire persistante (cdc §4.1–4.3 — priorité 7)

**Objectif :** les fichiers d'état ne peuvent plus être corrompus ni perdus silencieusement.

- [ ] **4.1 Réparation déterministe** : `src/core/state.py::repair_progress(target_dir)` — détecte `PROGRESS.md` malformé (séparateur absent, sections tronquées) et répare depuis `PROGRESS.md.tmpl` en préservant le contenu au mieux ; appelée par la boucle avant chaque lecture.
- [ ] **4.2 `ARCHITECTURE.md` séparé** : fichier dédié aux décisions structurantes (stack, schéma de données, conventions), immunisé contre la fenêtre glissante ; budget de taille/compaction (les entrées les plus anciennes sont compactées en une ligne) ; injecté dans le prompt à chaque itération.
- [ ] **4.3 Repli par résumé LLM** : dans `_update_state_files`, remplacer l'injection du stdout brut par un appel LLM de synthèse (`src/core/log_summarizer.py` généralisé ou `synthesize_progress_entry()` : entrée = `git log`/diff + fin de transcript → entrée `PROGRESS.md` structurée). Échec LLM → repli heuristique existant (jamais plus le stdout brut).
- [ ] Tests : `tests/test_state.py` (réparation sur fichiers corrompus), compaction d'`ARCHITECTURE.md`, synthèse avec provider mocké et repli heuristique.

**Commit type :** `feat:` + `fix:` + `test:`.

---

## Phase 8 : Observabilité `ITERATIONS.jsonl` (cdc §5.2 — priorité 8)

**Objectif :** un journal machine-readable exploitable sans parser du texte.

- [ ] `src/core/iterations.py` : compléter la ligne journalisée — horodatage, numéro, type de session (plan/implement/review), modèle, durée, code de sortie, type d'échec, taille du diff, résultats de tests parsés (phase 2), flag no-op, tags posés.
- [ ] `src/web/routes_dashboard.py` : `GET /api/iterations` (lecture du JSONL, bornée par `?limit=`).
- [ ] `src/web/static/` : section « Itérations » du tableau de bord — graphe simple (vanilla JS) durée/code de sortie par itération, détection d'anomalies basique (burn rate : coût par itération si le modèle expose des tokens).
- [ ] Alertes : webhook réutilisé de la phase 3 (échecs répétés, no-ops, caps).
- [ ] Tests : `tests/test_iterations.py` complétés (lignes conformes, lecture bornée), route FastAPI.

**Commit type :** `feat:` + `test:`.

---

## Phase 9 : Intégration, tests bout-en-bout, documentation

- [ ] Test d'intégration bout-en-bout avec un faux `opencode` (script shell piloté par scénario) : session vierge → Plan → Implement → gates → tags → fin de mission → `DONE` créé par la boucle.
- [ ] Scénarios d'échec : timeout, erreur API ×K → pause circuit breaker, no-op ×N → fin forcée.
- [ ] `README.md` : documentation des nouvelles variables d'environnement (`DEBUILDER_MAX_ITERATIONS`, `DEBUILDER_MAX_NOOPS`, `DEBUILDER_CB_*`, `DEBUILDER_MODEL_FALLBACK`, `DEBUILDER_WEBHOOK_URL`, `DEBUILDER_TEST_CMD`), des nouveaux fichiers d'état (`TASK.md`, `PLAN.md`, `ARCHITECTURE.md`, `SPEC_COVERAGE.md`, `FINISHED_REPORT.md`, `REVIEW.md`, `GATE_FAILURE.md`) et du rollback par tag.
- [ ] Revue finale : toutes les cases du cahier des charges §1–§8 couvertes (via `SPEC_COVERAGE.md`).

**Commit type :** `test:` + `docs:`.

---

## Récapitulatif des priorités (miroir du §8 du cahier des charges)

| # | Phase | Amélioration | Section cdc | Type de risque évité |
|---|-------|--------------|-------------|----------------------|
| 1 | 1 | Tags git par itération | 5.1 | Perte de travail irrécupérable |
| 2 | 2 | Gate de tests déterministe | 5.3 / 5.4 | État cassé commité |
| 3 | 3 | Circuit breaker API | 7 | Argent brûlé sur API morte |
| 4 | 4 | Continuité après échec / pod | 6 | Boucle figée sans surveillance |
| 5 | 5 | Découpage Plan/Implement + TASK.md | 2 | Surcharge contextuelle |
| 6 | 6 | Fin de mission proposition/review | 3 | Boucle sans fin |
| 7 | 7 | Repli mémoire par résumé LLM | 4.1–4.3 | Mémoire corrompue |
| 8 | 8 | Observabilité ITERATIONS.jsonl | 5.2 | Cécité sur le burn rate |

Les phases 0 et 9 sont transverses (socle et intégration). Chaque phase se termine par des tests verts avant de passer à la suivante.

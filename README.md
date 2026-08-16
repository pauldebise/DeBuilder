# DeBuilder

**DeBuilder** est un wrapper local pour [OpenCode](https://opencode.ai), conçu pour des travaux de développement et de machine learning. Il orchestre un agent d'IA autonome qui travaille en arrière-plan sur un projet cible, tout en exposant une interface web (FastAPI + HTML/JS vanilla) pour le superviser et intervenir de manière asynchrone.

L'agent tourne en boucle, itération après itération, sans mémoire de contexte interne : toute la continuité de son travail passe par des fichiers d'état Markdown lus et écrits dans le dépôt du projet cible.

## Sommaire

- [Concepts clés](#concepts-clés)
- [Architecture](#architecture)
- [Prérequis](#prérequis)
- [Installation et démarrage](#installation-et-démarrage)
- [Utilisation de l'interface](#utilisation-de-linterface)
- [Fichiers d'état du projet cible](#fichiers-détat-du-projet-cible)
- [Configuration (variables d'environnement)](#configuration-variables-denvironnement)
- [Sécurité](#sécurité)
- [Développement](#développement)
- [Structure du dépôt](#structure-du-dépôt)

## Concepts clés

- **Isolation stricte** : le code de DeBuilder (interface web, scripts d'orchestration) vit dans son propre dépôt Git, totalement séparé du projet cible sur lequel l'agent travaille. L'agent n'a ni la permission ni le besoin de lire ou modifier le code de DeBuilder.
- **Autonomie complète** : l'agent commit et pousse son travail à chaque itération, uniquement sur le dépôt du projet cible. Il ne bloque jamais en attendant une réponse humaine — s'il lui manque une ressource, il applique une solution de contournement et le signale.
- **Communication par fichiers** : GUI et boucle agent ne communiquent jamais directement en mémoire. Tout passe par le système de fichiers (`AGENTS.md`, `PROGRESS.md`, `BENCHMARKS.md`, ...), protégé par du file locking pour éviter toute corruption entre l'écriture de l'agent et le polling de l'interface.
- **Conscience matérielle** : au démarrage d'une session, DeBuilder audite la machine hôte (CPU, RAM, GPU) et transmet ces informations à l'agent, qui adapte ses décisions d'implémentation en conséquence (ex : entraîner un modèle si un GPU est disponible).
- **Veille en ligne** : les outils web d'OpenCode (`websearch`, `webfetch`) sont activés pour l'agent, qui doit vérifier en ligne les informations externes susceptibles d'avoir changé (documentation et signatures d'API, versions de paquets, bonnes pratiques, datasets et modèles disponibles) plutôt que de se fier à ses seules connaissances d'entraînement.

## Architecture

```
┌────────────────────────────┐                                ┌──────────────────────────┐
│  Frontend (navigateur)     │                                │  Boucle agent            │
│  src/web/static/           │                                │  (src/loop/agent_loop.sh │
│  HTML/JS vanilla, SSE      │                                │   + src/loop/agent.py)   │
└─────────────┬───────────────┘                                └───────────┬──────────────┘
              │ HTTP (REST) + SSE (/api/logs/stream)                       │
              v                                                            │
┌────────────────────────────┐         fichiers d'état                    │
│  Backend FastAPI            │ <----------------------------------------->│
│  (src/web/app.py,           │   AGENTS.md, PROGRESS.md,                 │
│   src/web/routes_*.py)      │   BENCHMARKS.md, SUGGESTIONS.md,          │
│  port 7680                  │   RESOURCES_NEEDED.md, DONE               │
└────────────────────────────┘                                 opencode run
                                                                            │
                                                                            v
                                                                 ┌──────────────────┐
                                                                 │  Dépôt du projet │
                                                                 │  cible (Git)     │
                                                                 └──────────────────┘
```

Le backend FastAPI et la boucle agent ne communiquent jamais directement en mémoire : tout passe par les fichiers d'état ci-dessus, exactement comme avec l'ancienne interface Gradio qu'il remplace. Le dépôt de DeBuilder et le dépôt du projet cible restent deux dépôts Git indépendants tout au long de la session.

## Prérequis

- Python 3.10+
- Git
- [OpenCode](https://opencode.ai) (`curl -fsSL https://opencode.ai/install | bash`)
- Une clé API pour un fournisseur supporté (DeepSeek, OpenAI ou Anthropic)
- `tmux` (recommandé, pour la persistance de session en cas de déconnexion)

`start.sh` détecte et installe automatiquement les dépendances manquantes (pip, FastAPI/uvicorn, OpenCode, tmux) sur les images minimales type RunPod.

## Installation et démarrage

```bash
git clone https://github.com/pauldebise/DeBuilder debuilder
cd debuilder
./start.sh
```

Le script :
1. détecte un interpréteur Python compatible (3.10 à 3.14),
2. installe les dépendances manquantes si besoin,
3. lance l'interface web (FastAPI/uvicorn) dans une session `tmux` nommée `debuilder` (persistante en cas de déconnexion).

```bash
tmux attach -t debuilder   # rattacher la session
tmux kill-session -t debuilder && ./start.sh   # relancer après une mise à jour du code
```

Sur un pod sans surveillance, `DEBUILDER_AUTO_RESTART=1 ./start.sh` relance automatiquement le processus web s'il meurt (crash, OOM). Si la boucle agent elle-même s'arrête, rattachez-vous à la session tmux (`tmux attach -t debuilder`) et relancez une session depuis l'interface : la boucle ne tourne pas dans tmux, elle est lancée par le serveur web.

L'interface est ensuite disponible sur `http://<host>:7680` (port configurable via `DEBUILDER_PORT`).

## Utilisation de l'interface

L'interface web tient sur une seule page, sans navigation par onglets pour l'essentiel — seuls la Progression, les Benchmarks et les Itérations ont un onglet dédié en haut de page :

| Zone / onglet | Rôle |
|---|---|
| **Écran de configuration** | Affiché uniquement en l'absence de session active : cloner un dépôt Git ou initialiser un projet vierge, définir le cahier des charges initial (→ `AGENTS.md`), choisir le fournisseur/modèle IA et fournir la clé API, et un panneau « Paramètres avancés » (caps d'itérations et de temps, accès internet de l'agent, modèle de secours, commande de test), puis lancer la boucle agent en arrière-plan (`POST /api/session/start`). |
| **Tableau de bord (zone principale)** | Badge d'état de la boucle en haut de page (active avec itération en cours / arrêt en cours / mission terminée / arrêtée / interrompue — via heartbeat `~/.debuilder/heartbeat.json` + PID `~/.debuilder/loop_pid.txt`), résumé en langage naturel de l'activité en cours (généré par LLM ou par heuristique de repli), avancement (`PROGRESS.md` parsé), alertes watchdog/système (y compris l'état du circuit breaker API), et flux des logs OpenCode quasi temps réel (Server-Sent Events sur `/api/logs/stream`, style terminal). |
| **Tableau de bord (zone latérale)** | Requêtes agent (`RESOURCES_NEEDED.md` + réponse courte), boîte aux lettres de suggestions (`SUGGESTIONS.md`), contrôles (arrêt d'urgence, rollback vers un tag d'itération ou le dernier commit, barrières Human-in-the-Loop), résumé des dernières métriques de `BENCHMARKS.md`. |
| **Onglet Progression** | Contenu intégral et à jour de `PROGRESS.md`, rendu en Markdown. |
| **Onglet Benchmarks** | Contenu intégral et à jour de `BENCHMARKS.md`, rendu en Markdown. |
| **Onglet Itérations** | Journal machine-readable `ITERATIONS.jsonl` (via `GET /api/iterations`) : statistiques (échecs, no-ops, durée moyenne, burn rate si le modèle expose ses tokens), graphe durée/statut par itération (canvas vanilla), détection d'anomalies basique (échecs et no-ops consécutifs, durée ou consommation de tokens anormales). |

La session active est mémorisée (`~/.debuilder/last_session.txt`) : un F5 pendant qu'une session tourne revient directement sur le tableau de bord (le backend est interrogé au chargement via `GET /api/session`), sans repasser par l'écran de configuration — y compris si l'interface a redémarré alors que la boucle agent tournait encore en arrière-plan.

## Fichiers d'état du projet cible

Ces fichiers sont créés dans le répertoire du projet cible (jamais dans celui de DeBuilder) et constituent le seul canal de communication entre l'agent et la GUI :

| Fichier | Écrit par | Rôle |
|---|---|---|
| `AGENTS.md` | GUI (une fois) | Objectif du projet, règles générales, audit matériel. |
| `PROGRESS.md` | Agent | Journal de bord à fenêtre glissante (2 dernières itérations). Action réalisée, résultat, problèmes rencontrés, prochaine sous-tâche. Réparé déterministiquement depuis le template s'il est malformé. |
| `BENCHMARKS.md` | Agent | Résultats de runs et métriques factuelles (temps, scores, usage hardware) sous forme de tableaux Markdown, pour éviter toute régression. |
| `TASK.md` | Session Plan → boucle | Contrat de tâche de l'itération (objectif, critères d'acceptation, commande de test exacte, sous-tâches en cases à cocher), consommé par la session Implement et vérifié par les gates de la boucle. |
| `PLAN.md` | Session Plan → boucle | Backlog persistant (tâches restantes par priorité), mis à jour à chaque itération. |
| `ARCHITECTURE.md` | Agent | Décisions structurantes (stack, schéma de données, conventions), protégées de la fenêtre glissante de `PROGRESS.md` ; compactées au-delà d'un budget de taille. |
| `SPEC_COVERAGE.md` | Session Plan | Mapping cahier des charges → item implémenté + test associé. La fin de mission est « couverture 100 % + tests verts ». |
| `FINISHED_REPORT.md` | Agent | Checklist du cahier des charges cochée quand l'agent estime la Definition of Done atteinte ; validée par une session Review avant que la boucle ne crée `DONE`. |
| `REVIEW.md` | Boucle | Feedback de rejet du rapport de fin de mission (injecté à la session suivante). |
| `GATE_FAILURE.md` | Boucle | Motifs d'échec des gates déterministes de l'itération (cases non cochées, `PROGRESS.md` non mis à jour, tests rouges), injectés à la session suivante. |
| `SUGGESTIONS.md` | GUI | Directives humaines lues par l'agent en début d'itération, vidé après traitement (uniquement après itération réussie avec justification). |
| `RESOURCES_NEEDED.md` | Agent | Demandes de ressources "bonus", jamais bloquantes. |
| `DONE` | GUI (kill-switch) ou boucle (fin de mission validée) | Présence du fichier → arrêt propre de la boucle en fin d'itération en cours. |
| `BARRIER_<type>` | GUI | Point d'arrêt Human-in-the-Loop : l'agent attend sa suppression avant de poursuivre l'opération concernée. |
| `OPENCODE_LOG.txt` | Boucle agent | Sortie brute d'OpenCode (tronquée automatiquement au-delà de 5 Mo). Jamais commité (voir `.gitignore` du projet cible). |
| `ITERATIONS.jsonl` | Boucle agent | Journal machine-readable append-only (une ligne JSON par itération : sessions, modèle, durée, code de sortie, taille du diff, tests parsés, flag no-op, tags). Jamais commité. |

## Configuration (variables d'environnement)

| Variable | Défaut | Description |
|---|---|---|
| `DEBUILDER_PORT` | `7680` | Port d'écoute de l'interface web. |
| `DEBUILDER_MODEL` | — | Modèle OpenCode actif, format `fournisseur/modele` (défini automatiquement au démarrage d'une session). |
| `DEBUILDER_STATE_DIR` | `~/.debuilder` | Répertoire de persistance de la session active (hors dépôts Git). |
| `DEBUILDER_OPENCODE_INACTIVITY_TIMEOUT` | `600` (10 min) | Délai max sans nouvelle sortie d'OpenCode avant de tuer l'itération (processus réellement bloqué). |
| `DEBUILDER_OPENCODE_MAX_SECONDS` | `10800` (3h) | Plafond absolu de durée d'une itération, même si OpenCode continue de produire de la sortie. |
| `DEBUILDER_WEB_TOOLS` | `1` | Accès internet de l'agent (`websearch` + `webfetch`). Mettre à `0` pour le couper entièrement. |
| `DEBUILDER_MAX_ITERATIONS` | `0` (illimité) | Cap dur : nombre maximum d'itérations avant arrêt propre de la boucle, avec notification (dashboard + webhook). |
| `DEBUILDER_MAX_HOURS` | `0` (illimité) | Cap dur : budget de temps global de la boucle avant arrêt propre. |
| `DEBUILDER_MAX_NOOPS` | `3` | Itérations no-op consécutives (diff limité aux fichiers d'état) avant d'exiger la fin de mission ou la replanification. |
| `DEBUILDER_MAX_CONSECUTIVE_FAILURES` | `3` | Échecs consécutifs (tous types) avant notification webhook `repeated_failures`. |
| `DEBUILDER_BACKOFF_CAP_SECONDS` | `300` | Plafond du backoff exponentiel entre itérations en échec. |
| `DEBUILDER_CB_MAX_FAILURES` | `3` | Échecs API consécutifs avant ouverture du circuit breaker (pause automatique). |
| `DEBUILDER_CB_PAUSE_SECONDS` | `600` | Pause initiale du circuit breaker ; doublée à chaque ouverture (plafonnée). |
| `DEBUILDER_MODEL_FALLBACK` | — | Modèle de secours utilisé tant que le circuit breaker est ouvert ; retour au principal après un succès. |
| `DEBUILDER_WEBHOOK_URL` | — | URL optionnelle (POST JSON, fire-and-forget) recevant les alertes : ouverture du circuit breaker, échecs répétés, no-ops répétés, cap dur atteint. |
| `DEBUILDER_TEST_CMD` | — | Commande de test exécutée par la gate déterministe de la boucle si ni `TASK.md` ni `AGENTS.md` n'en définissent une (et par le hook pre-commit). |
| `DEBUILDER_TEST_GATE_TIMEOUT` | `1800` | Durée maximale de la gate de tests avant de tuer la suite. |
| `DEBUILDER_ARCH_MAX_CHARS` | `4000` | Budget de taille d'`ARCHITECTURE.md` avant compaction des entrées anciennes. |

### Recherche en ligne

À chaque itération, la boucle agent injecte dans l'environnement d'OpenCode :

- `OPENCODE_ENABLE_EXA=1` : OpenCode n'expose l'outil `websearch` que si le modèle vient du fournisseur `opencode` (Zen) ou si un backend de recherche est activé. DeBuilder tournant avec DeepSeek / OpenAI / Anthropic, l'outil serait sinon absent de la liste présentée au modèle. Le backend Exa est un service MCP hébergé qui ne demande aucune clé API. Un réglage explicite de l'utilisateur (`OPENCODE_WEBSEARCH_PROVIDER`, `OPENCODE_ENABLE_PARALLEL`, ...) est respecté et n'est jamais écrasé.
- `OPENCODE_CONFIG_CONTENT` : config inline accordant les permissions `webfetch` et `websearch`. Ce canal (priorité la plus haute dans l'ordre de fusion d'OpenCode) évite d'écrire un `opencode.json` dans le dépôt du projet cible — l'isolation entre les deux dépôts est préservée — et garantit l'accès web même si un `deny` traîne dans la configuration du projet cible. Une valeur déjà présente est fusionnée, pas remplacée.

L'outil `webfetch` est disponible dans OpenCode quel que soit le fournisseur : seule sa permission a besoin d'être accordée.

Les clés API des fournisseurs (`DEEPSEEK_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`) sont saisies depuis l'écran de configuration et injectées comme variables d'environnement éphémères : **elles ne sont jamais écrites sur disque.**

## Sécurité

- Toute sortie affichée dans l'interface web (y compris le flux de logs SSE) ou écrite dans les logs/commits passe par `sanitize_text()`, qui masque les valeurs de toute variable d'environnement dont le nom contient `KEY`, `SECRET`, `TOKEN`, `PASSWORD` ou `API`.
- Les fichiers opérationnels de DeBuilder (`DONE`, `BARRIER_*`, `*.lock`, `OPENCODE_LOG.txt`, `ITERATIONS.jsonl`) sont exclus des commits sur le dépôt du projet cible.
- L'accès internet de l'agent sort de la machine : les requêtes `websearch` transitent par le service hébergé Exa et `webfetch` contacte directement les URL demandées. L'agent est explicitement instruit de ne jamais placer de secret ni de code propriétaire du projet dans une requête. Sur un projet sensible, couper l'accès avec `DEBUILDER_WEB_TOOLS=0`.
- L'agent n'a jamais accès en lecture ou écriture au dépôt Git de DeBuilder lui-même ; toutes les opérations Git de la boucle agent (`commit`, `push`, `rollback`) ciblent exclusivement le répertoire du projet.

## Développement

```bash
pip install -r requirements.txt
python -m pytest
```

La suite de tests couvre le file locking, la gestion des fichiers d'état, les secrets, le parsing Markdown, l'audit matériel, la logique d'itération de l'agent (sessions Plan/Implement/Review, gates, circuit breaker, no-ops, caps), les routes FastAPI (session, contrôles, journal d'itérations, flux SSE des logs), et des tests bout-en-bout du pipeline complet qui lancent la vraie boucle `agent_loop.sh` avec un faux `opencode` piloté par scénario (`tests/fixtures/fake_opencode.sh`) : mission complète jusqu'à `DONE`, timeout, erreurs API → circuit breaker, no-ops → fin forcée (`tests/`).

## Structure du dépôt

```
src/
├── core/
│   ├── filelock.py            # Verrouillage de fichiers (fcntl)
│   ├── git.py                 # Opérations Git sur le dépôt cible (clone, commit, push, rollback, tags)
│   ├── circuit_breaker.py     # Pause automatique après K échecs API + modèle de secours + webhook
│   ├── iterations.py          # Journal ITERATIONS.jsonl (append-only, verrouillé) et schéma des lignes
│   ├── log_summarizer.py      # Résumé en langage naturel des logs (LLM + repli heuristique)
│   ├── secrets.py             # Injection et sanitization des secrets
│   ├── session.py             # Persistance de la session active entre redémarrages
│   └── state.py               # Lecture/écriture des fichiers d'état, fenêtre glissante de PROGRESS.md, réparation déterministe
├── web/
│   ├── app.py                  # Point d'entrée FastAPI (sert /static et /, monte les routes)
│   ├── routes_session.py       # GET /api/session, POST /api/session/start
│   ├── routes_dashboard.py     # GET /api/dashboard, /api/progress, /api/benchmarks, /api/iterations
│   ├── routes_control.py       # POST /api/suggestions, /api/control/{kill,rollback,barrier}, GET /api/tags
│   ├── routes_requests.py      # GET /api/requests, POST /api/requests/respond
│   ├── routes_logs.py          # GET /api/logs/stream (Server-Sent Events)
│   └── static/                 # Frontend HTML/JS/CSS vanilla (index.html, app.js, style.css)
├── loop/
│   ├── agent_loop.sh           # Boucle shell : itérations Plan/Implement, caps durs, backoff, sortie propre
│   └── agent.py                # Orchestration des sessions, gates déterministes, journal d'itérations
└── utils/
    ├── hw_audit.py             # Audit matériel (CPU/RAM/GPU)
    ├── markdown_parser.py      # Extraction des sections PROGRESS/BENCHMARKS/alertes
    ├── task_parser.py          # Parsing du contrat TASK.md (cases, commande de test)
    ├── test_results.py         # Gate de tests déterministe (exécution + parse JUnit)
    └── text.py                 # Nettoyage ANSI, lecture de fin de fichier

templates/                      # Gabarits Markdown initiaux (AGENTS.md, PROGRESS.md, TASK.md, ...) et hook pre-commit
tests/                          # Suite de tests (unitaires + bout-en-bout avec un faux opencode)
cahier_des_charges.md           # Spécification fonctionnelle complète du projet
cahier_des_charges_pipeline.md  # Cahier des charges des améliorations du pipeline
PLAN_IMPLEMENTATION_PIPELINE.md # Découpage de l'implémentation du pipeline en phases
SPEC_COVERAGE.md                # Mapping cahier des charges pipeline → implémentation + tests
ROADMAP.md                      # Découpage du développement en jalons
```

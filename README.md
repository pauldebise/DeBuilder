# DeBuilder

**DeBuilder** est un wrapper local pour [OpenCode](https://opencode.ai), conçu pour des travaux de développement et de machine learning. Il orchestre un agent d'IA autonome qui travaille en arrière-plan sur un projet cible, tout en exposant une interface web (Gradio) pour le superviser et intervenir de manière asynchrone.

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

- **Isolation stricte** : le code de DeBuilder (interface Gradio, scripts d'orchestration) vit dans son propre dépôt Git, totalement séparé du projet cible sur lequel l'agent travaille. L'agent n'a ni la permission ni le besoin de lire ou modifier le code de DeBuilder.
- **Autonomie complète** : l'agent commit et pousse son travail à chaque itération, uniquement sur le dépôt du projet cible. Il ne bloque jamais en attendant une réponse humaine — s'il lui manque une ressource, il applique une solution de contournement et le signale.
- **Communication par fichiers** : GUI et boucle agent ne communiquent jamais directement en mémoire. Tout passe par le système de fichiers (`AGENTS.md`, `PROGRESS.md`, `BENCHMARKS.md`, ...), protégé par du file locking pour éviter toute corruption entre l'écriture de l'agent et le polling de l'interface.
- **Conscience matérielle** : au démarrage d'une session, DeBuilder audite la machine hôte (CPU, RAM, GPU) et transmet ces informations à l'agent, qui adapte ses décisions d'implémentation en conséquence (ex : entraîner un modèle si un GPU est disponible).

## Architecture

```
┌─────────────────────────┐         fichiers d'état          ┌──────────────────────────┐
│   Interface Gradio      │ <----------------------------->  │  Boucle agent            │
│   (src/app.py, src/gui) │   AGENTS.md, PROGRESS.md,        │   (src/loop/agent_loop.sh│
│   port 7680             │   BENCHMARKS.md, SUGGESTIONS.md, │    + src/loop/agent.py)  │
│                         │   RESOURCES_NEEDED.md, DONE      │                          │
└─────────────────────────┘                                  └───────────┬──────────────┘
                                                                         │ opencode run
                                                                         v
                                                                 ┌──────────────────┐
                                                                 │  Dépôt du projet │
                                                                 │  cible (Git)     │
                                                                 └──────────────────┘
```

Le dépôt de DeBuilder et le dépôt du projet cible restent deux dépôts Git indépendants tout au long de la session.

## Prérequis

- Python 3.10+
- Git
- [OpenCode](https://opencode.ai) (`curl -fsSL https://opencode.ai/install | bash`)
- Une clé API pour un fournisseur supporté (DeepSeek, OpenAI ou Anthropic)
- `tmux` (recommandé, pour la persistance de session en cas de déconnexion)

`start.sh` détecte et installe automatiquement les dépendances manquantes (pip, Gradio, OpenCode, tmux) sur les images minimales type RunPod.

## Installation et démarrage

```bash
git clone https://github.com/pauldebise/DeBuilder debuilder
cd debuilder
./start.sh
```

Le script :
1. détecte un interpréteur Python compatible (3.10 à 3.14),
2. installe les dépendances manquantes si besoin,
3. lance l'interface Gradio dans une session `tmux` nommée `debuilder` (persistante en cas de déconnexion).

```bash
tmux attach -t debuilder   # rattacher la session
tmux kill-session -t debuilder && ./start.sh   # relancer après une mise à jour du code
```



L'interface est ensuite disponible sur `http://<host>:7680` (port configurable via `DEBUILDER_PORT`).

## Utilisation de l'interface

L'interface Gradio est organisée en cinq onglets :

| Onglet | Rôle |
|---|---|
| **Configuration** | Démarrer une session : cloner un dépôt Git ou initialiser un projet vierge, définir le cahier des charges initial (→ `AGENTS.md`), choisir le fournisseur/modèle IA et fournir la clé API, lancer la boucle agent en arrière-plan. |
| **Tableau de Bord** | Suivi en lecture seule : résumé en langage naturel de l'activité en cours (généré par LLM ou par heuristique de repli), avancement (`PROGRESS.md`), métriques (`BENCHMARKS.md`), alertes watchdog et alertes système. Rafraîchissement automatique toutes les 30s. |
| **Centre de Contrôle** | Intervention asynchrone : boîte aux lettres pour envoyer des suggestions à l'agent (`SUGGESTIONS.md`), arrêt d'urgence (kill-switch), rollback du dernier commit (`git reset --hard HEAD~1`), activation de barrières Human-in-the-Loop sur des types d'opérations sensibles. |
| **Requêtes Agent** | Consultation des demandes de ressources émises par l'agent (`RESOURCES_NEEDED.md`, non bloquantes par conception) et envoi d'une réponse. |
| **Logs Systèmes** | Affichage brut des 200 dernières lignes de `OPENCODE_LOG.txt`, pour le débogage approfondi. |

La session active est mémorisée (`~/.debuilder/last_session.txt`) : si l'interface redémarre alors que la boucle agent tourne encore en arrière-plan, les onglets s'y rattachent automatiquement.

## Fichiers d'état du projet cible

Ces fichiers sont créés dans le répertoire du projet cible (jamais dans celui de DeBuilder) et constituent le seul canal de communication entre l'agent et la GUI :

| Fichier | Écrit par | Rôle |
|---|---|---|
| `AGENTS.md` | GUI (une fois) | Objectif du projet, règles générales, audit matériel. |
| `PROGRESS.md` | Agent | Journal de bord à fenêtre glissante (2 dernières itérations). Action réalisée, résultat, problèmes rencontrés, prochaine sous-tâche. |
| `BENCHMARKS.md` | Agent | Résultats de runs et métriques factuelles (temps, scores, usage hardware) sous forme de tableaux Markdown, pour éviter toute régression. |
| `SUGGESTIONS.md` | GUI | Directives humaines lues par l'agent en début d'itération, vidé après traitement. |
| `RESOURCES_NEEDED.md` | Agent | Demandes de ressources "bonus", jamais bloquantes. |
| `DONE` | GUI (kill-switch) | Présence du fichier → arrêt propre de la boucle en fin d'itération en cours. |
| `BARRIER_<type>` | GUI | Point d'arrêt Human-in-the-Loop : l'agent attend sa suppression avant de poursuivre l'opération concernée. |
| `OPENCODE_LOG.txt` | Boucle agent | Sortie brute d'OpenCode (tronquée automatiquement au-delà de 5 Mo). Jamais commité (voir `.gitignore` du projet cible). |

## Configuration (variables d'environnement)

| Variable | Défaut | Description |
|---|---|---|
| `DEBUILDER_PORT` | `7680` | Port d'écoute de l'interface Gradio. |
| `DEBUILDER_MODEL` | — | Modèle OpenCode actif, format `fournisseur/modele` (défini automatiquement au démarrage d'une session). |
| `DEBUILDER_STATE_DIR` | `~/.debuilder` | Répertoire de persistance de la session active (hors dépôts Git). |
| `DEBUILDER_OPENCODE_INACTIVITY_TIMEOUT` | `600` (10 min) | Délai max sans nouvelle sortie d'OpenCode avant de tuer l'itération (processus réellement bloqué). |
| `DEBUILDER_OPENCODE_MAX_SECONDS` | `10800` (3h) | Plafond absolu de durée d'une itération, même si OpenCode continue de produire de la sortie. |

Les clés API des fournisseurs (`DEEPSEEK_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`) sont saisies depuis l'onglet Configuration et injectées comme variables d'environnement éphémères : **elles ne sont jamais écrites sur disque.**

## Sécurité

- Toute sortie affichée dans la GUI ou écrite dans les logs/commits passe par `sanitize_text()`, qui masque les valeurs de toute variable d'environnement dont le nom contient `KEY`, `SECRET`, `TOKEN`, `PASSWORD` ou `API`.
- Les fichiers opérationnels de DeBuilder (`DONE`, `BARRIER_*`, `*.lock`, `OPENCODE_LOG.txt`) sont exclus des commits sur le dépôt du projet cible.
- L'agent n'a jamais accès en lecture ou écriture au dépôt Git de DeBuilder lui-même ; toutes les opérations Git de la boucle agent (`commit`, `push`, `rollback`) ciblent exclusivement le répertoire du projet.

## Développement

```bash
pip install -r requirements.txt
python -m pytest
```

La suite de tests couvre le file locking, la gestion des fichiers d'état, les secrets, le parsing Markdown, l'audit matériel et la logique d'itération de l'agent (`tests/`).

## Structure du dépôt

```
src/
├── app.py                     # Point d'entrée Gradio (assemble les 5 onglets)
├── core/
│   ├── filelock.py            # Verrouillage de fichiers (fcntl)
│   ├── git.py                 # Opérations Git sur le dépôt cible (clone, commit, push, rollback)
│   ├── log_summarizer.py      # Résumé en langage naturel des logs (LLM + repli heuristique)
│   ├── secrets.py             # Injection et sanitization des secrets
│   ├── session.py             # Persistance de la session active entre redémarrages
│   └── state.py               # Lecture/écriture des fichiers d'état, fenêtre glissante de PROGRESS.md
├── gui/
│   ├── config.py               # Onglet Configuration
│   ├── dashboard.py            # Onglet Tableau de Bord
│   ├── control.py              # Onglet Centre de Contrôle
│   ├── agents.py               # Onglet Requêtes Agent
│   └── logs.py                 # Onglet Logs Systèmes
├── loop/
│   ├── agent_loop.sh           # Boucle shell : une itération OpenCode à la fois
│   └── agent.py                # Construction du prompt, exécution d'OpenCode, mise à jour de l'état
└── utils/
    ├── hw_audit.py             # Audit matériel (CPU/RAM/GPU)
    ├── markdown_parser.py      # Extraction des sections PROGRESS/BENCHMARKS/alertes
    └── text.py                 # Nettoyage ANSI, lecture de fin de fichier

templates/                      # Gabarits Markdown initiaux (AGENTS.md, PROGRESS.md)
tests/                          # Suite de tests unitaires (pytest)
cahier_des_charges.md           # Spécification fonctionnelle complète du projet
ROADMAP.md                      # Découpage du développement en jalons
```

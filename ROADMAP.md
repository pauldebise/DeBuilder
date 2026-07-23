# ROADMAP : DeBuilder

Developpement iteratif par jalons (milestones), chaque milestone etant elle-meme decoupee en taches atomiques testables.

---

## Milestone 1 : Fondations (Core Infrastructure)

**Objectif :** Toute la plomberie bas niveau, sans interface ni boucle agent. Chaque module est testable unitairement.

### 1.1 File Locking (`src/core/filelock.py`)
- [ ] Implem `file_lock()` : context manager avec `fcntl.flock()` exclusif
- [ ] Implem `acquire_lock()` / `release_lock()` : avec timeout configurable
- [ ] Test : lock concurrent (2 process) -> le deuxieme attend puis acquiert
- [ ] Test : timeout depasse -> leve `TimeoutError`

### 1.2 Gestion des fichiers d'etat (`src/core/state.py`)
- [ ] Implem `init_project_state()` : cree les 6 fichiers (`AGENTS.md`, `PROGRESS.md`, `BENCHMARKS.md`, `SUGGESTIONS.md`, `RESOURCES_NEEDED.md`, `DONE`) depuis les templates
- [ ] Implem `read_state()` / `write_state()` / `append_state()` avec verrouillage
- [ ] Implem `PROGRESS.md` a fenetre glissante (conserve seulement 2-3 dernieres iterations)
- [ ] Test : read/write atomique, pas de corruption en ecritures paralleles
- [ ] Test : fenetre glissante tronque correctement

### 1.3 Gestion des secrets (`src/core/secrets.py`)
- [ ] Implem `inject_secrets()` : `os.environ[key] = value`
- [ ] Implem `get_secret()` : lecture depuis `os.environ`
- [ ] Implem `sanitize_text()` : masque toutes les valeurs secretes presentes dans un texte
- [ ] Test : aucun secret n'est ecrit sur disque
- [ ] Test : sanitization remplace bien les secrets par `***`

### 1.4 Operations Git (`src/core/git.py`)
- [ ] Implem `clone_repo()` : `git clone <url> <target_dir>`
- [ ] Implem `commit_all()` : `git add -A && git commit -m <msg>`
- [ ] Implem `push()` : `git push`
- [ ] Implem `stage_and_commit_all()` : add + commit + push atomique
- [ ] Implem `rollback_last()` : `git reset --hard HEAD~1`
- [ ] Test : operations ne touchent jamais le depot DeBuilder (verification isolation)
- [ ] Test : commit + push + rollback sur un depot temporaire

### 1.5 Audit materiel (`src/utils/hw_audit.py`)
- [ ] Implem `audit_hardware()` : CPU cores (`os.cpu_count()`), RAM totale (`psutil`), GPU via `nvidia-smi` ou `torch.cuda`
- [ ] Implem `format_for_agent()` : rendu Markdown lisible
- [ ] Test : mock nvidia-smi absent -> `gpu_available=False`
- [ ] Test : mock GPU present -> detection correcte VRAM

### 1.6 Parsing Markdown (`src/utils/markdown_parser.py`)
- [ ] Implem `parse_progress()` : extraction sections (derniere iteration, precedente, prochaine tache)
- [ ] Implem `parse_benchmarks()` : extraction tableaux Markdown -> `list[dict]`
- [ ] Implem `parse_alerts()` : detection mots-cles (stagnation, bottleneck, erreur)
- [ ] Test : parsing PROGRESS.md avec 3 iterations -> bonnes sections extraites
- [ ] Test : parsing BENCHMARKS.md avec tableaux varies

---

## Milestone 2 : La Boucle Autonome de l'Agent

**Objectif :** Le script `agent_loop.sh` execute OpenCode iterativement en s'appuyant sur les fichiers d'etat, sans interface graphique. Pilote manuellement.

### 2.1 Boucle principale (`src/loop/agent_loop.sh`)
- [ ] Implem boucle `while true` avec :
  - Detection de `DONE` -> sortie propre
  - Lecture `AGENTS.md` (objectif), `PROGRESS.md` (contexte recent), `BENCHMARKS.md` (ne pas regresser)
  - Lecture `SUGGESTIONS.md` si present -> integration au prompt avec obligation de justification
  - Appel a `opencode` avec le prompt construit
  - Post-iteration : mise a jour `PROGRESS.md` (fenetre glissante), `BENCHMARKS.md` si nouvelles metriques
  - `stage_and_commit_all()` sur le depot cible
- [ ] Test : la boucle s'arrete proprement quand `DONE` est present
- [ ] Test : les commits sont faits a chaque iteration sur le BON depot

### 2.2 Integration Hardware Awareness
- [ ] Audit materiel execute une fois au premier lancement, injecte dans `AGENTS.md`
- [ ] Re-audit periodique (configurable) si ressources changeantes (cloud)
- [ ] Test : l'agent recoit bien les infos hardware dans son contexte

### 2.3 Gestion des suggestions (`SUGGESTIONS.md`)
- [ ] L'agent lit le fichier, traite la suggestion, ecrit sa justification dans `PROGRESS.md`
- [ ] Nettoyage du fichier apres traitement pour eviter re-lecture
- [ ] Test : suggestion acceptee -> justification positive dans PROGRESS
- [ ] Test : suggestion rejetee -> justification du refus dans PROGRESS
- [ ] Test : suggestion reportee -> mention explicite dans PROGRESS

### 2.4 Gestion des requetes agent (`RESOURCES_NEEDED.md`)
- [ ] L'agent peut ecrire une demande de ressource (bonus), sans jamais bloquer
- [ ] L'agent doit appliquer une solution de contournement s'il manque une ressource
- [ ] Detection reponse utilisateur -> integration a l'iteration suivante
- [ ] Test : fichier RESOURCES_NEEDED ecrit -> l'agent continue sans attendre
- [ ] Test : ressource fournie -> l'agent l'utilise a l'iteration N+1

### 2.5 Barrières Human-in-the-Loop
- [ ] L'agent detecte une barriere activee pour l'operation planifiee
- [ ] Creation d'un fichier `BARRIER_<type>` -> l'agent attend la suppression
- [ ] Test : barriere active -> l'agent met en pause l'operation concernee
- [ ] Test : barriere levee -> l'agent reprend normalement

---

## Milestone 3 : GUI - Configuration et Gestion de Session

**Objectif :** L'interface Gradio est fonctionnelle pour le demarrage d'une session. Premier pont entre l'utilisateur et l'agent.

### 3.1 Squelette Gradio (`src/app.py`)
- [ ] Implem `main()` : lancement serveur Gradio sur port 7680
- [ ] Implem `build_interface()` : structure a onglets (5 tabs)
- [ ] Integration avec `start.sh` : lancement en arriere-plan tmux
- [ ] Test : l'app demarre, le port 7680 repond

### 3.2 Onglet Configuration (`src/gui/config.py`)
- [ ] Champ `repo_url` : URL Git (optionnel, vide = nouveau projet)
- [ ] Champ `workspace_dir` : chemin absolu du repertoire cible
- [ ] Champ `instructions` : cahier des charges libre (multiline)
- [ ] Champ secrets : paires cle/valeur dynamiques (ajout/suppression)
- [ ] Bouton "Demarrer la session" :
  - Si `repo_url` fourni -> `clone_repo()`
  - Sinon -> `git init` dans workspace_dir
  - `init_project_state()` avec les templates + instructions
  - `inject_secrets()` dans l'environnement
  - Lancement `agent_loop.sh` en sous-processus
- [ ] Test : session vierge creee avec fichiers d'etat OK
- [ ] Test : session depuis clone Git -> depot cloné, fichiers inities

---

## Milestone 4 : GUI - Tableau de Bord et Logs

**Objectif :** L'utilisateur peut suivre en temps reel l'avancement de l'agent. Interface en lecture seule.

### 4.1 Onglet Tableau de Bord (`src/gui/dashboard.py`)
- [ ] Zone "Progression" : parsing `PROGRESS.md` -> affichage formate (Markdown render)
- [ ] Zone "Metriques" : parsing `BENCHMARKS.md` -> tableaux HTML
- [ ] Zone "Alertes" : mots-cles colores (stagnation, erreur, bottleneck)
- [ ] Polling automatique via `gr.Textbox` avec `every=` (timer Gradio)
- [ ] Test : mise a jour fichier PROGRESS -> l'affichage se rafraichit dans les N secondes

### 4.2 Onglet Logs Systemes (`src/gui/logs.py`)
- [ ] Zone "Logs Agent" : affichage stdout/stderr de `agent_loop.sh`
- [ ] Zone "Logs Systeme" : affichage logs propres a DeBuilder
- [ ] Polling automatique identique au dashboard
- [ ] Sanitization des logs avant affichage (`sanitize_text()`)
- [ ] Test : un secret apparait dans les logs -> il est masque dans l'interface

---

## Milestone 5 : GUI - Centre de Controle

**Objectif :** L'utilisateur peut intervenir sur le comportement de l'agent de maniere asynchrone.

### 5.1 Boite aux Lettres / Suggestions (`src/gui/control.py`)
- [ ] Champ texte multiligne pour le message utilisateur
- [ ] Bouton "Envoyer" -> ecrit dans `SUGGESTIONS.md` (via `write_state()`)
- [ ] Feedback visuel : confirmation d'envoi
- [ ] Test : message envoye -> `SUGGESTIONS.md` contient le message

### 5.2 Kill-Switch
- [ ] Bouton "Arret d'urgence" avec confirmation modale
- [ ] Cree le fichier `DONE` dans le repertoire cible
- [ ] L'agent s'arrete proprement a la fin de son iteration en cours
- [ ] Test : kill-switch active -> `DONE` cree -> boucle agent s'arrete

### 5.3 Rollback
- [ ] Bouton "Rollback (HEAD~1)" avec confirmation
- [ ] Appel `rollback_last()` sur le depot cible
- [ ] Feedback : message de succes ou d'erreur
- [ ] Test : rollback effectue -> dernier commit annule, workspace restaure

### 5.4 Barrieres (Human-in-the-Loop)
- [ ] Liste des types d'operations barrierisables (entrainement long, deploy, etc.)
- [ ] Toggle On/Off par type d'operation
- [ ] Etat visuel clair (barriere active / inactive)
- [ ] Lorsqu'une barriere est declenchee : notification dans le dashboard
- [ ] Bouton "Valider" pour lever la barriere (supprime le fichier `BARRIER_*`)
- [ ] Test : barriere activee -> agent bloque sur l'operation -> validation -> agent reprend

---

## Milestone 6 : Requetes Agent et Polish Final

**Objectif :** Boucler les dernieres fonctionnalites et solidifier l'ensemble.

### 6.1 Onglet Requetes Agent (`src/gui/agents.py`)
- [ ] Affichage des demandes depuis `RESOURCES_NEEDED.md`
- [ ] Notification visuelle (badge / couleur) si une requete non traitee est en attente
- [ ] Champ reponse utilisateur -> notification dans `SUGGESTIONS.md`
- [ ] Test : agent ecrit une demande -> badge apparait -> reponse -> badge disparait

### 6.2 Securite et Robustesse
- [ ] `.gitignore` : exclut fichiers sensibles, venv, __pycache__, etc.
- [ ] Sanitization globale : toutes les sorties GUI passent par `sanitize_text()`
- [ ] Validation formulaire : chemins valides, depot Git accessible
- [ ] Gestion erreurs : l'echec d'une iteration ne casse pas la boucle
- [ ] Logs DeBuilder persistants avec rotation
- [ ] Test : injection de secrets dans les logs -> sanitization verifiee

### 6.3 Tests d'integration bout-en-bout
- [ ] Scenario complet : session vierge -> instructions -> 3 iterations agent -> suggestions -> kill-switch
- [ ] Scenario Git : clone -> modifications agent -> commit/push automatique -> rollback
- [ ] Scenario secrets : secret injecte -> jamais present dans les logs GUI
- [ ] Scenario barrieres : barriere activee -> agent bloque -> validation -> agent reprend

### 6.4 Packaging et Documentation
- [ ] `requirements.txt` complet et versionne
- [ ] `start.sh` fonctionnel sur machine vierge (verification dependances)
- [ ] Fichier d'installation rapide (setup one-liner)
- [ ] Verification compatibilite RunPod

---

## Recapitulatif des Priorites

| Milestone | Contenu                                          | Priorite |
|-----------|--------------------------------------------------|----------|
| M1        | Fondations (lock, state, secrets, git, hw, md)  | Haute    |
| M2        | Boucle agent autonome                            | Haute    |
| M3        | GUI : Configuration & Session                    | Haute    |
| M4        | GUI : Dashboard & Logs                           | Haute    |
| M5        | GUI : Centre de Controle                         | Moyenne  |
| M6        | Requetes Agent & Polish                          | Moyenne  |

L'ordre est sequentiel : chaque milestone depend de la precedente. Les tests unitaires sont executes a chaque fin de tache ; le milestone est marque comme termine uniquement lorsque tous ses tests passent.

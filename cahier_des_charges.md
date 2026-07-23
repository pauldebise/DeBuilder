# Cahier des Charges : DeBuilder

**Description du projet :** DeBuilder est un wrapper local pour OpenCode, conçu spécifiquement pour des travaux de développement et de machine learning. Il orchestre un agent d'IA autonome travaillant en arrière-plan tout en exposant une interface web de contrôle et de supervision asynchrone pour l'utilisateur.

## 1. Architecture Globale
- **Déploiement :** Le système doit être clonable via Git et exécutable sur une machine personnelle ou un pod distant (ex: RunPod).
- **Isolation des environnements (Sandboxing) :** Le code de DeBuilder (l'interface Gradio et les scripts d'orchestration) doit résider dans un répertoire et un dépôt Git strictement séparés de ceux du projet sur lequel l'agent travaille. L'agent s'exécute exclusivement dans le répertoire du projet cible. Il lui est formellement interdit de lire ou de modifier le code source de DeBuilder.
- **Point d'entrée :** Un script shell (ex: `start.sh`) lance l'application dans un environnement isolé (tmux ou screen) pour assurer la persistance de la session en cas de déconnexion.
- **Composants principaux :**
  - **La boucle de l'agent :** Un script autonome (ex: `agent_loop.sh`) qui exécute OpenCode de manière itérative dans le dossier du projet cible.
  - **L'Interface Web (GUI) :** Une application web (réalisée avec Gradio en Python) exposée sur un port défini (ex: 7680) pour le monitoring et la configuration.
- **Communication asynchrone et sécurité d'accès :** La synchronisation entre la boucle de l'agent et la GUI se fait exclusivement par le système de fichiers (lecture/écriture de fichiers Markdown stockés dans le répertoire du projet cible). Afin d'empêcher toute corruption de données (race conditions) entre les écritures de l'agent et le rafraîchissement continu de l'interface (polling), un mécanisme strict de verrouillage de fichiers (File Locking) doit être appliqué sur toutes les opérations d'entrée/sortie.

## 2. La Boucle Autonome de l'Agent
- **Autonomie complète :** L'agent s'exécute en boucle. Il n'a aucune mémoire de contexte interne entre deux itérations et s'appuie entièrement sur des fichiers d'état pour la continuité de l'information et empêcher la régression.
- **Fichiers d'état principaux :**
  - `AGENTS.md` : Définit l'objectif global, le rôle de l'agent et les contraintes du projet.
  - `PROGRESS.md` : Le journal de bord de l'agent. Pour éviter la saturation de la fenêtre de contexte de l'IA, ce fichier fonctionne avec une fenêtre glissante : il ne conserve systématiquement que les détails des 2 à 3 dernières itérations. Il documente l'état d'avancement immédiat, les problèmes récents, les solutions envisagées et la prochaine sous-tâche prévue.
  - `BENCHMARKS.md` : **Fichier avant tout destiné à l'agent pour lui permettre de ne pas régresser.** Il contient les résultats de runs et les données de performances factuelles (utilisation hardware, temps d'entraînement, scores, etc.) formatées sous forme de tableaux Markdown. **Il ne doit pas contenir de suivi de courbes d'apprentissage (comme la loss), l'agent devant ouvrir de manière autonome un outil dédié tel que TensorBoard pour cela.** L'interface permet à l'utilisateur d'avoir un regard sur ces benchmarks, mais tout comme `PROGRESS.md` et `AGENTS.md`, sa finalité première est de servir l'agent.
- **Conscience de l'environnement matériel (Hardware Awareness) :** L'agent doit être capable d'auditer dynamiquement les ressources de la machine hôte (quantité de RAM, présence et capacité d'un GPU, processeur). Cette conscience de son environnement lui permet de prendre des décisions d'implémentation autonomes et réalistes (par exemple : décider d'entraîner un modèle de machine learning s'il constate qu'il dispose d'un GPU, ou concevoir des algorithmes peu gourmands en mémoire s'il détecte une RAM restreinte).
- **Sécurité et persistance :** À chaque itération, l'agent doit commit et push son travail **uniquement sur le dépôt Git du projet cible**. Le script de boucle doit s'assurer que tout changement dans l'espace de travail du projet est commité même si l'agent échoue à le faire, sans jamais interférer avec le dépôt Git de DeBuilder.

## 3. L'Interface Web (GUI)
Construite avec Gradio, l'interface doit être claire, organisée par onglets et se rafraîchir automatiquement par polling des fichiers locaux du projet cible.

### 3.1 Configuration Initiale et Gestion de Session
- **Démarrage :** Permet de lancer une nouvelle session en préparant un environnement vierge ou en clonant un dépôt Git spécifié par l'utilisateur (le dépôt du projet de travail, séparé de DeBuilder).
- **Gestion des secrets :** Interface pour renseigner de manière sécurisée les clés API (modèle IA, services externes). Ces secrets sont injectés comme variables d'environnement éphémères dans la session de l'agent et ne doivent jamais être inscrits sur le disque.
- **Cahier des charges :** Champ permettant de définir l'objectif initial, qui sera transcrit dans le fichier `AGENTS.md` du projet cible.

### 3.2 Tableau de Bord (Lecture seule)
- **État actuel :** Affichage de l'avancement basé sur le parsing de `PROGRESS.md`.
- **Métriques :** Extraction et présentation lisible des données contenues dans `BENCHMARKS.md`, **offrant un regard à l'utilisateur sur ces données prioritairement exploitées par l'agent**.
- **Alertes :** Mise en évidence des alertes "watchdog" déclenchées par l'agent (ex: stagnation d'apprentissage, goulot d'étranglement).

### 3.3 Centre de Contrôle (Intervention Asynchrone)
- **Boîte aux Lettres (Guidage Humain) :**
  - Permet à l'utilisateur d'envoyer des directives, suggestions ou observations basées sur les logs.
  - Les messages sont formatés et écrits dans un fichier (ex: `SUGGESTIONS.md`).
  - L'agent lit ce fichier en début d'itération. Il reste libre d'accepter, reporter ou rejeter la suggestion, mais doit obligatoirement justifier sa décision dans le fichier `PROGRESS.md`.
- **Arrêt d'Urgence (Kill-Switch) :** Un bouton qui crée un fichier témoin (ex: `DONE`) pour stopper proprement la boucle de l'agent à la fin de son itération en cours.
- **Rollback (Machine à remonter le temps) :** Permet d'annuler les actions de la dernière itération via une commande `git reset --hard HEAD~1` exécutée sur le dépôt cible, tout en préservant la stabilité de la session et de DeBuilder.
- **Barrières (Human-in-the-Loop) :** Un système permettant de configurer des points d'arrêt de sécurité pour les opérations longues ou coûteuses. L'agent se met en pause et requiert une validation humaine explicite depuis l'interface avant de poursuivre.

### 3.4 Requêtes de l'Agent (Escalade Non-Bloquante / "Bonus")
- **Autonomie prioritaire :** L'agent est conçu pour être radicalement autonome et ne doit pas abuser des demandes d'assistance. Toute requête émise vers l'utilisateur doit être considérée comme un "bonus" (ex: demande d'accès à un outil spécifique ou à des ressources matérielles supplémentaires pour optimiser une tâche).
- **Interdiction de blocage :** L'agent ne doit **jamais** mettre sa boucle en pause dans l'attente d'une réponse humaine. S'il lui manque une ressource, il doit obligatoirement trouver et appliquer une solution de contournement par lui-même (mode dégradé, méthode alternative, passage à une autre sous-tâche).
- **Format :** L'agent écrit sa demande dans un fichier (ex: `RESOURCES_NEEDED.md`), en justifiant impérativement pourquoi cette ressource serait un plus et quelles solutions palliatives il a déjà mises en place. Cela déclenche une notification visuelle dans l'interface.
- **Résolution :** Si l'utilisateur ignore la demande, l'agent continue sans perturbation. Si la ressource est fournie via la GUI, le système ajoute une notification système dans la boîte aux lettres pour informer l'agent que le bonus est disponible à l'itération suivante.

### 3.5 Logs Systèmes
- Affichage direct des fichiers de logs bruts générés par l'agent et le script de boucle, permettant un débogage approfondi par l'utilisateur.

## 4. Contraintes de Sécurité Supplémentaires
- **Sanitization :** L'agent doit être instruit pour ne jamais inclure de clés API ou de tokens sensibles dans les logs ou les commits (même si le système de variables d'environnement limite ce risque).

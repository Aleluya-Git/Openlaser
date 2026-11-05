# OpenLaser Studio

[English](README.md) · [Español](README.es.md) · **Français** · [Deutsch](README.de.md)

Application de bureau (Windows, Linux, macOS) pour concevoir, préparer, simuler et exécuter des travaux
de gravure et de découpe laser avec des contrôleurs **GRBL 1.1+, grblHAL et FluidNC**, et de façon
expérimentale **Marlin** et **Smoothieware**. Écrite en Python 3.12 + PySide6. Code, icônes et textes
originaux — licence MIT.

> ⚠️ **La sécurité d'abord.** OpenLaser Studio ne remplace pas les verrouillages physiques, l'arrêt
> d'urgence câblé, la surveillance permanente, l'extraction des fumées ni une protection oculaire
> certifiée pour la longueur d'onde de votre laser. Les valeurs des matériaux de démonstration sont
> fictives : validez chaque combinaison sur votre machine et votre matériau.

## Téléchargement

Téléchargez la dernière version dans [**Releases**](https://github.com/Aleluya-Git/Openlaser/releases/latest) :

* `OpenLaserStudio-x.y.z-setup.exe` — installateur Windows (Python non requis).
* `OpenLaserStudio-x.y.z-portable-win64.zip` — version portable : décompressez et lancez `OpenLaserStudio.exe`.

Sous Linux/macOS, lancez-le depuis le code source (voir ci-dessous).

## Fonctions

* **Pilotage de la machine** — USB/série, TCP/Telnet et WebSocket ; déplacement manuel, homing, cadrage,
  aller à, origines, corrections en temps réel, console avec historique, macros et éditeur des
  paramètres `$$` de GRBL avec sauvegarde et restauration.
* **Contrôleurs** — GRBL 1.1+ (et les rapports de GRBL 0.9), grblHAL et FluidNC avec détection
  automatique du firmware ; Marlin et Smoothieware (expérimental) ; envoi des travaux sur la carte SD de
  FluidNC/ESP3D. Simulateur de chaque firmware pour tout essayer sans machine.
* **Dessin** — document vectoriel avec calques (Ligne, Remplissage, Remplissage + Ligne, Image), outils
  de dessin, édition de nœuds, opérations booléennes, décalages, réseaux, texte et texte sur chemin,
  bibliothèque de formes ; import SVG, DXF, PDF/AI et images.
* **Images** — luminosité/contraste/gamma, tramages (Floyd–Steinberg, Atkinson, Jarvis, Stucki, Bayer,
  demi-teinte…), niveaux de gris, angle de balayage et vectorisation. Le traitement est mis en cache
  pour que le déplacement des images reste fluide.
* **G-code et simulation** — génération ligne, remplissage et image, optimisation des déplacements,
  attaches, entrées/sorties, compensation de saignée, estimation du temps et simulation pas à pas avec
  aperçu de la gravure.
* **Caméra** — calibrage de l'objectif et de la zone de travail, superposition sur le canevas, Print and
  Cut et Center Finder.
* **Rotatif** et correction de cylindre, bibliothèque de matériaux et 13 assistants de test (mise au
  point, puissance, intervalle, saignée…).
* **Sécurité dès la conception** — liste de contrôle et vérifications avant le démarrage, laser
  toujours éteint pendant les déplacements et cadrages, arrêt logiciel, chien de garde du tir de mise au
  point, aucun déverrouillage ni reprise automatique après une alarme et journal des événements de sécurité.
* **Langues** — français, anglais, espagnol et allemand. Chacun peut ajouter une langue en traduisant un
  seul fichier JSON : voir [docs/IDIOMAS.md](docs/IDIOMAS.md) (Préférences → *Créer une nouvelle langue…*).
* **Plugins** — extensions Python pour d'autres contrôleurs et outils.

## Lancer depuis le code source

Python 3.12 ou plus récent requis.

```bash
# Windows
install.bat        # crée .venv et installe les dépendances
run.bat            # lance l'application avec le simulateur GRBL de démonstration
autoprueba.bat     # exécute les tests et fait une capture

# Linux / macOS
./install.sh && ./run.sh
```

Ou manuellement : `python -m venv .venv`, `pip install -e .`, `python -m openlaser`.

Pour générer vous-même l'installateur Windows, lancez `build_installer.bat` (PyInstaller + Inno Setup 6),
voir [docs/INSTALADOR.md](docs/INSTALADOR.md). L'envoi d'une étiquette `vX.Y.Z` génère et publie
automatiquement une release avec GitHub Actions.

## Conseils de connexion

* Fermez tout autre programme qui utilise le port série (LaserGRBL, LightBurn…) avant de vous connecter.
* Le débit par défaut est de 115200 bauds. DTR/RTS sont inactifs par défaut ; si la carte ne répond pas,
  OpenLaser essaie les autres combinaisons et vous indique celle qui a fonctionné pour l'enregistrer
  dans le profil de l'appareil.

## Documentation

La documentation technique, en espagnol, se trouve dans [`docs/`](docs) : architecture, chaque phase du
développement, installateur, langues et licences des tiers ([docs/LICENCIAS.md](docs/LICENCIAS.md)).

## Licence

MIT — voir [LICENSE](LICENSE). Utilise PySide6 (LGPL-3.0, liaison dynamique), pyserial, websockets et
numpy. Sans lien avec LaserGRBL ni LightBurn et ne contient aucun code ni ressource de leur part.

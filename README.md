# Parking Intelligent

Application Python de gestion de parking : détection de voiture, lecture de plaque tunisienne (OCR) et pointage automatique entrée/sortie avec calcul de la durée de stationnement.

## Installation

Python 3.9 à 3.12 recommandé.

```bash
cd parking_intelligent
python -m venv venv
venv\Scripts\activate          # Windows   (Linux/Mac : source venv/bin/activate)
pip install -r requirements.txt
python main.py
```

Au premier lancement, une connexion internet est nécessaire : YOLOv8 (`yolov8n.pt`) et les modèles EasyOCR (arabe + anglais) sont téléchargés automatiquement (~100 Mo). Le chargement prend ensuite quelques secondes (indicateur en bas du menu).

## Structure

| Fichier | Rôle |
|---|---|
| `main.py` | Interface Tkinter (caméras, résumé, historique) |
| `detection.py` | **Fonctionnalité 1** — détection de la voiture (YOLOv8) + rectangle |
| `plate_reader.py` | **Fonctionnalité 2** — localisation, extraction, amélioration, OCR de la plaque |
| `database.py` | **Fonctionnalité 3** — pointage entrée/sortie, durée (SQLite) |
| `engine.py` | Enchaîne détection → lecture de plaque |
| `config.py` | Caméras, capacité, modèles, langues OCR |
| `utils.py` | Formatage de durée, lecture/écriture d'images |
| `test_image.py` | Test en ligne de commande sur une image |

Dossiers créés automatiquement : `data/` (base `parking.db`), `captures/` (photos des pointages).

## Utilisation

1. **Démarrer caméra** (ou **Charger image** pour tester avec une photo).
2. **Capturer & lire** : la voiture est encadrée en vert, la plaque en jaune, le numéro s'affiche.
3. Corriger la plaque si besoin (le champ est modifiable), puis **Valider entrée** / **Valider sortie**.
   - Entrée : refusée si le véhicule est déjà dans le parking ou si le parking est complet.
   - Sortie : retrouve la dernière entrée, enregistre l'heure de sortie et affiche la durée.
4. Historique : recherche par plaque, filtre par statut, double-clic pour voir les photos, export CSV.

Format des plaques : `123 TU 4567` (série + « تونس » + numéro). La saisie manuelle accepte aussi `123 تونس 4567` ou `123TN4567`.

## Configuration (`config.py`)

- `ENTRY_CAMERA = 0`, `EXIT_CAMERA = 1` : index des caméras. Avec une seule webcam, mettre `0` aux deux, ou utiliser une caméra de téléphone via une URL (ex. application IP Webcam : `"http://192.168.1.10:8080/video"`).
- `DEFAULT_CAPACITY` : nombre de places (modifiable aussi dans **Paramètres**).
- `USE_GPU = True` si une carte NVIDIA + CUDA est disponible.

## Améliorer la précision (optionnel)

La plaque est localisée par une méthode OpenCV classique (contours + fond sombre des plaques tunisiennes). Pour de meilleurs résultats, placer un modèle YOLO entraîné sur les plaques dans `models/plate.pt` : il sera utilisé automatiquement.

## Tester sans interface

```bash
python test_image.py photos/voiture.jpg
```

## Mode automatique (sans clic)

Bouton **AUTO** en haut : pendant la caméra ou une **VIDÉO**, chaque véhicule est suivi (#1, #2…),
sa plaque est lue plusieurs fois et les lectures votent. Dès qu'une plaque est confirmée
(2 lectures identiques, ou 1 lecture très sûre), l'entrée ou la sortie est **enregistrée toute seule** :
la barrière se lève, le zoom de la plaque s'affiche, le journal se met à jour. La vidéo ne s'arrête jamais.

Couleurs des cadres : gris = vérification / à l'arrêt / trop loin · jaune = lecture · vert = plaque validée · rouge = illisible.

Bouton **SENS** :
- `PORTIQUES` : caméra de gauche = entrée, caméra de droite = sortie ;
- `AUTO` : une seule caméra suffit — plaque déjà dans le parking = sortie, sinon entrée.

Réglages dans `config.py` : `AUTO_MIN_CAR_AREA`, `AUTO_MAX_ATTEMPTS`, `AUTO_VOTES_NEEDED`,
`AUTO_STRONG_CONF`, `AUTO_COOLDOWN`.

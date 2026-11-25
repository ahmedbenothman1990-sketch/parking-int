"""Configuration globale de l'application Parking Intelligent."""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# --- Chemins ---------------------------------------------------------------
DB_PATH = os.path.join(BASE_DIR, "data", "parking.db")
CAPTURES_DIR = os.path.join(BASE_DIR, "captures")
MODELS_DIR = os.path.join(BASE_DIR, "models")

# --- Parking ---------------------------------------------------------------
DEFAULT_CAPACITY = 30          # nombre de places (modifiable dans Paramètres)

# --- Caméras ---------------------------------------------------------------
# 0 = webcam intégrée, 1 = deuxième caméra USB, ou une URL (ex: caméra IP / téléphone)
ENTRY_CAMERA = 0
EXIT_CAMERA = 1

# --- Détection de véhicules (YOLOv8) ----------------------------------------
YOLO_MODEL = "yolov8n.pt"      # téléchargé automatiquement au premier lancement
DETECTION_CONFIDENCE = 0.40
# Classes COCO considérées comme véhicules
VEHICLE_CLASSES = {2: "Voiture", 3: "Moto", 5: "Bus", 7: "Camion"}

# --- Lecture de plaque -------------------------------------------------------
# Modèle YOLO optionnel spécialisé plaques (ex: models/plate.pt).
# S'il n'existe pas, on utilise la localisation classique OpenCV (contours).
PLATE_MODEL = os.path.join(MODELS_DIR, "plate.pt")
OCR_LANGUAGES = ["ar", "en"]   # arabe (تونس) + chiffres
USE_GPU = False
PLATE_SEPARATOR = "TU"         # format affiché : 123 TU 4567

# --- Interface ---------------------------------------------------------------
VIDEO_WIDTH = 480
VIDEO_HEIGHT = 300
LIVE_DETECTION_EVERY_N_FRAMES = 5

# --- Mode automatique (lecture + enregistrement sans clic) --------------------
AUTO_MIN_CAR_AREA = 0.03       # ignore les véhicules plus petits que 3 % de l'image (trop loin)
AUTO_MAX_ATTEMPTS = 8          # lectures max par véhicule
AUTO_VOTES_NEEDED = 2          # nb de lectures identiques pour valider
AUTO_STRONG_CONF = 0.60        # ... ou 1 seule lecture si la confiance dépasse ce seuil
AUTO_COOLDOWN = 30             # secondes avant de pouvoir re-pointer la même plaque

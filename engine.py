"""Moteur de traitement : combine la détection de voiture et la lecture de plaque."""
import threading

import cv2

from detection import CarDetector
from plate_reader import PlateReader


class ParkingEngine:
    def __init__(self):
        self.detector = None
        self.reader = None
        self.ready = False
        self.error = None
        self.lock = threading.Lock()

    def load(self):
        """Chargement des modèles (long au premier lancement : téléchargements)."""
        try:
            self.detector = CarDetector()
            self.reader = PlateReader()
            self.ready = True
        except Exception as e:  # dépendance manquante, pas d'internet, etc.
            self.error = str(e)

    def detect_only(self, frame):
        """Détection rapide pour l'aperçu en direct (ignorée si une analyse est en cours)."""
        if not self.ready or not self.lock.acquire(blocking=False):
            return None
        try:
            return self.detector.detect(frame)
        finally:
            self.lock.release()

    def detect_all(self, frame):
        """Détection bloquante (utilisée par le mode automatique, dans un thread)."""
        with self.lock:
            return self.detector.detect(frame)

    def read_box(self, frame, box):
        """Lit la plaque d'un véhicule déjà détecté. Coordonnées de plaque renvoyées dans l'image entière."""
        x1, y1, x2, y2 = box
        h, w = frame.shape[:2]
        px, py = int((x2 - x1) * 0.08), int((y2 - y1) * 0.08)
        x1, y1 = max(0, x1 - px), max(0, y1 - py)
        x2, y2 = min(w, x2 + px), min(h, y2 + py)
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return {"plate": "", "confidence": 0.0, "plate_box": None, "plate_img": None}
        with self.lock:
            r = self.reader.read(crop)
        plate_box = None
        if r["box"]:
            bx, by, bw, bh = r["box"]
            plate_box = (x1 + bx, y1 + by, x1 + bx + bw, y1 + by + bh)
        plate_img = None
        if r["plate_img"] is not None and r["plate_img"].size:
            plate_img = self.reader.enhance(r["plate_img"])[0]       # zoom net et contrasté
        return {"plate": r["plate"], "confidence": r["confidence"], "plate_box": plate_box, "plate_img": plate_img}

    def process(self, frame):
        """Détecte la voiture, lit la plaque, renvoie l'image annotée et le résultat."""
        with self.lock:
            annotated = frame.copy()
            detections = self.detector.detect(frame)
            if detections:
                x1, y1, x2, y2, *_ = detections[0]
                car_img = frame[y1:y2, x1:x2]
            else:
                x1 = y1 = 0
                car_img = frame               # pas de voiture détectée : on tente quand même
            result = self.reader.read(car_img)

        CarDetector.draw(annotated, detections[:1])
        if result["box"]:
            px, py, pw, ph = result["box"]
            cv2.rectangle(annotated, (x1 + px, y1 + py), (x1 + px + pw, y1 + py + ph), (0, 220, 255), 3)
        if result["plate"]:
            text = f"Plaque : {result['plate']}"
            h = annotated.shape[0]
            cv2.rectangle(annotated, (10, h - 50), (20 + 18 * len(text), h - 10), (0, 0, 0), -1)
            cv2.putText(annotated, text, (18, h - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 2)

        result["vehicle_found"] = bool(detections)
        result["annotated"] = annotated
        return result

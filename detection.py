"""Fonctionnalité 1 — Détection de la voiture (YOLOv8 pré-entraîné sur COCO)."""
import cv2

from config import YOLO_MODEL, DETECTION_CONFIDENCE, VEHICLE_CLASSES


class CarDetector:
    def __init__(self):
        from ultralytics import YOLO  # import ici pour un démarrage plus rapide de l'interface
        self.model = YOLO(YOLO_MODEL)

    def detect(self, frame):
        """Retourne la liste des véhicules détectés : (x1, y1, x2, y2, label, confiance),
        triée du plus grand au plus petit (le plus grand = la voiture devant la caméra)."""
        result = self.model(frame, verbose=False, conf=DETECTION_CONFIDENCE)[0]
        detections = []
        for box in result.boxes:
            cls = int(box.cls[0])
            if cls in VEHICLE_CLASSES:
                x1, y1, x2, y2 = map(int, box.xyxy[0])
                detections.append((x1, y1, x2, y2, VEHICLE_CLASSES[cls], float(box.conf[0])))
        detections.sort(key=lambda d: (d[2] - d[0]) * (d[3] - d[1]), reverse=True)
        return detections

    @staticmethod
    def draw(frame, detections, color=(0, 200, 0)):
        """Dessine un rectangle autour de chaque véhicule détecté."""
        for x1, y1, x2, y2, label, conf in detections:
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 3)
            text = f"{label} {conf:.0%}"
            (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)
            cv2.rectangle(frame, (x1, max(y1 - th - 10, 0)), (x1 + tw + 8, y1), color, -1)
            cv2.putText(frame, text, (x1 + 4, max(y1 - 6, th)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        return frame

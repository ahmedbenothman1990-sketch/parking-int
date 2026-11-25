"""Fonctionnalité 2 — Lecture de la plaque d'immatriculation tunisienne.

Étapes : localiser la plaque -> extraire la zone -> améliorer l'image -> OCR -> formater.
Plaque tunisienne : fond noir, caractères blancs, « تونس » au centre entre deux nombres
(série sur 3 chiffres max, numéro sur 4 chiffres max). Format affiché : « 123 TU 4567 ».
"""
import os
import re

import cv2

from config import OCR_LANGUAGES, USE_GPU, PLATE_MODEL, PLATE_SEPARATOR

ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def normalize_plate(text):
    """Normalise une plaque saisie à la main ou lue par OCR : '123tunis 4567' -> '123 TU 4567'."""
    if not text:
        return ""
    t = text.translate(ARABIC_DIGITS).upper().strip()
    t = re.sub(r"تونس|TUNIS|TUN|TN", f" {PLATE_SEPARATOR} ", t)
    groups = re.findall(r"\d+", t)
    if len(groups) >= 2 and PLATE_SEPARATOR in t:
        return f"{groups[0]} {PLATE_SEPARATOR} {groups[-1]}"
    return re.sub(r"\s+", " ", t).strip()


class PlateReader:
    def __init__(self):
        import easyocr
        self.reader = easyocr.Reader(OCR_LANGUAGES, gpu=USE_GPU)
        self.plate_model = None
        if PLATE_MODEL and os.path.exists(PLATE_MODEL):
            from ultralytics import YOLO
            self.plate_model = YOLO(PLATE_MODEL)

    # ------------------------------------------------------- 1. localisation
    def locate_plate(self, car_img):
        """Retourne (x, y, w, h) de la plaque dans l'image de la voiture, ou None."""
        if self.plate_model is not None:
            res = self.plate_model(car_img, verbose=False, conf=0.3)[0]
            if len(res.boxes):
                b = max(res.boxes, key=lambda b: float(b.conf[0]))
                x1, y1, x2, y2 = map(int, b.xyxy[0])
                return x1, y1, x2 - x1, y2 - y1
        return self._locate_by_contours(car_img)

    @staticmethod
    def _locate_by_contours(img):
        """Méthode classique : contours rectangulaires avec un rapport largeur/hauteur de plaque."""
        h_img, w_img = img.shape[:2]
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        gray = cv2.bilateralFilter(gray, 11, 17, 17)
        edges = cv2.Canny(gray, 30, 200)
        edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (5, 3)))
        contours, _ = cv2.findContours(edges, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)

        best, best_score = None, 0
        for c in contours:
            x, y, w, h = cv2.boundingRect(c)
            if h == 0:
                continue
            ratio, area = w / h, w * h
            if not 2.0 <= ratio <= 6.5:
                continue
            if not 0.003 * w_img * h_img < area < 0.25 * w_img * h_img:
                continue
            score = area
            approx = cv2.approxPolyDP(c, 0.02 * cv2.arcLength(c, True), True)
            if len(approx) == 4:
                score *= 2                      # forme rectangulaire
            if y > h_img * 0.4:
                score *= 1.5                    # la plaque est dans la partie basse
            roi = gray[y:y + h, x:x + w]
            if roi.size and roi.mean() < 110:
                score *= 1.5                    # plaque tunisienne = fond sombre
            if score > best_score:
                best, best_score = (x, y, w, h), score
        return best

    # ------------------------------------------------- 2. amélioration image
    @staticmethod
    def enhance(img):
        """Agrandit, améliore le contraste et binarise. Retourne (gris, binaire)."""
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
        h, w = gray.shape
        new_w = max(400, w)
        gray = cv2.resize(gray, (new_w, max(1, int(h * new_w / w))), interpolation=cv2.INTER_CUBIC)
        gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
        gray = cv2.bilateralFilter(gray, 9, 75, 75)
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        if binary.mean() < 127:                 # texte blanc sur fond noir -> on inverse
            binary = cv2.bitwise_not(binary)
        return gray, binary

    # ------------------------------------------------------------ 3. OCR
    def ocr(self, images):
        best_text, best_conf = "", 0.0
        for img in images:
            results = self.reader.readtext(img, detail=1, paragraph=False)
            text, conf = self._parse(results)
            if text and conf > best_conf:
                best_text, best_conf = text, conf
        return best_text, best_conf

    def _parse(self, results):
        groups = []
        for bbox, txt, conf in results:
            txt = txt.translate(ARABIC_DIGITS)
            x = min(p[0] for p in bbox)
            for m in re.finditer(r"\d+", txt):
                groups.append((x, m.group(), conf))
        if not groups:
            return "", 0.0
        groups.sort(key=lambda g: g[0])
        conf = sum(g[2] for g in groups) / len(groups)
        return self.format_plate([g[1] for g in groups]), conf

    @staticmethod
    def format_plate(digit_groups):
        """Reconstruit « série TU numéro » à partir des groupes de chiffres lus."""
        groups = [g for g in digit_groups if g]
        if len(groups) >= 2:
            a, b = groups[0], groups[-1]
            # La série a au plus 3 chiffres, le numéro jusqu'à 4
            if len(a) <= 3 and len(b) >= 4:
                serie, numero = a, b
            elif len(b) <= 3 and len(a) >= 4:
                serie, numero = b, a
            else:
                serie, numero = a, b
            return f"{serie[:3]} {PLATE_SEPARATOR} {numero[:4]}"
        return groups[0] if groups else ""

    # ---------------------------------------------------------- pipeline
    def read(self, car_img):
        """Pipeline complet sur l'image d'une voiture."""
        box = self.locate_plate(car_img)
        plate_img, text, conf = None, "", 0.0
        if box:
            x, y, w, h = box
            pad = int(0.05 * w)
            plate_img = car_img[max(0, y - pad):y + h + pad, max(0, x - pad):x + w + pad]
            text, conf = self.ocr(self.enhance(plate_img))
        if not text:
            # Repli : OCR sur la moitié basse du véhicule
            lower = car_img[car_img.shape[0] // 2:, :]
            text, conf = self.ocr([self.enhance(lower)[0]])
            box = None if not text else box
        return {"plate": text, "confidence": conf, "box": box, "plate_img": plate_img}

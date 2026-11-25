"""Tester la détection + lecture de plaque sur une image, sans interface.

Usage :  python test_image.py chemin/vers/voiture.jpg
Le résultat annoté est enregistré dans captures/test_resultat.jpg
"""
import os
import sys

from config import CAPTURES_DIR
from engine import ParkingEngine
from utils import imread_unicode, imwrite_unicode

if len(sys.argv) < 2:
    sys.exit("Usage : python test_image.py image.jpg")

img = imread_unicode(sys.argv[1])
if img is None:
    sys.exit("Image introuvable ou illisible.")

print("Chargement des modèles...")
engine = ParkingEngine()
engine.load()
if not engine.ready:
    sys.exit(f"Erreur : {engine.error}")

r = engine.process(img)
print(f"Véhicule détecté : {'oui' if r['vehicle_found'] else 'non'}")
print(f"Plaque lue       : {r['plate'] or '(aucune)'}")
print(f"Confiance OCR    : {r['confidence']:.0%}")
out = os.path.join(CAPTURES_DIR, "test_resultat.jpg")
imwrite_unicode(out, r["annotated"])
print(f"Image annotée    : {out}")

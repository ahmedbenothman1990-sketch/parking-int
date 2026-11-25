"""Fonctions utilitaires."""
import os
import cv2
import numpy as np


def format_duration(minutes):
    """Convertit une durée en minutes en texte lisible : 2h 05min, 1j 3h 00min..."""
    if minutes is None:
        return "—"
    hours, mins = divmod(int(minutes), 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days}j {hours}h {mins:02d}min"
    if hours:
        return f"{hours}h {mins:02d}min"
    return f"{mins} min"


def imread_unicode(path):
    """cv2.imread ne gère pas les chemins avec accents sous Windows."""
    data = np.fromfile(path, dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def imwrite_unicode(path, image):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    ok, buf = cv2.imencode(os.path.splitext(path)[1] or ".jpg", image)
    if ok:
        buf.tofile(path)
    return ok

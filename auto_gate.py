"""Mode automatique : suivi des véhicules + lecture répétée + vote.

Principe (comme un vrai système LPR) :
  1. chaque véhicule détecté devient une « piste » suivie d'image en image (IoU) ;
  2. une piste confirmée (vue plusieurs fois) et qui a bougé est lue plusieurs fois ;
  3. les lectures votent : la plaque est validée quand elle est lue 2 fois
     (ou 1 fois avec une très bonne confiance) ;
  4. si le véhicule quitte l'image avant, on garde la meilleure lecture.
Aucun clic : la décision est renvoyée à l'interface qui enregistre entrée ou sortie.
"""
import re
from collections import Counter

PLATE_RE = re.compile(r"^\d{1,3} TU \d{1,4}$")


def lev(a, b):
    """Distance de Levenshtein (nombre de caractères différents)."""
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def match_known(plate, known):
    """Retrouve une plaque connue à 1 caractère près (corrige une petite erreur d'OCR)."""
    if plate in known:
        return plate
    p = plate.replace(" ", "")
    for k in known:
        kk = k.replace(" ", "")
        if len(kk) == len(p) and lev(kk, p) <= 1:
            return k
    return None


def iou(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    area = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / area if area > 0 else 0.0


def center(b):
    return (b[0] + b[2]) / 2, (b[1] + b[3]) / 2


class Track:
    def __init__(self, tid, box, now):
        self.id, self.box, self.origin = tid, box, center(box)
        self.hits, self.last_seen, self.born = 1, now, now
        self.moved = False
        self.votes, self.best = Counter(), {}      # plaque -> nb de lectures / meilleure confiance
        self.attempts, self.last_read = 0, 0.0
        self.plate, self.failed = None, False
        self.plate_box, self.plate_img = None, None


class AutoTracker:
    def __init__(self, confirm=2, max_attempts=8, lost_after=2.5, min_area=0.03,
                 votes_needed=2, strong_conf=0.6, min_conf=0.25, read_gap=0.3, move_frac=0.03):
        self.confirm, self.max_attempts, self.lost_after = confirm, max_attempts, lost_after
        self.min_area, self.votes_needed, self.strong_conf = min_area, votes_needed, strong_conf
        self.min_conf, self.read_gap, self.move_frac = min_conf, read_gap, move_frac
        self.reset()

    def reset(self):
        self.tracks, self.next_id = [], 1
        self.W, self.H = 1, 1

    # -------------------------------------------------------------- suivi
    def update(self, boxes, W, H, now):
        """boxes : liste de (x1, y1, x2, y2). Renvoie les décisions des pistes perdues."""
        self.W, self.H = W, H
        pairs = []
        for ti, t in enumerate(self.tracks):
            for di, d in enumerate(boxes):
                s = iou(t.box, d)
                if s <= 0.2:
                    (tx, ty), (dx, dy) = center(t.box), center(d)
                    lim = max(t.box[2] - t.box[0], t.box[3] - t.box[1]) * 0.7
                    dist = ((tx - dx) ** 2 + (ty - dy) ** 2) ** 0.5
                    s = 0.05 + 0.15 * (1 - dist / lim) if lim and dist < lim else 0
                if s > 0:
                    pairs.append((s, ti, di))
        pairs.sort(reverse=True)
        used_t, used_d = set(), set()
        for _, ti, di in pairs:
            if ti in used_t or di in used_d:
                continue
            used_t.add(ti)
            used_d.add(di)
            t, d = self.tracks[ti], boxes[di]
            t.box, t.last_seen, t.hits = d, now, t.hits + 1
            cx, cy = center(d)
            if ((cx - t.origin[0]) ** 2 + (cy - t.origin[1]) ** 2) ** 0.5 > self.move_frac * W:
                t.moved = True
        for di, d in enumerate(boxes):
            if di not in used_d:
                self.tracks.append(Track(self.next_id, d, now))
                self.next_id += 1

        decisions, alive = [], []
        for t in self.tracks:
            if now - t.last_seen <= self.lost_after:
                alive.append(t)
            elif not t.plate and t.votes:
                plate, _ = t.votes.most_common(1)[0]
                if t.best[plate] >= self.min_conf:            # meilleure lecture avant de disparaître
                    t.plate = plate
                    decisions.append(self._decision(t))
        self.tracks = alive
        return decisions

    def area(self, t):
        b = t.box
        return (b[2] - b[0]) * (b[3] - b[1]) / float(self.W * self.H)

    def state(self, t):
        if t.plate:
            return "plate"
        if t.failed:
            return "failed"
        if t.hits < self.confirm:
            return "checking"
        if not t.moved:
            return "parked"
        if self.area(t) < self.min_area:
            return "far"
        return "reading"

    def next_to_read(self, now):
        """Piste à lire maintenant : confirmée, en mouvement, assez grande, pas encore décidée."""
        ready = [t for t in self.tracks if self.state(t) == "reading"
                 and now - t.last_read >= self.read_gap and t.attempts < self.max_attempts]
        return max(ready, key=self.area) if ready else None

    # ------------------------------------------------------------- lecture
    def add_read(self, t, plate, conf, plate_box=None, plate_img=None, now=0.0):
        """Ajoute une lecture. Renvoie une décision (dict) si la plaque est validée."""
        t.attempts += 1
        t.last_read = now
        if plate and PLATE_RE.match(plate) and conf >= self.min_conf:
            key = match_known(plate, list(t.votes)) or plate
            t.votes[key] += 1
            if conf >= t.best.get(key, 0):
                t.best[key] = conf
                t.plate_box, t.plate_img = plate_box, plate_img
            if t.votes[key] >= self.votes_needed or conf >= self.strong_conf:
                t.plate = key
                return self._decision(t)
        if t.attempts >= self.max_attempts and not t.plate:
            if t.votes:
                key, _ = t.votes.most_common(1)[0]
                t.plate = key
                return self._decision(t)
            t.failed = True
        return None

    def _decision(self, t):
        return {"plate": t.plate, "confidence": t.best.get(t.plate, 0.0), "votes": t.votes[t.plate],
                "plate_img": t.plate_img, "track": t.id}

    # ----------------------------------------------------------- affichage
    # Texte ASCII : la police de cv2.putText ne sait pas dessiner les accents
    LABELS = {"checking": "verification...", "parked": "a l'arret - ignore", "far": "trop loin",
              "reading": "lecture...", "failed": "illisible"}

    def overlay(self, now):
        items = []
        for t in self.tracks:
            if now - t.last_seen > 1.0:
                continue
            st = self.state(t)
            label = t.plate if st == "plate" else self.LABELS[st]
            if st == "reading" and t.attempts:
                label += f" {t.attempts}/{self.max_attempts}"
            items.append({"box": t.box, "state": st, "label": f"#{t.id} {label}",
                          "plate_box": t.plate_box if st == "plate" else None})
        return items

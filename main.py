"""Parking Intelligent — interface « Asphalte ».

L'écran imite un vrai parking : bitume, peinture jaune de marquage, plan du parking
vu de dessus qui se remplit de voitures, plaques dessinées comme de vraies plaques
tunisiennes et barrière qui se lève quand un véhicule est accepté.

Raccourcis :  F1 lire entrée · F2 valider entrée · F3 lire sortie · F4 valider sortie
Lancement  :  python main.py
"""
import csv
import math
import os
import random
import re
import threading
import time
from datetime import datetime
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, messagebox, filedialog, simpledialog

import cv2
from PIL import Image, ImageTk, ImageOps

import config
from auto_gate import AutoTracker, match_known
from database import Database
from detection import CarDetector
from engine import ParkingEngine
from plate_reader import normalize_plate
from utils import format_duration, imread_unicode, imwrite_unicode

# ------------------------------------------------------------------ palette
C = {
    "asphalt": "#141619",   # fond : bitume
    "slab": "#1c1f23",      # dalles de béton sombre
    "slab2": "#262a30",
    "seam": "#3a3f47",      # joints
    "paint": "#f2c230",     # jaune de marquage au sol
    "chalk": "#ebe6da",     # blanc cassé
    "dust": "#8b9098",      # texte secondaire
    "stop": "#e5484d",
    "go": "#46b672",
    "plate": "#0a0a0b",
}
F = {}                      # polices, choisies au démarrage selon ce qui est installé
VW, VH = 440, 264           # taille de l'image caméra
DATE_FMT = "%Y-%m-%d %H:%M:%S"


def init_fonts(root):
    families = set(tkfont.families(root))

    def pick(*names):
        return next((n for n in names if n in families), "Arial")

    # Bahnschrift = police DIN des panneaux routiers, livrée avec Windows 10/11
    F["sign"] = pick("Bahnschrift SemiBold Condensed", "Bahnschrift SemiBold", "Bahnschrift",
                     "Arial Narrow", "DejaVu Sans Condensed")
    F["light"] = pick("Bahnschrift Light", "Bahnschrift", "Segoe UI", "DejaVu Sans")
    F["mono"] = pick("Consolas", "Cascadia Mono", "Courier New", "DejaVu Sans Mono")
    F["ar"] = pick("Segoe UI", "Arial", "DejaVu Sans")


def rounded(canvas, x1, y1, x2, y2, r, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return canvas.create_polygon(pts, smooth=True, **kw)


# ================================================================ PETITS ÉLÉMENTS
class Key(tk.Label):
    """Bouton plat façon touche de pupitre : s'allume à la couleur de sa fonction au survol."""

    def __init__(self, parent, text, cmd, color=None, key=None):
        self.color = color or C["chalk"]
        self.key = key
        super().__init__(parent, bg=C["slab2"], fg=self.color, font=(F["sign"], 11),
                         padx=10, pady=6, cursor="hand2")
        self.set_text(text)
        self.cmd = cmd
        self.bind("<Enter>", lambda e: self.config(bg=self.color, fg=C["asphalt"]))
        self.bind("<Leave>", lambda e: self.config(bg=self.parent_bg(), fg=self.color))
        self.bind("<Button-1>", lambda e: self.cmd())

    def parent_bg(self):
        return C["slab2"]

    def set_text(self, text):
        self.config(text=f"{self.key} ▸ {text}" if self.key else text)


class Hazard(tk.Canvas):
    """Bande jaune et noire, comme sur les poteaux de parking."""

    def __init__(self, parent, height=8):
        super().__init__(parent, height=height, bg=C["asphalt"], highlightthickness=0)
        self.bind("<Configure>", self._draw)

    def _draw(self, e):
        self.delete("all")
        h = e.height
        for x in range(-h, e.width + h, h * 2):
            self.create_polygon(x, h, x + h, 0, x + 2 * h, 0, x + h, h, fill=C["paint"], outline="")


class Barrier(tk.Canvas):
    """Barrière rouge et blanche qui se lève quand un pointage est accepté."""

    W, H = 170, 74

    def __init__(self, parent, mirror=False):
        super().__init__(parent, width=self.W, height=self.H, bg=C["slab"], highlightthickness=0)
        self.angle, self.mirror = 0, mirror
        self.draw()

    def draw(self):
        self.delete("all")
        side = -1 if self.mirror else 1
        px = self.W - 22 if self.mirror else 22
        py, length = 50, 136
        self.create_rectangle(px - 9, py - 6, px + 9, self.H - 2, fill=C["seam"], outline="")
        a = math.radians(self.angle)
        dx, dy = math.cos(a) * side, -math.sin(a)
        n = 8
        for i in range(n):
            self.create_line(px + dx * length * i / n, py + dy * length * i / n,
                             px + dx * length * (i + 1) / n, py + dy * length * (i + 1) / n,
                             width=7, fill=C["stop"] if i % 2 == 0 else C["chalk"])
        self.create_oval(px - 6, py - 6, px + 6, py + 6, fill=C["paint"], outline="")
        lamp = C["go"] if self.angle > 40 else C["stop"]
        self.create_oval(px - 4, self.H - 20, px + 4, self.H - 12, fill=lamp, outline="")

    def cycle(self):
        self._move(80, lambda: self.after(1800, lambda: self._move(0)))

    def _move(self, target, done=None):
        if abs(target - self.angle) <= 5:
            self.angle = target
            self.draw()
            if done:
                done()
            return
        self.angle += 5 if target > self.angle else -5
        self.draw()
        self.after(14, lambda: self._move(target, done))


class PlateView(tk.Canvas):
    """Affiche la plaque comme une vraie plaque tunisienne : 123 تونس 4567."""

    W, H = 292, 62

    def __init__(self, parent, var):
        super().__init__(parent, width=self.W, height=self.H, bg=C["slab"], highlightthickness=0)
        self.var = var
        var.trace_add("write", lambda *_: self.draw())
        self.draw()

    def draw(self):
        self.delete("all")
        w, h = self.W, self.H
        rounded(self, 3, 3, w - 3, h - 3, 10, fill=C["plate"], outline=C["chalk"], width=2)
        text = self.var.get().strip().upper()
        m = re.match(r"^(\d{1,3})\s*TU\s*(\d{1,4})$", text)
        if m or not text:
            left, right = (m.group(1), m.group(2)) if m else ("---", "----")
            col = C["chalk"] if m else C["seam"]
            self.create_text(w * 0.2, h / 2, text=left, fill=col, font=(F["sign"], 26))
            self.create_text(w / 2, h / 2, text="تونس", fill=col, font=(F["ar"], 15, "bold"))
            self.create_text(w * 0.79, h / 2, text=right, fill=col, font=(F["sign"], 26))
        else:
            self.create_text(w / 2, h / 2, text=text, fill=C["paint"], font=(F["sign"], 22))


class LotMap(tk.Canvas):
    """Plan du parking vu de dessus : chaque véhicule présent occupe une place."""

    CAR_COLORS = ["#7d8a96", "#a65f4b", "#cfc8b8", "#50707d", "#8f7c5c", "#5e6166", "#b48f58", "#6d5a7a"]
    MAX_DRAWN = 40

    def __init__(self, parent):
        super().__init__(parent, bg=C["asphalt"], highlightthickness=0, width=400, height=300)
        self.capacity, self.inside = 30, 0
        self.bind("<Configure>", lambda e: self.draw())

    def set_state(self, capacity, inside):
        self.capacity, self.inside = capacity, inside
        self.draw()

    def draw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 60 or h < 60:
            return
        n = min(self.capacity, self.MAX_DRAWN)
        per_row = math.ceil(n / 2)
        m = 12
        sw = (w - 2 * m) / per_row
        sh = h * 0.34
        lane_y = h / 2

        self.create_line(m, lane_y, w - m, lane_y, fill=C["paint"], dash=(12, 9), width=2)
        self.create_text(m + 2, lane_y - 12, text="ENTRÉE ▸", anchor="w", fill=C["dust"], font=(F["sign"], 9))
        self.create_text(w - m - 2, lane_y + 12, text="▸ SORTIE", anchor="e", fill=C["dust"], font=(F["sign"], 9))

        # Les voitures se garent un peu n'importe où, comme dans la vraie vie
        order = list(range(n))
        random.Random(7).shuffle(order)
        occupied = set(order[:min(self.inside, n)])

        for i in range(n):
            row, col = divmod(i, per_row)
            x0 = m + col * sw
            y0, y1 = (m, m + sh) if row == 0 else (h - m - sh, h - m)
            back = y0 if row == 0 else y1
            for x in (x0, x0 + sw):
                self.create_line(x, y0, x, y1, fill=C["chalk"], width=2)
            self.create_line(x0, back, x0 + sw, back, fill=C["chalk"], width=2)
            num_y = y1 + 9 if row == 0 else y0 - 9
            self.create_text(x0 + sw / 2, num_y, text=f"{i + 1:02d}", fill=C["seam"], font=(F["mono"], 8))
            if i in occupied:
                self._car(x0 + sw / 2, (y0 + y1) / 2, sw * 0.62, sh * 0.74,
                          self.CAR_COLORS[(i * 5) % len(self.CAR_COLORS)], front_up=row == 0)

        if self.capacity > n:
            self.create_text(w - m, h - 2, anchor="se", fill=C["dust"], font=(F["mono"], 8),
                             text=f"+{self.capacity - n} places hors plan")

    def _car(self, cx, cy, cw, ch, color, front_up):
        rounded(self, cx - cw / 2, cy - ch / 2, cx + cw / 2, cy + ch / 2, cw * 0.35, fill=color, outline="")
        glass = "#1d2228"
        big, small = ch * 0.2, ch * 0.13
        if front_up:
            ys = (cy - ch * 0.32, cy - ch * 0.32 + big, cy + ch * 0.2, cy + ch * 0.2 + small)
        else:
            ys = (cy + ch * 0.32 - big, cy + ch * 0.32, cy - ch * 0.2 - small, cy - ch * 0.2)
        rounded(self, cx - cw * 0.34, ys[0], cx + cw * 0.34, ys[1], 4, fill=glass, outline="")
        rounded(self, cx - cw * 0.3, ys[2], cx + cw * 0.3, ys[3], 3, fill=glass, outline="")


# ===================================================================== PORTIQUE
class Gate(tk.Frame):
    """Un portique (entrée ou sortie) : caméra, plaque, barrière et commandes."""

    def __init__(self, master, app, mode):
        super().__init__(master, bg=C["slab"])
        self.app, self.mode = app, mode
        self.is_entry = mode == "entree"
        self.source = config.ENTRY_CAMERA if self.is_entry else config.EXIT_CAMERA
        self.cap, self.running, self.frozen = None, False, False
        self.frame, self.captured, self.photo = None, None, None
        self.frame_count, self.last_dets, self.blink = 0, [], False
        self.is_video, self.video_name, self.delay = False, "", 30
        self.video_fps, self.video_total, self.busy = 25.0, 0, False
        self.tracker = AutoTracker(min_area=config.AUTO_MIN_CAR_AREA, max_attempts=config.AUTO_MAX_ATTEMPTS,
                                   votes_needed=config.AUTO_VOTES_NEEDED, strong_conf=config.AUTO_STRONG_CONF)
        self.overlay, self.zoom = [], None
        self.accent = C["go"] if self.is_entry else C["stop"]
        keys = ("F1", "F2") if self.is_entry else ("F3", "F4")

        head = tk.Frame(self, bg=C["slab"])
        head.pack(fill="x", padx=14, pady=(12, 6))
        tk.Label(head, text="ENTRÉE" if self.is_entry else "SORTIE", bg=C["slab"], fg=C["chalk"],
                 font=(F["sign"], 22)).pack(side="left")
        tk.Label(head, text="دخول" if self.is_entry else "خروج", bg=C["slab"], fg=self.accent,
                 font=(F["ar"], 15, "bold")).pack(side="left", padx=10)
        tk.Label(head, text="CAM 01" if self.is_entry else "CAM 02", bg=C["slab"], fg=C["dust"],
                 font=(F["mono"], 9)).pack(side="right")

        # --- viseur caméra
        self.view = tk.Canvas(self, width=VW, height=VH, bg="#0c0d0f", highlightthickness=0)
        self.view.pack(padx=14)
        self.img_item = self.view.create_image(VW / 2, VH / 2)
        self.hint = self.view.create_text(VW / 2, VH / 2, fill=C["dust"], font=(F["light"], 12), justify="center",
                                          text="caméra éteinte\n\nCAMÉRA · VIDÉO · PHOTO\n"
                                               "(cliquez sur l'image pour mettre en pause)")
        self.view.bind("<Button-1>", lambda e: self.toggle_pause())
        L = 22
        for x, y, sx, sy in ((10, 10, 1, 1), (VW - 10, 10, -1, 1), (10, VH - 10, 1, -1), (VW - 10, VH - 10, -1, -1)):
            self.view.create_line(x, y + sy * L, x, y, x + sx * L, y, fill=C["paint"], width=2)
        self.rec = self.view.create_oval(20, 20, 29, 29, fill="", outline="")
        self.rec_txt = self.view.create_text(35, 25, anchor="w", fill=C["chalk"], font=(F["mono"], 9))
        self.ts = self.view.create_text(VW - 20, VH - 20, anchor="se", fill=C["chalk"], font=(F["mono"], 9))

        # --- plaque + barrière
        mid = tk.Frame(self, bg=C["slab"])
        mid.pack(fill="x", padx=14, pady=(12, 0))
        self.plate_var = tk.StringVar()
        PlateView(mid, self.plate_var).pack(side="left", anchor="s")
        self.barrier = Barrier(mid, mirror=not self.is_entry)
        self.barrier.pack(side="right")

        fix = tk.Frame(self, bg=C["slab"])
        fix.pack(fill="x", padx=14, pady=(6, 0))
        tk.Label(fix, text="corriger à la main ›", bg=C["slab"], fg=C["dust"],
                 font=(F["light"], 10)).pack(side="left")
        tk.Entry(fix, textvariable=self.plate_var, bg=C["slab2"], fg=C["chalk"], insertbackground=C["paint"],
                 relief="flat", font=(F["mono"], 12), width=15).pack(side="left", padx=8, ipady=3)
        self.conf_lbl = tk.Label(fix, text="", bg=C["slab"], fg=C["dust"], font=(F["mono"], 9))
        self.conf_lbl.pack(side="right")

        # --- commandes
        row = tk.Frame(self, bg=C["slab"])
        row.pack(fill="x", padx=14, pady=(10, 6))
        self.cam_key = Key(row, "CAMÉRA", self.toggle_camera)
        self.cam_key.pack(side="left")
        self.video_key = Key(row, "VIDÉO", self.toggle_video)
        self.video_key.pack(side="left", padx=4)
        Key(row, "PHOTO", self.load_image).pack(side="left")
        Key(row, "VALIDER", self.validate, color=self.accent, key=keys[1]).pack(side="right")
        Key(row, "LIRE", self.capture, color=C["paint"], key=keys[0]).pack(side="right", padx=6)

        self.status = tk.Label(self, bg=C["slab"], font=(F["mono"], 10), anchor="w")
        self.status.pack(fill="x", padx=14, pady=(2, 12))
        self.say("en attente d'un véhicule…")
        self._blink()

    # ------------------------------------------------------------ affichage
    def say(self, text, color=None):
        self.status.config(text=text, fg=color or C["dust"])

    def _blink(self):
        self.blink = not self.blink
        if self.running and not self.frozen:
            self.view.itemconfig(self.rec, fill=C["stop"] if self.blink else "")
            label = "VIDÉO" if self.is_video else "REC"
            if self.app.auto_scan.get():
                label += " · AUTO"
            self.view.itemconfig(self.rec_txt, text=label)
        else:
            self.view.itemconfig(self.rec, fill="")
            self.view.itemconfig(self.rec_txt, text="PAUSE" if self.frame is not None else "")
        self.after(600, self._blink)

    def show(self, bgr):
        img = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
        self.photo = ImageTk.PhotoImage(ImageOps.contain(img, (VW, VH)))
        self.view.itemconfig(self.img_item, image=self.photo)
        self.view.itemconfig(self.hint, text="")

    # -------------------------------------------------------------- caméra
    def toggle_camera(self):
        if self.running and not self.is_video:
            self.stop()
            return
        self.stop()
        backend = cv2.CAP_DSHOW if os.name == "nt" and isinstance(self.source, int) else cv2.CAP_ANY
        cap = cv2.VideoCapture(self.source, backend)
        if not cap.isOpened():
            messagebox.showerror("Caméra", f"Impossible d'ouvrir la caméra « {self.source} ».\n"
                                           "Vérifiez config.py ou utilisez VIDÉO / PHOTO.")
            return
        self._start(cap, is_video=False)
        self.cam_key.set_text("COUPER")
        self.say("caméra en direct", C["go"])

    def toggle_video(self):
        """Lit une vidéo comme si c'était la caméra : utile pour tester la détection."""
        if self.running and self.is_video:
            self.stop()
            self.say("vidéo arrêtée")
            return
        path = filedialog.askopenfilename(title="Choisir une vidéo",
                                          filetypes=[("Vidéos", "*.mp4 *.avi *.mov *.mkv *.wmv *.m4v"),
                                                     ("Tous", "*.*")])
        if not path:
            return
        self.stop()
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            messagebox.showerror("Vidéo", "Impossible de lire cette vidéo.\nEssayez un fichier .mp4.")
            return
        fps = cap.get(cv2.CAP_PROP_FPS)
        self.video_fps = fps if fps and 1 < fps < 121 else 25.0
        self.video_total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        self.video_name = os.path.basename(path)
        self._start(cap, is_video=True)
        self.video_key.set_text("STOP")
        tip = "AUTO lit les plaques tout seul" if self.app.auto_scan.get() else "LIRE pour analyser l'image"
        self.say(f"vidéo en lecture — {tip}", C["go"])

    def _start(self, cap, is_video):
        self.cap, self.is_video = cap, is_video
        self.delay = max(1, int(1000 / self.video_fps)) if is_video else 30
        self.running, self.frozen, self.captured = True, False, None
        self.last_dets, self.frame_count = [], 0
        self.reset_auto()
        self._loop()

    def reset_auto(self):
        self.tracker.reset()
        self.overlay, self.zoom = [], None

    def toggle_pause(self):
        if not self.running:
            return
        self.frozen = not self.frozen
        if self.frozen:
            self.say("pause — cliquez sur l'image pour reprendre")
        else:
            self.captured = None
            self.say("lecture reprise", C["go"])

    def stop(self):
        self.running, self.is_video = False, False
        if self.cap:
            self.cap.release()
            self.cap = None
        self.cam_key.set_text("CAMÉRA")
        self.video_key.set_text("VIDÉO")

    def _loop(self):
        if not self.running:
            return
        if not self.frozen and self.cap:
            ok, frame = self.cap.read()
            if not ok and self.is_video:                 # fin de la vidéo : on recommence
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ok, frame = self.cap.read()
            if ok:
                self.frame = frame
                self.frame_count += 1
                display = frame.copy()
                engine = self.app.engine
                if self.app.auto_scan.get() and engine.ready:
                    # Zéro pause : la vidéo continue, l'analyse travaille sur une copie en arrière-plan
                    if not self.busy:
                        self.busy = True
                        snap = frame.copy()
                        threading.Thread(target=self._auto_worker, args=(snap,), daemon=True).start()
                    self._draw_overlay(display)
                elif self.app.live_detect.get() and engine.ready:
                    if self.frame_count % config.LIVE_DETECTION_EVERY_N_FRAMES == 0:
                        dets = engine.detect_only(frame)
                        if dets is not None:
                            self.last_dets = dets[:1]
                    CarDetector.draw(display, self.last_dets, color=(48, 194, 242))
                self._draw_zoom(display)
                self.show(display)
                self.view.itemconfig(self.ts, text=self._timestamp())
        self.after(self.delay, self._loop)

    def _timestamp(self):
        if not self.is_video:
            return datetime.now().strftime("%d.%m.%Y  %H:%M:%S")
        pos = int(self.cap.get(cv2.CAP_PROP_POS_FRAMES) / self.video_fps) if self.cap else 0
        total = int(self.video_total / self.video_fps)
        return f"{self.video_name[:24]}  {pos // 60:02d}:{pos % 60:02d} / {total // 60:02d}:{total % 60:02d}"

    # ------------------------------------------------- mode automatique
    def _auto_worker(self, frame):
        """Thread : détecte, suit, lit une piste, vote. Ne touche jamais à l'interface."""
        engine, tracker = self.app.engine, self.tracker
        decisions, reading, error = [], None, None
        try:
            now = time.time()
            h, w = frame.shape[:2]
            boxes = [d[:4] for d in engine.detect_all(frame)]
            decisions += tracker.update(boxes, w, h, now)
            t = tracker.next_to_read(now)
            if t is not None:
                r = engine.read_box(frame, t.box)
                d = tracker.add_read(t, r["plate"], r["confidence"], r["plate_box"], r["plate_img"], time.time())
                if d:
                    decisions.append(d)
                reading = (t.id, t.attempts, r["plate"], r["confidence"])
            overlay = tracker.overlay(time.time())
        except Exception as e:
            overlay, error = [], str(e)
        self.after(0, lambda: self._on_auto(overlay, decisions, reading, frame, error))

    def _on_auto(self, overlay, decisions, reading, frame, error):
        self.busy = False
        if not self.running or not self.app.auto_scan.get():
            self.overlay = []
            return
        self.overlay = overlay
        if error:
            self.say(f"✗ analyse : {error[:60]}", C["stop"])
            return
        if reading and not decisions:
            tid, n, plate, conf = reading
            seen = f"« {plate} » {conf:.0%}" if plate else "rien de lisible"
            self.conf_lbl.config(text=f"#{tid} lecture {n} : {seen}")
        for d in decisions:
            self._auto_register(d, frame)

    def _auto_register(self, d, frame):
        """Enregistre automatiquement l'entrée ou la sortie, sans clic."""
        app, plate, now = self.app, d["plate"], time.time()
        inside = app.db.inside_plates()
        known = match_known(plate, inside)
        if app.lane_auto:
            going_out = known is not None             # déjà dans le parking -> c'est une sortie
        else:
            going_out = not self.is_entry
        if going_out and known:
            plate = known                              # corrige une petite erreur d'OCR
        if now - app.last_hit.get(plate, 0) < config.AUTO_COOLDOWN:
            return                                     # même voiture encore devant la caméra
        app.last_hit[plate] = now

        shot = frame.copy()
        self._draw_overlay(shot)
        safe = re.sub(r"[^0-9A-Za-z]+", "_", plate)
        kind = "sortie" if going_out else "entree"
        image_path = os.path.join(config.CAPTURES_DIR, f"{kind}_{datetime.now():%Y%m%d_%H%M%S}_{safe}.jpg")
        imwrite_unicode(image_path, shot)

        if going_out:
            ok, msg, minutes = app.db.register_exit(plate, image_path)
            done = f"✓ AUTO {plate} sorti · resté {format_duration(minutes)}" if ok else ""
            color = C["stop"]
        else:
            ok, msg = app.db.register_entry(plate, image_path)
            done = f"✓ AUTO {plate} entré à {datetime.now():%H:%M}"
            color = C["go"]
        votes = f"{d['votes']} lecture{'s' if d['votes'] > 1 else ''}, {d['confidence']:.0%}"
        if ok:
            self.plate_var.set(plate)
            self.conf_lbl.config(text=votes)
            self.barrier.cycle()
            self.say(done, color)
            if d.get("plate_img") is not None:
                self.zoom = (d["plate_img"], plate, now + 6)
        else:
            self.say("✗ " + msg.split("\n")[0], C["stop"])
            self.bell()
        app.refresh()

    def _draw_overlay(self, img):
        """Dessine les pistes : gris = vérification/ignoré, jaune = lecture, vert = plaque, rouge = illisible."""
        colors = {"plate": (114, 182, 70), "reading": (48, 194, 242), "failed": (77, 72, 229)}
        h, w = img.shape[:2]
        lw = max(2, w // 400)
        fs = max(0.5, w / 1600)
        for it in self.overlay:
            col = colors.get(it["state"], (152, 144, 139))
            x1, y1, x2, y2 = map(int, it["box"])
            cv2.rectangle(img, (x1, y1), (x2, y2), col, lw if it["state"] != "checking" else 1)
            (tw, th), _ = cv2.getTextSize(it["label"], cv2.FONT_HERSHEY_SIMPLEX, fs, lw)
            ty = max(th + 8, y1)
            cv2.rectangle(img, (x1, ty - th - 8), (x1 + tw + 10, ty), col, -1)
            cv2.putText(img, it["label"], (x1 + 5, ty - 5), cv2.FONT_HERSHEY_SIMPLEX, fs, (20, 22, 25), lw)
            if it["plate_box"]:
                px1, py1, px2, py2 = map(int, it["plate_box"])
                cv2.rectangle(img, (px1, py1), (px2, py2), (114, 182, 70), lw + 1)

    def _draw_zoom(self, img):
        """Incruste le zoom net de la plaque validée en bas à droite pendant quelques secondes."""
        if not self.zoom or time.time() > self.zoom[2]:
            return
        plate_img, plate, _ = self.zoom
        h, w = img.shape[:2]
        z = plate_img if plate_img.ndim == 3 else cv2.cvtColor(plate_img, cv2.COLOR_GRAY2BGR)
        zw = int(w * 0.3)
        zh = max(20, int(z.shape[0] * zw / z.shape[1]))
        if zh > h * 0.4:
            zh = int(h * 0.4)
            zw = int(z.shape[1] * zh / z.shape[0])
        z = cv2.resize(z, (zw, zh), interpolation=cv2.INTER_LANCZOS4)
        m = int(w * 0.02)
        x0, y0 = w - zw - m, h - zh - m
        lw = max(2, w // 400)
        cv2.rectangle(img, (x0 - lw * 3, y0 - int(h * 0.07)), (x0 + zw + lw * 3, y0 + zh + lw * 3), (20, 22, 25), -1)
        img[y0:y0 + zh, x0:x0 + zw] = z
        cv2.rectangle(img, (x0, y0), (x0 + zw, y0 + zh), (114, 182, 70), lw)
        cv2.putText(img, f"PLAQUE {plate}", (x0, y0 - int(h * 0.02)), cv2.FONT_HERSHEY_SIMPLEX,
                    max(0.5, w / 1500), (114, 182, 70), lw)

    def load_image(self):
        path = filedialog.askopenfilename(title="Choisir une photo de voiture",
                                          filetypes=[("Images", "*.jpg *.jpeg *.png *.bmp *.webp"), ("Tous", "*.*")])
        if not path:
            return
        img = imread_unicode(path)
        if img is None:
            messagebox.showerror("Image", "Impossible de lire cette image.")
            return
        if self.running:
            self.stop()
        self.frame, self.frozen, self.captured = img, True, None
        self.plate_var.set("")
        self.conf_lbl.config(text="")
        self.show(img)
        self.view.itemconfig(self.ts, text=os.path.basename(path)[:40])
        self.say("photo chargée — appuyez sur LIRE")

    # ---------------------------------------------------- capture + lecture
    def capture(self):
        if self.frame is None:
            self.say("✗ pas d'image : allumez la caméra ou chargez une photo", C["stop"])
            return
        if not self.app.engine.ready:
            self.say(self.app.engine.error and "✗ moteur hors ligne — tapez la plaque à la main"
                     or "… le moteur charge encore, patientez", C["paint"])
            return
        self.frozen = True
        frame = self.frame.copy()
        self.say("lecture de la plaque…", C["paint"])
        threading.Thread(target=lambda: self._process(frame), daemon=True).start()

    def _process(self, frame):
        try:
            result = self.app.engine.process(frame)
        except Exception as e:
            result = {"error": str(e)}
        self.after(0, lambda: self._on_result(result))

    def _on_result(self, r):
        if "error" in r:
            self.say(f"✗ erreur : {r['error'][:60]}", C["stop"])
            return
        self.show(r["annotated"])
        self.captured = r["annotated"]
        if r["plate"]:
            self.plate_var.set(r["plate"])
            self.conf_lbl.config(text=f"sûr à {r['confidence']:.0%}")
            self.say("plaque lue — vérifiez puis VALIDER", C["chalk"])
        else:
            self.plate_var.set("")
            self.conf_lbl.config(text="")
            what = "véhicule vu" if r["vehicle_found"] else "aucun véhicule vu"
            self.say(f"? {what}, plaque illisible — tapez-la à la main", C["paint"])

    # ---------------------------------------------------------- validation
    def validate(self):
        plate = normalize_plate(self.plate_var.get())
        if not plate:
            self.say("✗ aucune plaque à valider", C["stop"])
            self.bell()
            return
        self.plate_var.set(plate)
        image_path = None
        img = self.captured if self.captured is not None else self.frame
        if img is not None:
            safe = re.sub(r"[^0-9A-Za-z]+", "_", plate)
            image_path = os.path.join(config.CAPTURES_DIR, f"{self.mode}_{datetime.now():%Y%m%d_%H%M%S}_{safe}.jpg")
            imwrite_unicode(image_path, img)

        if self.is_entry:
            ok, msg = self.app.db.register_entry(plate, image_path)
            done = f"✓ {plate} entré à {datetime.now():%H:%M}"
        else:
            ok, msg, minutes = self.app.db.register_exit(plate, image_path)
            done = f"✓ {plate} sorti · resté {format_duration(minutes)}" if ok else ""

        if ok:
            self.barrier.cycle()
            self.say(done, self.accent)
            self.plate_var.set("")
            self.conf_lbl.config(text="")
            self.captured = None
            self.frozen = False
        else:
            self.say("✗ " + msg.split("\n")[0], C["stop"])
            self.bell()
        self.app.refresh()


# ==================================================================== APPLICATION
class ParkingApp(tk.Tk):
    FILTERS = ["Tous", "En cours", "Terminé"]

    def __init__(self):
        super().__init__()
        init_fonts(self)
        self.title("Parking · موقف")
        self.geometry("1440x920")
        self.minsize(1300, 820)
        self.configure(bg=C["asphalt"])

        self.db = Database()
        self.engine = ParkingEngine()
        self.live_detect = tk.BooleanVar(value=True)
        self.auto_scan = tk.BooleanVar(value=False)
        self.lane_auto = False          # True : une seule caméra, entrée/sortie devinée par la base
        self.last_hit = {}              # plaque -> dernier pointage auto (anti-doublon)
        self.filter_idx = 0

        self._style()
        self._topbar()
        Hazard(self).pack(fill="x")
        self._body()
        self._journal()

        self.refresh()
        self._tick()
        self.bind_all("<F1>", lambda e: self.entry_gate.capture())
        self.bind_all("<F2>", lambda e: self.entry_gate.validate())
        self.bind_all("<F3>", lambda e: self.exit_gate.capture())
        self.bind_all("<F4>", lambda e: self.exit_gate.validate())
        threading.Thread(target=self._load_models, daemon=True).start()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # --------------------------------------------------------------- style
    def _style(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure("Journal.Treeview", background=C["slab"], fieldbackground=C["slab"], foreground=C["chalk"],
                    rowheight=30, font=(F["mono"], 10), borderwidth=0)
        s.configure("Journal.Treeview.Heading", background=C["asphalt"], foreground=C["paint"],
                    font=(F["sign"], 11), relief="flat", borderwidth=0, padding=(0, 6))
        s.map("Journal.Treeview.Heading", background=[("active", C["asphalt"])])
        s.map("Journal.Treeview", background=[("selected", C["seam"])], foreground=[("selected", C["paint"])])
        s.layout("Journal.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        s.configure("Dark.Vertical.TScrollbar", troughcolor=C["slab"], background=C["seam"],
                    arrowcolor=C["dust"], bordercolor=C["slab"], lightcolor=C["seam"], darkcolor=C["seam"])

    # -------------------------------------------------------------- en-tête
    def _topbar(self):
        bar = tk.Frame(self, bg=C["asphalt"])
        bar.pack(fill="x", padx=20, pady=(12, 10))
        tk.Label(bar, text="PARKING", bg=C["asphalt"], fg=C["chalk"], font=(F["sign"], 28)).pack(side="left")
        tk.Label(bar, text="موقف", bg=C["asphalt"], fg=C["paint"], font=(F["ar"], 20, "bold")).pack(side="left", padx=12)
        self.engine_lbl = tk.Label(bar, text="moteur : chargement…", bg=C["asphalt"], fg=C["paint"],
                                   font=(F["mono"], 9))
        self.engine_lbl.pack(side="left", padx=(24, 0), pady=(10, 0))

        self.clock = tk.Label(bar, bg=C["asphalt"], fg=C["chalk"], font=(F["mono"], 18))
        self.clock.pack(side="right", padx=(18, 0))
        Key(bar, "QUITTER", self.on_close, color=C["stop"]).pack(side="right", padx=3)
        Key(bar, "?", self.about, color=C["dust"]).pack(side="right", padx=3)
        Key(bar, "PLACES", self.settings).pack(side="right", padx=3)
        self.live_key = Key(bar, "LIVE ●", self.toggle_live, color=C["go"])
        self.live_key.pack(side="right", padx=3)
        self.auto_key = Key(bar, "AUTO ○", self.toggle_auto, color=C["dust"])
        self.auto_key.pack(side="right", padx=3)
        self.lane_key = Key(bar, "SENS : PORTIQUES", self.toggle_lane, color=C["dust"])
        self.lane_key.pack(side="right", padx=3)

    # --------------------------------------------------------------- corps
    def _body(self):
        body = tk.Frame(self, bg=C["asphalt"])
        body.pack(fill="x", padx=20, pady=(14, 0))
        self.entry_gate = Gate(body, self, "entree")
        self.entry_gate.pack(side="left", anchor="n")
        self.exit_gate = Gate(body, self, "sortie")
        self.exit_gate.pack(side="right", anchor="n")

        mid = tk.Frame(body, bg=C["asphalt"])
        mid.pack(side="left", fill="both", expand=True, padx=16)
        tk.Label(mid, text="PLAN DU PARKING", bg=C["asphalt"], fg=C["dust"],
                 font=(F["sign"], 11)).pack(anchor="w")
        self.lot = LotMap(mid)
        self.lot.pack(fill="both", expand=True, pady=(4, 8))

        big = tk.Frame(mid, bg=C["asphalt"])
        big.pack(fill="x")
        self.free_lbl = tk.Label(big, text="0", bg=C["asphalt"], fg=C["paint"], font=(F["sign"], 60))
        self.free_lbl.pack(side="left")
        side = tk.Frame(big, bg=C["asphalt"])
        side.pack(side="left", padx=10, pady=(14, 0), anchor="w")
        self.cap_lbl = tk.Label(side, text="/ 30", bg=C["asphalt"], fg=C["dust"], font=(F["sign"], 20))
        self.cap_lbl.pack(anchor="w")
        tk.Label(side, text="places libres · أماكن شاغرة", bg=C["asphalt"], fg=C["chalk"],
                 font=(F["light"], 10)).pack(anchor="w")
        self.today_lbl = tk.Label(mid, bg=C["asphalt"], fg=C["dust"], font=(F["mono"], 10), anchor="w")
        self.today_lbl.pack(fill="x", pady=(2, 0))

    # ------------------------------------------------------------- journal
    def _journal(self):
        box = tk.Frame(self, bg=C["slab"])
        box.pack(fill="both", expand=True, padx=20, pady=16)

        bar = tk.Frame(box, bg=C["slab"])
        bar.pack(fill="x", padx=14, pady=(12, 8))
        tk.Label(bar, text="JOURNAL DES PASSAGES", bg=C["slab"], fg=C["chalk"], font=(F["sign"], 15)).pack(side="left")
        tk.Label(bar, text="سجل", bg=C["slab"], fg=C["paint"], font=(F["ar"], 12, "bold")).pack(side="left", padx=8)

        Key(bar, "EXPORTER", self.export_csv).pack(side="right", padx=3)
        Key(bar, "SUPPRIMER", self.delete_selected, color=C["stop"]).pack(side="right", padx=3)
        self.filter_key = Key(bar, "TOUS", self.cycle_filter, color=C["paint"])
        self.filter_key.pack(side="right", padx=3)
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self.refresh_history())
        self.search_entry = tk.Entry(bar, textvariable=self.search_var, bg=C["slab2"], fg=C["chalk"],
                                     insertbackground=C["paint"], relief="flat", font=(F["mono"], 11), width=22)
        self.search_entry.pack(side="right", padx=(6, 12), ipady=5)
        tk.Label(bar, text="⌕ plaque", bg=C["slab"], fg=C["dust"], font=(F["light"], 10)).pack(side="right")

        cols = ("id", "jour", "plaque", "arrivee", "depart", "duree", "etat")
        heads = ("N°", "JOUR", "PLAQUE", "ARRIVÉE", "DÉPART", "DURÉE", "ÉTAT")
        widths = (60, 120, 190, 110, 110, 170, 140)
        frame = tk.Frame(box, bg=C["slab"])
        frame.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        self.tree = ttk.Treeview(frame, columns=cols, show="headings", style="Journal.Treeview")
        for c, hd, w in zip(cols, heads, widths):
            self.tree.heading(c, text=hd)
            self.tree.column(c, width=w, anchor="center")
        sb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview, style="Dark.Vertical.TScrollbar")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.tag_configure("inside", foreground=C["paint"])
        self.tree.tag_configure("gone", foreground=C["dust"])
        self.tree.tag_configure("odd", background=C["slab2"])
        self.tree.bind("<Double-1>", self.show_details)

    # ------------------------------------------------------------- données
    def refresh(self):
        s = self.db.stats()
        self.lot.set_state(s["capacity"], s["inside"])
        self.free_lbl.config(text=str(s["free"]), fg=C["stop"] if s["free"] == 0 else C["paint"])
        self.cap_lbl.config(text=f"/ {s['capacity']}")
        self.today_lbl.config(text=f"aujourd'hui   ↓ {s['entries_today']} entrées   "
                                   f"↑ {s['exits_today']} sorties   ● {s['inside']} sur place")
        self.refresh_history()

    def refresh_history(self):
        selected = set(self.tree.selection())
        self.tree.delete(*self.tree.get_children())
        now = datetime.now()
        rows = self.db.history(self.search_var.get(), self.FILTERS[self.filter_idx])
        for n, r in enumerate(rows):
            d_in = datetime.strptime(r["date_entree"], DATE_FMT)
            done = r["date_sortie"] is not None
            if done:
                duree = format_duration(r["duree_minutes"])
            else:
                duree = format_duration(int((now - d_in).total_seconds() // 60)) + " …"
            tags = ("gone" if done else "inside",) + (("odd",) if n % 2 else ())
            self.tree.insert("", "end", iid=str(r["id"]), tags=tags, values=(
                r["id"], d_in.strftime("%d/%m/%Y"), r["immatriculation"], d_in.strftime("%H:%M"),
                r["date_sortie"][11:16] if done else "—", duree, "parti" if done else "● sur place"))
        keep = [i for i in selected if self.tree.exists(i)]
        if keep:
            self.tree.selection_set(keep)

    def cycle_filter(self):
        self.filter_idx = (self.filter_idx + 1) % len(self.FILTERS)
        self.filter_key.set_text(self.FILTERS[self.filter_idx].upper())
        self.refresh_history()

    def toggle_live(self):
        self.live_detect.set(not self.live_detect.get())
        on = self.live_detect.get()
        self.live_key.color = C["go"] if on else C["dust"]
        self.live_key.config(fg=self.live_key.color)
        self.live_key.set_text("LIVE ●" if on else "LIVE ○")
        for gate in (self.entry_gate, self.exit_gate):
            gate.last_dets = []

    def toggle_auto(self):
        """AUTO : pendant la caméra/vidéo, la plaque est lue toute seule environ chaque seconde."""
        self.auto_scan.set(not self.auto_scan.get())
        on = self.auto_scan.get()
        self.auto_key.color = C["paint"] if on else C["dust"]
        self.auto_key.config(fg=self.auto_key.color)
        self.auto_key.set_text("AUTO ●" if on else "AUTO ○")
        for gate in (self.entry_gate, self.exit_gate):
            gate.reset_auto()
            if gate.running:
                gate.say("mode automatique : lecture et pointage sans clic" if on else "mode manuel",
                         C["paint"] if on else None)

    def toggle_lane(self):
        """PORTIQUES : caméra gauche = entrée, droite = sortie.
        AUTO : n'importe quelle caméra ; plaque déjà dans le parking = sortie, sinon entrée."""
        self.lane_auto = not self.lane_auto
        self.lane_key.color = C["paint"] if self.lane_auto else C["dust"]
        self.lane_key.config(fg=self.lane_key.color)
        self.lane_key.set_text("SENS : AUTO" if self.lane_auto else "SENS : PORTIQUES")

    def delete_selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Supprimer", "Sélectionnez une ligne du journal.")
            return
        if messagebox.askyesno("Supprimer", f"Supprimer {len(sel)} passage(s) ?"):
            for iid in sel:
                self.db.delete_pointage(int(iid))
            self.refresh()

    def show_details(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            return
        r = self.db.conn.execute("SELECT * FROM pointages WHERE id=?", (int(sel[0]),)).fetchone()
        win = tk.Toplevel(self, bg=C["asphalt"])
        win.title(f"Passage n°{r['id']}")
        top = tk.Frame(win, bg=C["asphalt"])
        top.pack(fill="x", padx=18, pady=(16, 6))
        var = tk.StringVar(value=r["immatriculation"])
        PlateView(top, var).pack(side="left")
        info = (f"arrivée   {r['date_entree']}\n"
                f"départ    {r['date_sortie'] or '— toujours sur place'}\n"
                f"durée     {format_duration(r['duree_minutes'])}")
        tk.Label(top, text=info, bg=C["asphalt"], fg=C["chalk"], font=(F["mono"], 11),
                 justify="left").pack(side="left", padx=18)
        imgs = tk.Frame(win, bg=C["asphalt"])
        imgs.pack(padx=12, pady=(6, 16))
        win.photos = []
        for title, path in (("ENTRÉE", r["image_entree"]), ("SORTIE", r["image_sortie"])):
            if path and os.path.exists(path):
                img = Image.fromarray(cv2.cvtColor(imread_unicode(path), cv2.COLOR_BGR2RGB))
                photo = ImageTk.PhotoImage(ImageOps.contain(img, (380, 250)))
                win.photos.append(photo)
                col = tk.Frame(imgs, bg=C["asphalt"])
                col.pack(side="left", padx=6)
                tk.Label(col, text=title, bg=C["asphalt"], fg=C["paint"], font=(F["sign"], 11)).pack(anchor="w")
                tk.Label(col, image=photo, bg=C["asphalt"]).pack()

    def export_csv(self):
        path = filedialog.asksaveasfilename(defaultextension=".csv", initialfile="journal_parking.csv",
                                            filetypes=[("CSV", "*.csv")])
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["ID", "Immatriculation", "Date entrée", "Date sortie", "Durée (min)", "Statut"])
            for r in self.db.history():
                w.writerow([r["id"], r["immatriculation"], r["date_entree"], r["date_sortie"] or "",
                            r["duree_minutes"] if r["duree_minutes"] is not None else "",
                            "Terminé" if r["date_sortie"] else "En cours"])
        messagebox.showinfo("Export", f"Journal exporté :\n{path}")

    def settings(self):
        value = simpledialog.askinteger("Places", "Nombre total de places :",
                                        initialvalue=self.db.get_capacity(), minvalue=1, maxvalue=10000)
        if value:
            self.db.set_capacity(value)
            self.refresh()

    def about(self):
        messagebox.showinfo("À propos",
                            "Parking · موقف\n\n"
                            "Détection : YOLOv8 + OpenCV\nPlaque : OpenCV + EasyOCR (arabe / chiffres)\n"
                            "Base : SQLite · Interface : Tkinter\n\n"
                            "F1 lire entrée · F2 valider entrée\nF3 lire sortie · F4 valider sortie")

    # -------------------------------------------------------------- divers
    def _load_models(self):
        self.engine.load()
        self.after(0, self._on_models_loaded)

    def _on_models_loaded(self):
        if self.engine.ready:
            self.engine_lbl.config(text="moteur prêt", fg=C["go"])
        else:
            self.engine_lbl.config(text="moteur hors ligne — saisie manuelle", fg=C["stop"])
            messagebox.showwarning("Moteur", f"Impossible de charger les modèles :\n{self.engine.error}\n\n"
                                             "Installez les dépendances : pip install -r requirements.txt")

    def _tick(self, n=0):
        self.clock.config(text=datetime.now().strftime("%H:%M:%S"))
        if n and n % 30 == 0:
            self.refresh_history()          # met à jour les durées « sur place »
        self.after(1000, lambda: self._tick(n + 1))

    def on_close(self):
        self.entry_gate.stop()
        self.exit_gate.stop()
        self.db.close()
        self.destroy()


if __name__ == "__main__":
    ParkingApp().mainloop()

"""Gestion de la base de données SQLite (véhicules + pointages)."""
import os
import sqlite3
from datetime import datetime

from config import DB_PATH, DEFAULT_CAPACITY
from utils import format_duration

DATE_FMT = "%Y-%m-%d %H:%M:%S"


class Database:
    def __init__(self, path=DB_PATH):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self):
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS vehicules (
                immatriculation TEXT PRIMARY KEY,
                premiere_visite TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS pointages (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                immatriculation TEXT NOT NULL,
                date_entree     TEXT NOT NULL,
                date_sortie     TEXT,
                duree_minutes   INTEGER,
                image_entree    TEXT,
                image_sortie    TEXT,
                FOREIGN KEY (immatriculation) REFERENCES vehicules(immatriculation)
            );
            CREATE INDEX IF NOT EXISTS idx_pointages_plaque ON pointages(immatriculation);
            CREATE TABLE IF NOT EXISTS parametres (
                cle    TEXT PRIMARY KEY,
                valeur TEXT
            );
            """
        )
        self.conn.commit()

    # ------------------------------------------------------------ paramètres
    def get_capacity(self):
        row = self.conn.execute("SELECT valeur FROM parametres WHERE cle='capacite'").fetchone()
        return int(row["valeur"]) if row else DEFAULT_CAPACITY

    def set_capacity(self, value):
        self.conn.execute("INSERT OR REPLACE INTO parametres VALUES('capacite', ?)", (str(int(value)),))
        self.conn.commit()

    # -------------------------------------------------------------- pointage
    def get_open_entry(self, plate):
        """Dernier pointage d'entrée sans sortie pour cette plaque."""
        return self.conn.execute(
            "SELECT * FROM pointages WHERE immatriculation=? AND date_sortie IS NULL "
            "ORDER BY date_entree DESC LIMIT 1",
            (plate,),
        ).fetchone()

    def is_inside(self, plate):
        return self.get_open_entry(plate) is not None

    def inside_plates(self):
        rows = self.conn.execute("SELECT DISTINCT immatriculation FROM pointages WHERE date_sortie IS NULL").fetchall()
        return [r[0] for r in rows]

    def count_inside(self):
        return self.conn.execute("SELECT COUNT(*) FROM pointages WHERE date_sortie IS NULL").fetchone()[0]

    def register_entry(self, plate, image_path=None):
        """Entrée : vérifie que le véhicule n'est pas déjà dans le parking, puis enregistre."""
        if self.is_inside(plate):
            return False, f"Le véhicule {plate} est déjà dans le parking."
        if self.count_inside() >= self.get_capacity():
            return False, "Parking complet : aucune place disponible."
        now = datetime.now().strftime(DATE_FMT)
        self.conn.execute("INSERT OR IGNORE INTO vehicules VALUES (?, ?)", (plate, now))
        self.conn.execute(
            "INSERT INTO pointages (immatriculation, date_entree, image_entree) VALUES (?, ?, ?)",
            (plate, now, image_path),
        )
        self.conn.commit()
        return True, f"Entrée de {plate} enregistrée à {now}."

    def register_exit(self, plate, image_path=None):
        """Sortie : retrouve la dernière entrée, enregistre la sortie et calcule la durée."""
        row = self.get_open_entry(plate)
        if row is None:
            return False, f"Aucune entrée en cours trouvée pour {plate}.", None
        now = datetime.now()
        entree = datetime.strptime(row["date_entree"], DATE_FMT)
        minutes = int((now - entree).total_seconds() // 60)
        self.conn.execute(
            "UPDATE pointages SET date_sortie=?, duree_minutes=?, image_sortie=? WHERE id=?",
            (now.strftime(DATE_FMT), minutes, image_path, row["id"]),
        )
        self.conn.commit()
        return True, f"Sortie de {plate} enregistrée.\nDurée de stationnement : {format_duration(minutes)}", minutes

    def delete_pointage(self, pointage_id):
        self.conn.execute("DELETE FROM pointages WHERE id=?", (pointage_id,))
        self.conn.commit()

    # ------------------------------------------------------------ consultation
    def history(self, search="", status="Tous"):
        query = "SELECT * FROM pointages WHERE immatriculation LIKE ?"
        params = [f"%{search.strip()}%"]
        if status == "En cours":
            query += " AND date_sortie IS NULL"
        elif status == "Terminé":
            query += " AND date_sortie IS NOT NULL"
        query += " ORDER BY id DESC"
        return self.conn.execute(query, params).fetchall()

    def stats(self):
        today = datetime.now().strftime("%Y-%m-%d") + "%"
        q = lambda sql, *p: self.conn.execute(sql, p).fetchone()[0]
        inside = self.count_inside()
        capacity = self.get_capacity()
        return {
            "inside": inside,
            "entries_today": q("SELECT COUNT(*) FROM pointages WHERE date_entree LIKE ?", today),
            "exits_today": q("SELECT COUNT(*) FROM pointages WHERE date_sortie LIKE ?", today),
            "capacity": capacity,
            "free": max(capacity - inside, 0),
        }

    def close(self):
        self.conn.close()

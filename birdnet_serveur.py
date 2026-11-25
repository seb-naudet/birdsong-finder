# birdnet_serveur.py - serveur LOCAL d'analyse BirdNET (gros PC, Windows)
#
# Analyse les fichiers audio deposes dans ./audio/ (SOUS-DOSSIERS COMPRIS) et
# sert une interface HTML accessible (NVDA) sur http://127.0.0.1:8790/.
# L'interface vit dans birdnet.html, SEPARE de ce serveur (relu a chaque
# requete : modifiable sans redemarrer).
#
# Strategie par nombre de canaux (acquis du projet vps ambisonique) :
#   1 canal  -> analyse directe.
#   2 canaux -> gauche et droite SEPARES (+45 % d'especes mesure vs somme mono)
#               + canal cote (L-R). Position grossiere gauche/droite.
#   4 canaux -> suppose AmbiX (W Y Z X) : 6 micros virtuels comme sur le VPS
#               (W + 4 cardioides en quadrants + haut), position rabattue sur
#               l'axe gauche-droite (5 crans, avant/arriere confondus).
#   N canaux -> chaque canal separement (plafond MAX_CANAUX), sans position.
# Passe-haut 200 Hz APRES combinaison (beamforming lineaire), overlap 1,5 s,
# recalage temporel "t" au pic d'energie (le nom sort quand l'oiseau chante).
#
# Resultats <fichier>.birds.json dans analyses/ (format v2 du VPS).
# Veille automatique : nouveau fichier -> analyse ; fichier remplace ->
# reanalyse ; audio supprime -> sidecar retire ; fichier trop recent ->
# attend le tour suivant (transfert en cours).
#
# Noms d'oiseaux : les 35 langues officielles BirdNET dans ./labels/
# (servies par /api/labels, la langue se choisit dans la page).
#
# Lancement :  venv\Scripts\python birdnet_serveur.py   (page dans le navigateur)
# Programme portable : birdnet_app.py importe ce module, sert la meme page
# dans SA fenetre (WebView2) et branche ses crochets (APPLI, CHOISIR_DOSSIER,
# QUITTER, MONTRER). Gele par PyInstaller, les donnees de l'utilisateur
# vivent a cote de l'exe, les ressources dans _internal (sys._MEIPASS).
import io
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import ctypes
from ctypes import wintypes
import hashlib
import queue
import shutil
import unicodedata
import urllib.parse
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Bibliotheques de calcul : chargees par charger_calcul() au DEMARRAGE du
# serveur, pas a l'import du module. Le programme a fenetre importe ce
# module tout de suite (chemins, crochets) et montre sa fenetre pendant ce
# chargement, long au premier lancement (antivirus + caches froids).
np = sf = signal = None


def charger_calcul():
    global np, sf, signal
    if np is not None:
        return
    # Prevenir AVANT : sinon la console reste muette et on croit a un
    # plantage.
    print("Demarrage... chargement des bibliotheques d'analyse", flush=True)
    print("(le premier lancement peut prendre une minute, patience)",
          flush=True)
    import numpy
    import soundfile
    import scipy.signal
    np, sf, signal = numpy, soundfile, scipy.signal


# --- Reglages ---
PORT = int(os.environ.get("BIRDNET_PORT", "8790"))   # autre port : essais
GELE = getattr(sys, "frozen", False)     # programme portable (PyInstaller)


def inscriptible(d):
    """Vrai si on peut creer des fichiers dans d (un exe dezippe dans
    Program Files, ou sur un support protege, ne le peut pas)."""
    try:
        os.makedirs(d, exist_ok=True)
        essai = os.path.join(d, ".essai_ecriture")
        with open(essai, "w") as f:
            f.write("ok")
        os.remove(essai)
        return True
    except OSError:
        return False


if GELE:
    # Ressources figees (page, noms d'oiseaux, ffmpeg) dans _internal ; les
    # donnees de l'utilisateur A COTE DE L'EXE (programme portable : une cle
    # USB emporte tout). Dossier protege en ecriture -> profil Windows.
    DOSSIER = os.path.dirname(os.path.abspath(sys.executable))
    RESSOURCES = getattr(sys, "_MEIPASS", DOSSIER)
    DONNEES = os.path.join(DOSSIER, "data")
    AUDIO_DIR = os.path.join(DOSSIER, "audio")
    if not inscriptible(DONNEES):
        DONNEES = os.path.join(
            os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
            os.path.splitext(os.path.basename(sys.executable))[0])
        AUDIO_DIR = os.path.join(DONNEES, "audio")
else:
    DOSSIER = RESSOURCES = DONNEES = os.path.dirname(os.path.abspath(__file__))
    AUDIO_DIR = os.path.join(DOSSIER, "audio")
LABELS_DIR = os.path.join(RESSOURCES, "labels")
TMP_DIR = os.path.join(DONNEES, "tmp")
# Resultats d'analyse (.birds.json) RANGES A PART de l'audio (2026-11-24,
# demande de l'auteur) : analyses/audio/ reflete le dossier audio/, et chaque
# dossier ajoute a son sous-dossier "<nom>_<empreinte>" (dossier.txt y garde
# le chemin d'origine). L'audio n'est jamais touche.
ANALYSES_DIR = os.path.join(DONNEES, "analyses")
BIN_DIR = os.path.join(RESSOURCES, "bin")
PAGE = os.path.join(RESSOURCES, "birdnet.html")

# Crochets poses par birdnet_app.py (programme a fenetre). Restent None
# quand le serveur tourne seul (page dans le navigateur, .bat).
APPLI = None            # {"nom": ..., "version": ...} : infos pour la page
CHOISIR_DOSSIER = None  # () -> chemin ou None : boite native de la fenetre
QUITTER = None          # () -> ferme la fenetre (et donc le programme)
ARRET = threading.Event()   # arret demande : l'analyse en cours s'interrompt
PROC_FFMPEG = None          # ffmpeg en cours (a tuer a l'arret)


class Interrompu(Exception):
    """Analyse interrompue par l'arret du programme (pas une erreur : rien
    n'est ecrit, le fichier repart en analyse au prochain lancement)."""
MONTRER = None          # () -> ramene la fenetre au premier plan

# Boite Windows de choix de dossier (tkinter) pour le serveur SANS fenetre :
# lancee dans un processus a part, elle ecrit le chemin choisi sur stdout.
CODE_BOITE_DOSSIER = (
    "import sys, tkinter as tk\n"
    "from tkinter import filedialog\n"
    "r = tk.Tk(); r.withdraw()\n"
    "r.attributes('-topmost', True)\n"
    "d = filedialog.askdirectory("
    "title='Dossier a analyser par BirdNET', parent=r)\n"
    "sys.stdout.buffer.write((d or '').encode('utf-8'))\n")

# Programmes en console (ffmpeg, ffprobe) lances SANS fenetre : depuis le
# programme a fenetre (sans console), chaque appel ferait sinon clignoter
# une fenetre noire, que NVDA annoncerait.
SANS_FENETRE = ({"creationflags": subprocess.CREATE_NO_WINDOW}
                if os.name == "nt" else {})


def outil(nom):
    """ffmpeg/ffprobe : celui LIVRE DANS LE PACK (bin/) d'abord, le PATH
    seulement en secours. Le pack ne depend donc pas d'une install systeme."""
    local = os.path.join(BIN_DIR, nom + (".exe" if os.name == "nt" else ""))
    return local if os.path.exists(local) else nom


FFMPEG = outil("ffmpeg")
FFPROBE = outil("ffprobe")


def lancer(cmd):
    """Lance ffmpeg/ffprobe sans fenetre. En cas d'echec, l'erreur porte la
    DERNIERE ligne de ffmpeg (lisible dans la page) au lieu de la commande.
    Le processus est garde dans PROC_FFMPEG pour que l'arret du programme
    puisse le tuer (sinon un ffmpeg orphelin finirait d'ecrire dans tmp/)."""
    global PROC_FFMPEG
    if ARRET.is_set():
        raise Interrompu()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, **SANS_FENETRE)
    PROC_FFMPEG = proc
    try:
        out, err = proc.communicate()
    finally:
        PROC_FFMPEG = None
    if ARRET.is_set():
        raise Interrompu()
    if proc.returncode != 0:
        lignes = err.decode("utf-8", "replace").strip().splitlines()
        raise RuntimeError("%s : %s" % (os.path.basename(cmd[0]),
                                        lignes[-1] if lignes else
                                        "code %d" % proc.returncode))
    return out


def arreter_analyse(delai=5.0):
    """Arret propre demande (bouton Quitter, croix de la fenetre) : on leve le
    drapeau, on tue un ffmpeg en cours et on laisse au plus `delai` s a
    l'analyse pour s'interrompre entre deux micros (un micro deja lance dans
    BirdNET ne s'interrompt pas, mais aucun sidecar partiel n'est ecrit : le
    fichier repart en analyse au prochain lancement). Idempotent."""
    ARRET.set()
    proc = PROC_FFMPEG
    if proc is not None:
        try:
            proc.kill()
        except OSError:
            pass
    try:
        direct.arreter()
    except Exception as e:
        log("arret du direct : %s" % e)
    fin = time.time() + delai
    while time.time() < fin:
        with verrou:
            if not etat["en_cours"]:
                return True
        time.sleep(0.1)
    return False

MIN_CONF = 0.17                 # seuil BAS : on stocke large, la page trie
OVERLAP = 1.5                   # fenetres de 3 s vues deux fois
HIGHPASS_CUTOFF = 200           # anti-vent (Butterworth ordre 10)
SR = 48000
EXTENSIONS = (".wav", ".flac", ".mp3", ".ogg", ".opus", ".m4a", ".aac",
              ".aif", ".aiff", ".wma",
              # videos : ffmpeg en extrait la piste audio avant l'analyse
              ".mp4", ".m4v", ".mov", ".mkv", ".avi", ".webm", ".wmv",
              ".mpg", ".mpeg", ".3gp", ".mts", ".m2ts")
SUFFIXE = ".birds.json"
STABILITE_S = 60                # fichier plus recent = peut-etre en cours de copie
VEILLE_S = 20                   # periode du balayage automatique
MAX_CANAUX = 16
VERSION_SIDECAR = 2
EXTRAIT_MAX = 30.0              # garde-fou : jamais plus long que ca (secondes)
MODELE = "BirdNET GLOBAL 6K V2.4"
R2 = 0.70710678
FEN_PIC, PAS_PIC = 0.10, 0.05   # detecteur de pic d'energie (recalage "t")

# Les 35 langues officielles BirdNET presentes dans ./labels/. Noms NATIFS
# (chacun reconnait sa langue quel que soit l'affichage de l'interface).
LANGUES = {
    "af": "Afrikaans", "ar": "العربية", "bg": "Български", "ca": "Català",
    "cs": "Čeština", "da": "Dansk", "de": "Deutsch", "el": "Ελληνικά",
    "en_uk": "English", "es": "Español", "fi": "Suomi", "fr": "Français",
    "he": "עברית", "hr": "Hrvatski", "hu": "Magyar",
    "in": "Bahasa Indonesia", "is": "Íslenska", "it": "Italiano",
    "ja": "日本語", "ko": "한국어", "lt": "Lietuvių", "ml": "മലയാളം",
    "nl": "Nederlands", "no": "Norsk", "pl": "Polski", "ro": "Română",
    "ru": "Русский", "sk": "Slovenčina", "sl": "Slovenščina",
    "sr": "Српски", "sv": "Svenska", "th": "ไทย", "tr": "Türkçe",
    "uk": "Українська", "zh": "中文",
}

# Pays -> (lat, lon) du centre du pays, pour le filtre d'especes BirdNET
# (le modele est mondial : il n'a pas de notion de pays, seulement un point).
# Noms en francais. Outre-mers francais listes a part (avifaune differente).
PAYS = {
    "Afghanistan": (33.9, 67.7), "Afrique du Sud": (-30.6, 22.9),
    "Albanie": (41.2, 20.2), "Algérie": (28.0, 1.7),
    "Allemagne": (51.2, 10.4), "Andorre": (42.5, 1.5),
    "Angola": (-11.2, 17.9), "Arabie saoudite": (23.9, 45.1),
    "Argentine": (-38.4, -63.6), "Arménie": (40.1, 45.0),
    "Australie": (-25.3, 133.8), "Autriche": (47.5, 14.6),
    "Azerbaïdjan": (40.1, 47.6), "Bahamas": (25.0, -77.4),
    "Bahreïn": (26.0, 50.6), "Bangladesh": (23.7, 90.4),
    "Barbade": (13.2, -59.5), "Belgique": (50.5, 4.5),
    "Belize": (17.2, -88.5), "Bénin": (9.3, 2.3),
    "Bhoutan": (27.5, 90.4), "Biélorussie": (53.7, 27.9),
    "Birmanie (Myanmar)": (21.9, 95.9), "Bolivie": (-16.3, -63.6),
    "Bosnie-Herzégovine": (43.9, 17.7), "Botswana": (-22.3, 24.7),
    "Brésil": (-14.2, -51.9), "Brunei": (4.5, 114.7),
    "Bulgarie": (42.7, 25.5), "Burkina Faso": (12.2, -1.6),
    "Burundi": (-3.4, 29.9), "Cambodge": (12.6, 105.0),
    "Cameroun": (7.4, 12.4), "Canada": (56.1, -106.3),
    "Cap-Vert": (16.0, -24.0), "Chili": (-35.7, -71.5),
    "Chine": (35.9, 104.2), "Chypre": (35.1, 33.4),
    "Colombie": (4.6, -74.3), "Comores": (-11.6, 43.3),
    "Congo (Brazzaville)": (-0.2, 15.8), "Congo (RDC)": (-4.0, 21.8),
    "Corée du Nord": (40.3, 127.5), "Corée du Sud": (35.9, 127.8),
    "Costa Rica": (9.7, -83.8), "Côte d'Ivoire": (7.5, -5.5),
    "Croatie": (45.1, 15.2), "Cuba": (21.5, -77.8),
    "Danemark": (56.3, 9.5), "Djibouti": (11.8, 42.6),
    "Égypte": (26.8, 30.8), "Émirats arabes unis": (23.4, 53.8),
    "Équateur": (-1.8, -78.2), "Érythrée": (15.2, 39.8),
    "Espagne": (40.5, -3.7), "Estonie": (58.6, 25.0),
    "Eswatini": (-26.5, 31.5), "États-Unis": (37.1, -95.7),
    "Éthiopie": (9.1, 40.5), "Fidji": (-17.7, 178.0),
    "Finlande": (61.9, 25.7), "France": (46.2, 2.2),
    "Gabon": (-0.8, 11.6), "Gambie": (13.4, -15.3),
    "Géorgie": (42.3, 43.4), "Ghana": (7.9, -1.0),
    "Grèce": (39.1, 21.8), "Groenland": (71.7, -42.6),
    "Guadeloupe": (16.2, -61.6), "Guatemala": (15.8, -90.2),
    "Guinée": (9.9, -9.7), "Guinée équatoriale": (1.7, 10.3),
    "Guinée-Bissau": (11.8, -15.2), "Guyana": (4.9, -58.9),
    "Guyane française": (4.0, -53.0), "Haïti": (19.0, -72.3),
    "Honduras": (15.2, -86.2), "Hongrie": (47.2, 19.5),
    "Inde": (20.6, 79.0), "Indonésie": (-0.8, 113.9),
    "Irak": (33.2, 43.7), "Iran": (32.4, 53.7),
    "Irlande": (53.4, -8.2), "Islande": (64.9, -19.0),
    "Israël": (31.0, 34.9), "Italie": (41.9, 12.6),
    "Jamaïque": (18.1, -77.3), "Japon": (36.2, 138.3),
    "Jordanie": (30.6, 36.2), "Kazakhstan": (48.0, 66.9),
    "Kenya": (0.0, 37.9), "Kirghizistan": (41.2, 74.8),
    "Kiribati": (1.9, -157.4), "Koweït": (29.3, 47.5),
    "La Réunion": (-21.1, 55.5), "Laos": (19.9, 102.5),
    "Lesotho": (-29.6, 28.2), "Lettonie": (56.9, 24.6),
    "Liban": (33.9, 35.9), "Liberia": (6.4, -9.4),
    "Libye": (26.3, 17.2), "Liechtenstein": (47.2, 9.6),
    "Lituanie": (55.2, 23.9), "Luxembourg": (49.8, 6.1),
    "Macédoine du Nord": (41.6, 21.7), "Madagascar": (-18.8, 47.0),
    "Malaisie": (4.2, 102.0), "Malawi": (-13.3, 34.3),
    "Maldives": (3.2, 73.2), "Mali": (17.6, -4.0),
    "Malte": (35.9, 14.4), "Maroc": (31.8, -7.1),
    "Martinique": (14.6, -61.0), "Maurice": (-20.3, 57.6),
    "Mauritanie": (21.0, -10.9), "Mayotte": (-12.8, 45.2),
    "Mexique": (23.6, -102.6), "Micronésie": (7.4, 150.6),
    "Moldavie": (47.4, 28.4), "Monaco": (43.7, 7.4),
    "Mongolie": (46.9, 103.8), "Monténégro": (42.7, 19.4),
    "Mozambique": (-18.7, 35.5), "Namibie": (-22.9, 18.5),
    "Nauru": (-0.5, 166.9), "Népal": (28.4, 84.1),
    "Nicaragua": (12.9, -85.2), "Niger": (17.6, 8.1),
    "Nigeria": (9.1, 8.7), "Norvège": (64.6, 12.7),
    "Nouvelle-Calédonie": (-21.3, 165.6),
    "Nouvelle-Zélande": (-40.9, 174.9), "Oman": (21.5, 55.9),
    "Ouganda": (1.4, 32.3), "Ouzbékistan": (41.4, 64.6),
    "Pakistan": (30.4, 69.3), "Palaos": (7.5, 134.6),
    "Panama": (8.5, -80.8),
    "Papouasie-Nouvelle-Guinée": (-6.3, 143.9),
    "Paraguay": (-23.4, -58.4), "Pays-Bas": (52.1, 5.3),
    "Pérou": (-9.2, -75.0), "Philippines": (12.9, 121.8),
    "Pologne": (51.9, 19.1),
    "Polynésie française": (-17.7, -149.4), "Portugal": (39.4, -8.2),
    "Qatar": (25.4, 51.2), "Roumanie": (45.9, 25.0),
    "Royaume-Uni": (55.4, -3.4), "Russie": (61.5, 105.3),
    "Rwanda": (-1.9, 29.9), "Saint-Marin": (43.9, 12.5),
    "Saint-Pierre-et-Miquelon": (46.9, -56.3),
    "Salvador": (13.8, -88.9), "Samoa": (-13.8, -172.1),
    "Sao Tomé-et-Principe": (0.2, 6.6), "Sénégal": (14.5, -14.5),
    "Serbie": (44.0, 21.0), "Seychelles": (-4.7, 55.5),
    "Sierra Leone": (8.5, -11.8), "Singapour": (1.4, 103.8),
    "Slovaquie": (48.7, 19.7), "Slovénie": (46.2, 15.0),
    "Somalie": (5.2, 46.2), "Soudan": (12.9, 30.2),
    "Soudan du Sud": (7.9, 30.0), "Sri Lanka": (7.9, 80.8),
    "Suède": (60.1, 18.6), "Suisse": (46.8, 8.2),
    "Suriname": (3.9, -56.0), "Syrie": (34.8, 39.0),
    "Tadjikistan": (38.9, 71.3), "Tanzanie": (-6.4, 34.9),
    "Tchad": (15.5, 18.7), "Tchéquie": (49.8, 15.5),
    "Thaïlande": (15.9, 101.0), "Timor oriental": (-8.9, 125.7),
    "Togo": (8.6, 0.8), "Tonga": (-21.2, -175.2),
    "Trinité-et-Tobago": (10.7, -61.2), "Tunisie": (33.9, 9.5),
    "Turkménistan": (39.0, 59.6), "Turquie": (39.0, 35.2),
    "Tuvalu": (-7.1, 177.6), "Ukraine": (48.4, 31.2),
    "Uruguay": (-32.5, -55.8), "Vanuatu": (-15.4, 166.9),
    "Venezuela": (6.4, -66.6), "Vietnam": (14.1, 108.3),
    "Yémen": (15.6, 48.5), "Zambie": (-13.1, 27.8),
    "Zimbabwe": (-19.0, 29.2),
}

# Code ISO 3166-1 de chaque pays : la page traduit le nom dans la langue de
# l'appareil via Intl.DisplayNames (retour utilisateur 2026-09 : un
# anglophone cherchait "United Kingdom" et ne trouvait que "Royaume-Uni").
# Les outre-mers francais ont TOUS leur propre code ISO (GP, RE, YT...).
# Le nom francais reste la cle de PAYS et la valeur persistee dans la
# config (compatibilite) ; il sert aussi de secours si le navigateur ne
# sait pas traduire un code.
PAYS_CODE = {
    "Afghanistan": "AF", "Afrique du Sud": "ZA", "Albanie": "AL",
    "Algérie": "DZ", "Allemagne": "DE", "Andorre": "AD", "Angola": "AO",
    "Arabie saoudite": "SA", "Argentine": "AR", "Arménie": "AM",
    "Australie": "AU", "Autriche": "AT", "Azerbaïdjan": "AZ",
    "Bahamas": "BS", "Bahreïn": "BH", "Bangladesh": "BD", "Barbade": "BB",
    "Belgique": "BE", "Belize": "BZ", "Bénin": "BJ", "Bhoutan": "BT",
    "Biélorussie": "BY", "Birmanie (Myanmar)": "MM", "Bolivie": "BO",
    "Bosnie-Herzégovine": "BA", "Botswana": "BW", "Brésil": "BR",
    "Brunei": "BN", "Bulgarie": "BG", "Burkina Faso": "BF", "Burundi": "BI",
    "Cambodge": "KH", "Cameroun": "CM", "Canada": "CA", "Cap-Vert": "CV",
    "Chili": "CL", "Chine": "CN", "Chypre": "CY", "Colombie": "CO",
    "Comores": "KM", "Congo (Brazzaville)": "CG", "Congo (RDC)": "CD",
    "Corée du Nord": "KP", "Corée du Sud": "KR", "Costa Rica": "CR",
    "Côte d'Ivoire": "CI", "Croatie": "HR", "Cuba": "CU", "Danemark": "DK",
    "Djibouti": "DJ", "Égypte": "EG", "Émirats arabes unis": "AE",
    "Équateur": "EC", "Érythrée": "ER", "Espagne": "ES", "Estonie": "EE",
    "Eswatini": "SZ", "États-Unis": "US", "Éthiopie": "ET", "Fidji": "FJ",
    "Finlande": "FI", "France": "FR", "Gabon": "GA", "Gambie": "GM",
    "Géorgie": "GE", "Ghana": "GH", "Grèce": "GR", "Groenland": "GL",
    "Guadeloupe": "GP", "Guatemala": "GT", "Guinée": "GN",
    "Guinée équatoriale": "GQ", "Guinée-Bissau": "GW", "Guyana": "GY",
    "Guyane française": "GF", "Haïti": "HT", "Honduras": "HN",
    "Hongrie": "HU", "Inde": "IN", "Indonésie": "ID", "Irak": "IQ",
    "Iran": "IR", "Irlande": "IE", "Islande": "IS", "Israël": "IL",
    "Italie": "IT", "Jamaïque": "JM", "Japon": "JP", "Jordanie": "JO",
    "Kazakhstan": "KZ", "Kenya": "KE", "Kirghizistan": "KG",
    "Kiribati": "KI", "Koweït": "KW", "La Réunion": "RE", "Laos": "LA",
    "Lesotho": "LS", "Lettonie": "LV", "Liban": "LB", "Liberia": "LR",
    "Libye": "LY", "Liechtenstein": "LI", "Lituanie": "LT",
    "Luxembourg": "LU", "Macédoine du Nord": "MK", "Madagascar": "MG",
    "Malaisie": "MY", "Malawi": "MW", "Maldives": "MV", "Mali": "ML",
    "Malte": "MT", "Maroc": "MA", "Martinique": "MQ", "Maurice": "MU",
    "Mauritanie": "MR", "Mayotte": "YT", "Mexique": "MX",
    "Micronésie": "FM", "Moldavie": "MD", "Monaco": "MC", "Mongolie": "MN",
    "Monténégro": "ME", "Mozambique": "MZ", "Namibie": "NA", "Nauru": "NR",
    "Népal": "NP", "Nicaragua": "NI", "Niger": "NE", "Nigeria": "NG",
    "Norvège": "NO", "Nouvelle-Calédonie": "NC", "Nouvelle-Zélande": "NZ",
    "Oman": "OM", "Ouganda": "UG", "Ouzbékistan": "UZ", "Pakistan": "PK",
    "Palaos": "PW", "Panama": "PA", "Papouasie-Nouvelle-Guinée": "PG",
    "Paraguay": "PY", "Pays-Bas": "NL", "Pérou": "PE", "Philippines": "PH",
    "Pologne": "PL", "Polynésie française": "PF", "Portugal": "PT",
    "Qatar": "QA", "Roumanie": "RO", "Royaume-Uni": "GB", "Russie": "RU",
    "Rwanda": "RW", "Saint-Marin": "SM", "Saint-Pierre-et-Miquelon": "PM",
    "Salvador": "SV", "Samoa": "WS", "Sao Tomé-et-Principe": "ST",
    "Sénégal": "SN", "Serbie": "RS", "Seychelles": "SC",
    "Sierra Leone": "SL", "Singapour": "SG", "Slovaquie": "SK",
    "Slovénie": "SI", "Somalie": "SO", "Soudan": "SD",
    "Soudan du Sud": "SS", "Sri Lanka": "LK", "Suède": "SE", "Suisse": "CH",
    "Suriname": "SR", "Syrie": "SY", "Tadjikistan": "TJ", "Tanzanie": "TZ",
    "Tchad": "TD", "Tchéquie": "CZ", "Thaïlande": "TH",
    "Timor oriental": "TL", "Togo": "TG", "Tonga": "TO",
    "Trinité-et-Tobago": "TT", "Tunisie": "TN", "Turkménistan": "TM",
    "Turquie": "TR", "Tuvalu": "TV", "Ukraine": "UA", "Uruguay": "UY",
    "Vanuatu": "VU", "Venezuela": "VE", "Vietnam": "VN", "Yémen": "YE",
    "Zambie": "ZM", "Zimbabwe": "ZW",
}

# --- Reglages persistants (lieu, saison, dossiers supplementaires) ---
# Ils s'appliquent a TOUS les fichiers ; la page les modifie via /api/config.
CONFIG_PATH = os.path.join(DONNEES, "birdnet_config.json")
CONFIG_DEFAUT = {
    "pays": "France",
    "lat": 46.2, "lon": 2.2,       # centre de la France = PAYS["France"]
    "date_mode": "auto",           # "auto" = date de chaque fichier
    "jour": 15, "mois": 6,         # utilises si date_mode = "fixe"
    "dossiers": [],                # dossiers surveilles EN PLUS de audio/
    "direct_entree": "",           # entree audio du direct (identifiant dshow)
    "direct_entree_nom": "",       # son nom lisible
    "direct_fichier": False,       # ecrire les especes entendues dans un fichier
}
config = dict(CONFIG_DEFAUT)


def charger_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            j = json.load(f)
        for cle in CONFIG_DEFAUT:
            if cle in j:
                config[cle] = j[cle]
        # Un dossier ABSENT au demarrage (disque externe debranche, reseau
        # coupe) reste surveille : l'oublier ici ferait prendre ses analyses
        # pour celles d'un dossier retire, et ranger() les supprimerait.
        # La page le signale "introuvable" ; le retirer est un geste de
        # l'utilisateur (bouton "Ne plus surveiller").
        config["dossiers"] = [d for d in config["dossiers"]
                              if isinstance(d, str) and d.strip()]
    except (OSError, ValueError):
        pass


def sauver_config():
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=1)
    os.replace(tmp, CONFIG_PATH)


# --- Etat partage (worker d'analyse / serveur HTTP) ---
verrou = threading.Lock()
picker_verrou = threading.Lock()    # une seule boite "choisir un dossier"
file_attente = deque()          # relpaths a analyser (avec doublons evites)
etat = {"en_cours": None, "etape": "", "depuis": 0.0, "faits": 0}
erreurs = {}                    # rel -> (mtime, message) : pas de re-essai en boucle
journal = deque(maxlen=40)


def log(msg):
    ligne = time.strftime("%H:%M:%S ") + msg
    print(ligne, flush=True)
    with verrou:
        journal.append(ligne)


# --- Utilitaires fichiers ---
# Le dossier audio/ du projet est TOUJOURS surveille ; l'utilisateur peut
# ajouter d'autres dossiers (config["dossiers"]). Les fichiers de audio/
# gardent un chemin relatif (compatibilite), ceux des dossiers ajoutes un
# chemin ABSOLU a barres obliques.
def racines():
    with verrou:
        return [AUDIO_DIR] + list(config["dossiers"])


def dans(chemin, racine):
    racine = os.path.realpath(racine)
    return chemin == racine or chemin.startswith(racine + os.sep)


def rel_de(chemin):
    p = os.path.realpath(chemin)
    if dans(p, AUDIO_DIR):
        return os.path.relpath(p, AUDIO_DIR).replace("\\", "/")
    return p.replace("\\", "/")


def chemin_sur(rel):
    """Chemin absolu d'un identifiant client, verrouille dans un des dossiers
    surveilles (anti-traversal)."""
    r = rel.replace("\\", "/")
    if re.match(r"^([A-Za-z]:/|//)", r):        # chemin absolu Windows/UNC
        p = os.path.realpath(r)
        for d in racines():
            if d != AUDIO_DIR and dans(p, d):
                return p
        raise ValueError("chemin hors des dossiers surveilles")
    p = os.path.realpath(os.path.join(AUDIO_DIR, r))
    if not dans(p, AUDIO_DIR):
        raise ValueError("chemin hors du dossier audio")
    return p


def slug_dossier(racine):
    """Nom du sous-dossier d'analyses d'un dossier ajoute : son nom + une
    empreinte du chemin complet (deux dossiers "mp3" ne se melangent pas)."""
    p = os.path.realpath(racine)
    nom = re.sub(r"[^A-Za-z0-9._-]+", "_", os.path.basename(p.rstrip("\\/"))
                 or "dossier").strip("_") or "dossier"
    emp = hashlib.sha1(os.path.normcase(p).encode("utf-8")).hexdigest()[:8]
    return "%s_%s" % (nom[:40], emp)


def dossier_analyses(racine, creer=False):
    """Sous-dossier de analyses/ qui recoit les resultats d'un dossier
    surveille. Pour un dossier ajoute, dossier.txt garde son chemin complet
    (lisible, et c'est lui qui sert au rangement du demarrage)."""
    if os.path.realpath(racine) == os.path.realpath(AUDIO_DIR):
        d = os.path.join(ANALYSES_DIR, "audio")
    else:
        d = os.path.join(ANALYSES_DIR, slug_dossier(racine))
    if creer and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
        if not d.endswith(os.sep + "audio"):
            with open(os.path.join(d, "dossier.txt"), "w",
                      encoding="utf-8") as f:
                f.write(os.path.realpath(racine) + "\n")
    return d


def racine_de(chemin):
    """(dossier surveille, chemin relatif dedans) d'un fichier audio, dans
    l'ordre de racines() — le meme que lister_audio() pour les recoupements.
    (None, None) si le fichier n'est sous aucun dossier surveille."""
    p = os.path.realpath(chemin)
    for racine in racines():
        if dans(p, racine):
            return racine, os.path.relpath(p, os.path.realpath(racine))
    return None, None


def sidecar_de(chemin, creer=False):
    """Chemin du .birds.json d'un fichier audio (dans analyses/), ou None si
    le fichier n'est plus sous un dossier surveille."""
    racine, rel = racine_de(chemin)
    if racine is None:
        return None
    sc = os.path.join(dossier_analyses(racine, creer), rel + SUFFIXE)
    if creer:
        os.makedirs(os.path.dirname(sc), exist_ok=True)
    return sc


def lister_audio():
    """Tous les fichiers audio des dossiers surveilles, SOUS-DOSSIERS
    COMPRIS (doublons ecartes si un dossier ajoute recoupe audio/)."""
    trouves, vus = [], set()
    for racine in racines():
        for base, dossiers, fichiers in os.walk(racine):
            dossiers[:] = [d for d in dossiers if not d.startswith(".")]
            for f in fichiers:
                if f.lower().endswith(EXTENSIONS) and not f.startswith("."):
                    p = os.path.realpath(os.path.join(base, f))
                    if p not in vus:
                        vus.add(p)
                        trouves.append(p)
    return sorted(trouves)


def a_faire(chemin, force=False):
    if force:
        return True
    sc = sidecar_de(chemin)
    if not sc or not os.path.exists(sc):
        return True
    try:
        return os.path.getmtime(sc) < os.path.getmtime(chemin)
    except OSError:
        return True


def supprimer_vides(dossier):
    """Retire les sous-dossiers vides (apres suppression d'analyses)."""
    for base, dossiers, fichiers in os.walk(dossier, topdown=False):
        if base != dossier and not os.listdir(base):
            try:
                os.rmdir(base)
            except OSError:
                pass


def nettoyer():
    """Analyses orphelines (audio supprime) -> retirees, dans analyses/ de
    chaque dossier surveille. Ne touche JAMAIS l'audio. Un dossier surveille
    INTROUVABLE (disque externe debranche, reseau coupe) est laisse tel quel :
    ses analyses attendent son retour."""
    for racine in racines():
        if not os.path.isdir(racine):
            continue
        d = dossier_analyses(racine)
        if not os.path.isdir(d):
            continue
        for base, dossiers, fichiers in os.walk(d):
            for f in fichiers:
                if not f.endswith(SUFFIXE):
                    continue
                sc = os.path.join(base, f)
                rel = os.path.relpath(sc, d)[:-len(SUFFIXE)]
                if not os.path.exists(os.path.join(racine, rel)):
                    try:
                        os.remove(sc)
                        log("nettoyage : analyse de %s retiree (audio "
                            "supprime)" % rel.replace("\\", "/"))
                    except OSError as e:
                        log("nettoyage impossible : %s" % e)
        supprimer_vides(d)


def ranger():
    """Rangement au demarrage (2026-11-24, demande de l'auteur) :
    1. les anciens .birds.json poses A COTE de l'audio (versions precedentes)
       sont deplaces dans analyses/ — ou supprimes si leur audio n'existe
       plus ;
    2. le sous-dossier d'analyses d'un dossier qui n'est PLUS surveille
       (retire dans la page) est supprime ;
    3. un dossier surveille introuvable sur le disque est garde (disque
       debranche ?) et signale dans le journal ;
    4. nettoyer() : analyses dont l'audio a disparu."""
    os.makedirs(ANALYSES_DIR, exist_ok=True)
    deplaces = supprimes = 0
    for racine in racines():
        if not os.path.isdir(racine):
            log("dossier surveille introuvable (disque debranche ?) : %s — "
                "ses analyses sont conservees" % racine)
            continue
        for base, dossiers, fichiers in os.walk(racine):
            dossiers[:] = [x for x in dossiers if not x.startswith(".")]
            for f in fichiers:
                if not f.endswith(SUFFIXE):
                    continue
                ancien = os.path.join(base, f)
                audio = ancien[:-len(SUFFIXE)]
                try:
                    if not os.path.exists(audio):
                        os.remove(ancien)
                        supprimes += 1
                        continue
                    nouveau = sidecar_de(audio, creer=True)
                    if nouveau is None:
                        continue
                    if (not os.path.exists(nouveau) or
                            os.path.getmtime(nouveau) <
                            os.path.getmtime(ancien)):
                        os.replace(ancien, nouveau)
                    else:
                        os.remove(ancien)
                    deplaces += 1
                except OSError as e:
                    log("rangement impossible pour %s : %s" % (ancien, e))
    if deplaces or supprimes:
        log("rangement : %d resultat(s) deplace(s) dans %s, %d orphelin(s) "
            "supprime(s)" % (deplaces, ANALYSES_DIR, supprimes))
    attendus = {os.path.basename(dossier_analyses(r)) for r in racines()}
    for nom in os.listdir(ANALYSES_DIR):
        d = os.path.join(ANALYSES_DIR, nom)
        if nom in attendus or not os.path.isdir(d):
            continue
        origine = ""
        try:
            with open(os.path.join(d, "dossier.txt"), encoding="utf-8") as f:
                origine = f.read().strip()
        except OSError:
            pass
        try:
            shutil.rmtree(d)
            log("rangement : analyses de %s supprimees (dossier plus "
                "surveille)" % (origine or nom))
        except OSError as e:
            log("rangement impossible pour %s : %s" % (d, e))
    nettoyer()


def mettre_en_file(rel):
    with verrou:
        if rel != etat["en_cours"] and rel not in file_attente:
            file_attente.append(rel)


def balayer(force=False):
    """Un tour de veille : nouveaux fichiers / remplaces -> file d'attente,
    orphelins -> retires, fichiers trop recents -> tour suivant."""
    nettoyer()
    maintenant = time.time()
    for chemin in lister_audio():
        rel = rel_de(chemin)
        try:
            mtime = os.path.getmtime(chemin)
        except OSError:
            continue
        if maintenant - mtime < STABILITE_S:
            continue                      # copie peut-etre en cours
        if not force and rel in erreurs and erreurs[rel][0] == mtime:
            continue                      # deja echoue sur cette version
        if a_faire(chemin, force):
            mettre_en_file(rel)


# --- Sonde ffprobe : canaux, duree, date de prise ---
def sonde(chemin):
    sortie = lancer(
        [FFPROBE, "-v", "error", "-print_format", "json", "-show_streams",
         "-show_format", chemin]).decode("utf-8", "replace")
    j = json.loads(sortie)
    canaux, duree = 0, 0.0
    for s in j.get("streams", []):
        if s.get("codec_type") == "audio":
            canaux = int(s.get("channels", 0))
            break
    fmt = j.get("format", {}) or {}
    try:
        duree = float(fmt.get("duration", 0) or 0)
    except ValueError:
        duree = 0.0
    date_prise = None
    for cle, val in (fmt.get("tags", {}) or {}).items():
        if cle.lower() == "date" and val:
            v = str(val).strip().replace("/", "-")[:10]
            for f in ("%Y-%m-%d", "%Y-%m", "%Y"):
                try:
                    date_prise = datetime.strptime(v, f)
                    break
                except ValueError:
                    pass
        if date_prise:
            break
    if date_prise is None:
        date_prise = datetime.fromtimestamp(os.path.getmtime(chemin))
    return canaux, duree, date_prise


# --- Micros virtuels selon le nombre de canaux ---
def construire_beams(donnees, canaux):
    """-> liste de (nom, signal, azimut_ou_None). Azimut : 0 = devant,
    + = gauche (convention du VPS)."""
    if canaux == 1:
        return [("mono", donnees[:, 0], None)]
    if canaux == 2:
        L, R = donnees[:, 0], donnees[:, 1]
        return [("gauche", L, 45.0), ("droite", R, -45.0),
                ("cote", (L - R) * 0.5, None)]
    if canaux == 4:                       # suppose AmbiX ACN : W Y Z X
        W, Y, Z, X = (donnees[:, 0], donnees[:, 1],
                      donnees[:, 2], donnees[:, 3])
        quadrants = [("avant-gauche", R2, R2, 45.0),
                     ("avant-droite", R2, -R2, -45.0),
                     ("arriere-gauche", -R2, R2, 135.0),
                     ("arriere-droite", -R2, -R2, -135.0)]
        beams = [("W", 0.5 * W, None)]
        for nom, kx, ky, az in quadrants:
            beams.append((nom, 0.5 * (W + kx * X + ky * Y), az))
        beams.append(("haut", 0.5 * (W + Z), None))
        return beams
    n = min(canaux, MAX_CANAUX)
    return [("canal %d" % (i + 1), donnees[:, i], None) for i in range(n)]


def position(confs, azimuts):
    """Code de position (C/CG/G/CD/D, H en secours) a partir des confiances
    par beam. Somme vectorielle ponderee, puis angle RABATTU sur l'axe
    gauche-droite : avant et arriere ne sont PAS distingues (retour
    utilisateur 2026-09 : "front"/"rear" semait la confusion, seul le
    lateral parle a tout le monde). La page traduit encore les anciens
    codes 8 secteurs des sidecars deja ecrits."""
    horiz = {n: c for n, c in confs.items() if azimuts.get(n) is not None}
    if not horiz:
        return "H" if confs.get("haut", 0) > 0 else None
    vx = sum(c * np.cos(np.radians(azimuts[n])) for n, c in horiz.items())
    vy = sum(c * np.sin(np.radians(azimuts[n])) for n, c in horiz.items())
    if vx == 0 and vy == 0:
        return None
    az = np.degrees(np.arctan2(vy, vx))     # 0 = devant, + = gauche
    if abs(az) > 90:                        # arriere -> rabattu vers l'avant
        az = np.sign(az) * (180 - abs(az))
    # Bornes choisies pour la stereo (beams a +/-45) : un oiseau entendu
    # dans un seul canal tombe franchement en "gauche"/"droite".
    for borne, code in [(15.0, "C"), (40.0, "CG"), (90.1, "G")]:
        if abs(az) <= borne:
            if code == "C":
                return code
            return code if az > 0 else code.replace("G", "D")
    return None


def pic_energie(y, debut, fin):
    """Moment du pic d'energie dans [debut, fin] (s) du signal filtre."""
    i0, i1 = int(debut * SR), min(int(fin * SR), len(y))
    n, pas = int(FEN_PIC * SR), int(PAS_PIC * SR)
    if i1 - i0 < n:
        return debut
    seg = y[i0:i1]
    meilleur, t_pic = -1.0, debut
    for k in range(0, len(seg) - n + 1, pas):
        e = float(np.mean(seg[k:k + n] ** 2))
        if e > meilleur:
            meilleur, t_pic = e, debut + k / SR
    return t_pic


# --- Extraits audio (ecoute du chant, pas analyse) ---
def filtre_ecoute(canaux):
    """Chaine ffmpeg pour rendre l'extrait ECOUTABLE. Ici PAS de passe-haut :
    il sert a l'analyse, pas a l'oreille. L'AmbiX 4 canaux est decode en
    stereo (gauche = W+Y, droite = W-Y), sinon on entend du B-format brut."""
    if canaux == 4:
        return ("channelmap=0|1|2|3:quad,"
                "pan=stereo|c0=0.707*c0+0.707*c1|c1=0.707*c0-0.707*c1")
    if canaux > 2:
        return "pan=stereo|c0=c0|c1=c1"    # multicanal quelconque : 2 premiers
    return None


def canaux_de(chemin):
    """Nombre de canaux : depuis le sidecar (gratuit), ffprobe en secours."""
    try:
        with open(sidecar_de(chemin) or "", encoding="utf-8") as f:
            n = int(json.load(f).get("canaux", 0))
        if n > 0:
            return n
    except (OSError, ValueError, TypeError):
        pass
    return sonde(chemin)[0]


def purge_tmp():
    """Vide tmp/ des WAV de travail au demarrage. En marche normale chaque
    WAV est efface dans un finally, mais une fenetre fermee (ou une coupure)
    PENDANT une analyse laisse src_<pid>.wav / beam_<pid>.wav sur le disque :
    decodage complet en 32 bits flottants, soit ~700 Mo par heure d'audio et
    par canal. Le garde-fou "deja lancE" garantit qu'aucun autre serveur ne
    tourne : rien de vivant ici. On ne touche que les .wav (srv.log reste)."""
    efface = 0
    for nom in os.listdir(TMP_DIR):
        if not nom.lower().endswith(".wav"):
            continue
        try:
            os.remove(os.path.join(TMP_DIR, nom))
            efface += 1
        except OSError:
            pass
    if efface:
        print("tmp/ : %d fichier(s) de travail efface(s)." % efface)


def extraire(chemin, debut, duree):
    """Decoupe [debut, debut+duree] et renvoie un WAV 16 bits (octets)."""
    sortie = os.path.join(TMP_DIR, "extrait_%d_%d.wav"
                          % (os.getpid(), threading.get_ident()))
    cmd = [FFMPEG, "-y", "-loglevel", "error",
           "-ss", "%.3f" % debut, "-t", "%.3f" % duree, "-i", chemin, "-vn"]
    af = filtre_ecoute(canaux_de(chemin))
    if af:
        cmd += ["-af", af]
    cmd += ["-ar", str(SR), "-c:a", "pcm_s16le", sortie]
    try:
        lancer(cmd)
        with open(sortie, "rb") as f:
            return f.read()
    finally:
        if os.path.exists(sortie):
            try:
                os.remove(sortie)
            except OSError:
                pass


# --- Lecture locale des extraits (par le SERVEUR, pas le navigateur) ---
# Le navigateur soumet chaque lecture a sa politique "autoplay" : le clic
# simule par NVDA en mode navigation n'est pas un geste utilisateur, et en
# mode navigation NVDA consomme fleches et lettres -> aucune VRAIE touche
# n'atteint le navigateur, meme le contexte Web Audio restait suspendu tant
# qu'on n'avait pas fait Tab. Serveur et navigateur etant sur la MEME
# machine, on joue l'extrait ICI : aucune politique navigateur, la barre
# d'espace marche toujours. La page garde Web Audio en secours (acces
# distant, ou serveur non Windows).
# Lecteur = MCI (winmm, le lecteur WAV de Windows) et plus winsound, qui ne
# sait pas faire PAUSE (2026-11-24, demande de l'auteur : Ctrl+Espace =
# pause/reprise, Maj+Espace = arret, plus de bouton Arreter). PIEGE MESURE :
# un peripherique MCI appartient au THREAD qui l'a ouvert (erreur 263 depuis
# un autre thread, et chaque requete HTTP a le sien) -> un thread lecteur
# unique execute toutes les commandes, les requetes lui passent par une file.
verrou_lecture = threading.Lock()
lecture_idx = 0


def mci(cmd):
    tampon = ctypes.create_unicode_buffer(256)
    err = ctypes.windll.winmm.mciSendStringW(cmd, tampon, 255, 0)
    if err:
        msg = ctypes.create_unicode_buffer(256)
        ctypes.windll.winmm.mciGetErrorStringW(err, msg, 255)
        raise RuntimeError("MCI %d : %s" % (err, msg.value))
    return tampon.value


class Lecteur:
    """Thread proprietaire du peripherique MCI ; exec() y fait tourner une
    fonction et attend son resultat (au plus 5 s)."""
    def __init__(self):
        self.file = queue.Queue()
        threading.Thread(target=self.boucle, daemon=True).start()

    def boucle(self):
        while True:
            fn, rep, fait = self.file.get()
            try:
                rep.append(fn())
            except Exception as e:
                rep.append(e)
            fait.set()

    def exec(self, fn):
        rep, fait = [], threading.Event()
        self.file.put((fn, rep, fait))
        fait.wait(5)
        r = rep[0] if rep else None
        if isinstance(r, Exception):
            raise r
        return r

    @staticmethod
    def mode():
        try:
            return mci("status lecture mode")     # playing / paused / stopped
        except RuntimeError:
            return "ferme"

    def jouer(self, fichier):
        def go():
            try:
                mci("close lecture")
            except RuntimeError:
                pass
            mci('open "%s" type waveaudio alias lecture' % fichier)
            mci("play lecture")
        self.exec(go)

    def pause(self):
        """Bascule pause / reprise. -> "pause", "reprise" ou "rien"."""
        def go():
            m = self.mode()
            if m == "playing":
                mci("pause lecture")
                return "pause"
            if m == "paused":
                mci("resume lecture")
                return "reprise"
            return "rien"
        return self.exec(go)

    def arreter(self):
        def go():
            try:
                mci("stop lecture")
                mci("close lecture")
            except RuntimeError:
                pass
        self.exec(go)


lecteur = None


def le_lecteur():
    global lecteur
    if lecteur is None:
        ctypes.windll.winmm        # Windows seulement : sinon AttributeError
        lecteur = Lecteur()      # -> 500, la page bascule sur Web Audio
    return lecteur


def jouer_local(chemin, debut, duree):
    global lecture_idx
    donnees = extraire(chemin, debut, duree)
    with verrou_lecture:
        # Deux fichiers en alternance : MCI tient ouvert le WAV en cours de
        # lecture, on ne reecrit pas celui-la.
        lecture_idx ^= 1
        sortie = os.path.join(TMP_DIR, "lecture_%d.wav" % lecture_idx)
        le_lecteur().arreter()
        with open(sortie, "wb") as f:
            f.write(donnees)
        le_lecteur().jouer(sortie)


def pause_local():
    with verrou_lecture:
        return le_lecteur().pause()


def arreter_local():
    with verrou_lecture:
        le_lecteur().arreter()


def charger_labels(code):
    """Table sci -> nom commun pour une langue (fichier officiel BirdNET)."""
    table = {}
    chemin = os.path.join(LABELS_DIR, "labels_%s.txt" % code)
    try:
        with open(chemin, encoding="utf-8") as f:
            for ligne in f:
                ligne = ligne.strip()
                if "_" in ligne:
                    sci, commun = ligne.split("_", 1)
                    table[sci] = commun
    except OSError:
        pass
    return table


def charger_birdnetlib():
    """-> (Analyzer, RecordingBuffer). birdnetlib importe EN TETE librosa
    (qui tire numba, llvmlite, scikit-learn) et matplotlib, alors qu'ils ne
    servent qu'a SES fonctions de lecture de fichier et de spectrogramme.
    Ici l'audio arrive deja decode par ffmpeg (RecordingBuffer) : des
    modules vides suffisent. Gain dans le programme portable (tailles
    des paquets) : ~230 Mo de bibliotheques en moins, et plusieurs
    secondes de chargement.
    Le vrai module reste utilise s'il a deja ete importe ailleurs."""
    import types
    for nom in ("librosa", "matplotlib", "matplotlib.pyplot"):
        if nom not in sys.modules:
            module = types.ModuleType(nom)
            module.__path__ = []            # importable comme un paquet
            sys.modules[nom] = module
    sys.modules["matplotlib"].pyplot = sys.modules["matplotlib.pyplot"]
    from birdnetlib import RecordingBuffer
    from birdnetlib.analyzer import Analyzer
    return Analyzer, RecordingBuffer


# --- Analyse d'un fichier ---
def analyser_fichier(analyzer, labels_fr, rel):
    chemin = chemin_sur(rel)
    t0 = time.time()
    canaux, _, date_prise = sonde(chemin)
    if canaux < 1:
        raise ValueError("aucun canal audio")

    # Lieu et saison : les reglages de la page (communs a TOUS les fichiers).
    # L'annee du filtre saisonnier n'a aucun effet (BirdNET raisonne en
    # semaine de l'annee) : 2024 arbitraire.
    with verrou:
        lat, lon = float(config["lat"]), float(config["lon"])
        if config["date_mode"] == "fixe":
            date_filtre = datetime(2024, int(config["mois"]),
                                   int(config["jour"]))
        else:
            date_filtre = date_prise

    wav_src = os.path.join(TMP_DIR, "src_%d.wav" % os.getpid())
    try:
        # Decodage complet 48 kHz. channelmap sur 4 canaux : l'Opus ambisonique
        # (mapping_family 2) est etiquete "ambisonic 1", layout que le
        # reechantillonneur refuse -> on le RENOMME sans conversion (piege
        # documente dans le projet vps ambisonique).
        cmd = [FFMPEG, "-y", "-loglevel", "error", "-i", chemin, "-vn"]
        if canaux == 4:
            cmd += ["-af", "channelmap=0|1|2|3:quad"]
        cmd += ["-ar", str(SR), "-c:a", "pcm_f32le", wav_src]
        lancer(cmd)
        brut, sr = sf.read(wav_src)
        if brut.ndim == 1:
            brut = brut.reshape(-1, 1)
        duree = len(brut) / sr
        sos = signal.butter(10, HIGHPASS_CUTOFF, "hp", fs=sr, output="sos")

        beams = construire_beams(brut, canaux)
        azimuts = {nom: az for nom, _, az in beams}
        RecordingBuffer = charger_birdnetlib()[1]

        brutes = {}          # (sci, debut) -> {"fin", "confs": {beam: conf}}
        especes = {}
        filtres = {}
        for i, (nom_beam, sig, _) in enumerate(beams):
            if ARRET.is_set():
                raise Interrompu()
            with verrou:
                etat["etape"] = "micro %s (%d/%d)" % (nom_beam, i + 1,
                                                      len(beams))
            y = signal.sosfilt(sos, sig)
            filtres[nom_beam] = y
            # Signal donne EN MEMOIRE (RecordingBuffer), sans fichier ni
            # librosa. Avant, chaque micro passait par un WAV que soundfile
            # ecrit en PCM 16 BITS par defaut, relu en float32 : on garde CE
            # MEME aller-retour (en memoire) pour que les detections restent
            # identiques au millieme a toutes les analyses deja faites. Sans
            # lui : confiances decalees jusqu'a 0,01 (mesure 2026-11-24).
            tampon = io.BytesIO()
            sf.write(tampon, y.astype(np.float32), sr, format="WAV")
            tampon.seek(0)
            y16 = sf.read(tampon, dtype="float32")[0]
            rec = RecordingBuffer(analyzer, y16, sr,
                                  lat=lat, lon=lon, date=date_filtre,
                                  min_conf=MIN_CONF, overlap=OVERLAP)
            rec.analyze()
            for d in rec.detections:
                sci = d["scientific_name"]
                cle = (sci, round(float(d["start_time"]), 1))
                e = brutes.setdefault(
                    cle, {"fin": round(float(d["end_time"]), 1), "confs": {}})
                c = float(d["confidence"])
                if c > e["confs"].get(nom_beam, 0):
                    e["confs"][nom_beam] = c
                if sci not in especes:
                    especes[sci] = {"en": d["common_name"],
                                    "fr": labels_fr.get(sci,
                                                       d["common_name"])}
            log("  %s : %s, %d detections" % (rel, nom_beam,
                                              len(rec.detections)))

        detections = []
        for (sci, debut), e in sorted(brutes.items(), key=lambda kv: kv[0][1]):
            confs = e["confs"]
            meilleur = max(confs, key=confs.get)
            detections.append({
                "debut": debut, "fin": e["fin"],
                "conf": round(max(confs.values()), 3),
                "sci": sci,
                "pos": position(confs, azimuts),
                "t": round(pic_energie(filtres[meilleur], debut, e["fin"]), 1),
            })
    finally:
        if os.path.exists(wav_src):
            try:
                os.remove(wav_src)
            except OSError:
                pass

    donnees = {
        "version": VERSION_SIDECAR,
        "fichier": os.path.basename(chemin),
        "duree": round(duree, 1),
        "canaux": canaux,
        "date_prise": date_prise.strftime("%Y-%m-%d"),
        "lat": lat, "lon": lon,
        "params": {"min_conf": MIN_CONF, "overlap": OVERLAP,
                   "date_filtre": date_filtre.strftime("%d/%m"),
                   "highpass": HIGHPASS_CUTOFF, "modele": MODELE,
                   "beams": [nom for nom, _, _ in beams]},
        "analyse_le": datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
        "especes": especes,
        "detections": detections,
    }
    # Ecriture atomique, temporaire propre au processus, dans analyses/.
    sidecar = sidecar_de(chemin, creer=True)
    if sidecar is None:
        raise Interrompu()        # dossier retire pendant l'analyse
    tmp = "%s.%d.tmp" % (sidecar, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(donnees, f, ensure_ascii=False)
    os.replace(tmp, sidecar)
    log("%s : %d detections, %d espece(s), %.0f s analysees en %.0f s" %
        (rel, len(detections), len(especes), duree, time.time() - t0))


def son_fin():
    """Joue tada.wav sur la machine quand la file d'analyse se vide : signal
    audible « c'est pret » sans avoir la page sous les yeux. Asynchrone,
    silencieux si le fichier manque ou hors Windows. Un tada.wav pose a cote
    du programme passe en premier ; sinon celui de Windows (le programme
    public n'en livre aucun : le son de Windows n'est pas redistribuable)."""
    if os.name != "nt":
        return
    for tada in (os.path.join(DOSSIER, "tada.wav"),
                 os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                              "Media", "tada.wav")):
        if os.path.exists(tada):
            break
    else:
        return
    try:
        import winsound
        winsound.PlaySound(tada, winsound.SND_FILENAME | winsound.SND_ASYNC)
    except Exception as e:
        log("son de fin impossible : %s" % e)


# --- Direct : analyse en continu d'une entree audio de Windows ---
# Idee reprise du direct du VPS (birds_live.py, 2026-08-13), ici avec une
# entree locale : micro, interface, cable virtuel (VB-Cable), "mixage stereo"...
# ffmpeg (dshow) liste les entrees et capture en f32 48 kHz ; un tampon
# glissant de DIRECT_FENETRE s est analyse toutes les DIRECT_PERIODE s avec
# les MEMES micros virtuels que les fichiers (construire_beams : stereo ->
# gauche / droite / cote, mono -> direct, 4 canaux -> AmbiX) et la meme
# position (position()). Presence avec les seuils du VPS : entree >= 0,25,
# "sur" >= 0,50, maintien >= 0,18, sortie apres 40 s sans rechant.
# ANNONCES = EVENEMENTS decides ICI (et non dans la page comme sur le VPS,
# pour que le journal texte dise exactement ce qui est annonce) : "arrivee"
# d'une espece (sauf si elle revient moins de DIRECT_GARDE s apres sa sortie
# : reaffichee en silence, anti va-et-vient a l'aube) et "confirmation"
# (incertain -> sur, une seule fois par sejour ; sur -> incertain est muet).
# La page les lit par /api/direct/etat et les dit dans une region aria-live
# si la case "annoncer" est cochee ; le serveur les ecrit dans
# direct/direct_<date>.txt si la case "fichier" est cochee.
# PIEGE : un interpreteur LiteRT n'est pas partageable entre threads -> le
# direct a SON Analyzer, distinct de celui du worker des fichiers.
DIRECT_FENETRE = 12            # s analysees a chaque passe
DIRECT_PERIODE = 6             # s entre deux passes (chaque son est vu 2 fois)
DIRECT_MIN_CONF = 0.15         # ce que BirdNET retourne ; le tri est ici :
DIRECT_CONF_ENTREE = 0.25      # apparition dans la liste
DIRECT_CONF_SUR = 0.50         # "sur" (sinon "(incertain)")
DIRECT_CONF_MAINTIEN = 0.18    # rechant qui garde l'espece en scene
DIRECT_SORTIE = 40             # s sans rechant -> sortie de scene
DIRECT_GARDE = 20              # s : revenue plus tot apres sa sortie -> muette
DIRECT_NIVEAU_CIBLE_DB = -28.0 # normalisation d'assurance (BirdNET est
DIRECT_GAIN_MAX_DB = 30.0      # insensible au gain, mesure VPS 2026-08-13)
DIRECT_SILENCE_DB = -72.0      # en dessous : pas d'analyse (entree muette)
DIRECT_AUDIO_PERIME = 15       # s sans audio frais -> "aucun son recu"
DIRECT_DIR = os.path.join(DONNEES, "direct")   # journaux texte
MONITEUR_MAX_BLOCS = 8         # blocs de 0,1 s en attente au plus (latence)
MONITEUR_SATURATION = 10       # blocs satures d'affilee (1 s) -> coupure


def entree_loopback(nom):
    """Entree qui reprend ce que l'ordinateur JOUE (mixage stereo et ses
    cousins) : le moniteur y ferait une boucle (larsen numerique)."""
    n = unicodedata.normalize("NFKD", nom or "").encode(
        "ascii", "ignore").decode().lower()
    return any(m in n for m in (
        "stereo mix", "stereomix", "mixage stereo", "mix stereo",
        "mezcla estereo", "missaggio stereo", "what u hear", "wave out mix",
        "loopback"))


class Moniteur:
    """Ecoute de l'entree du direct sur la sortie par defaut de Windows :
    waveOut (winmm) en ctypes, aucune dependance. Les blocs PCM 16 bits sont
    pousses au fil de la capture ; au plus MONITEUR_MAX_BLOCS en attente
    (latence bornee : on jette plutot que de prendre du retard). A n'utiliser
    que depuis UN thread, celui de la capture."""
    WAVE_MAPPER = ctypes.c_uint(0xFFFFFFFF)
    WHDR_DONE = 0x00000001

    class WAVEFORMATEX(ctypes.Structure):
        _fields_ = [("wFormatTag", wintypes.WORD),
                    ("nChannels", wintypes.WORD),
                    ("nSamplesPerSec", wintypes.DWORD),
                    ("nAvgBytesPerSec", wintypes.DWORD),
                    ("nBlockAlign", wintypes.WORD),
                    ("wBitsPerSample", wintypes.WORD),
                    ("cbSize", wintypes.WORD)]

    class WAVEHDR(ctypes.Structure):
        _fields_ = [("lpData", ctypes.c_void_p),
                    ("dwBufferLength", wintypes.DWORD),
                    ("dwBytesRecorded", wintypes.DWORD),
                    ("dwUser", ctypes.c_size_t),
                    ("dwFlags", wintypes.DWORD),
                    ("dwLoops", wintypes.DWORD),
                    ("lpNext", ctypes.c_void_p),
                    ("reserved", ctypes.c_size_t)]

    def __init__(self, canaux):
        self.winmm = ctypes.windll.winmm
        fmt = self.WAVEFORMATEX(1, canaux, SR, SR * canaux * 2, canaux * 2,
                                16, 0)
        self.hwo = wintypes.HANDLE()
        err = self.winmm.waveOutOpen(ctypes.byref(self.hwo), self.WAVE_MAPPER,
                                     ctypes.byref(fmt), 0, 0, 0)
        if err:
            raise RuntimeError("sortie audio (waveOut) : erreur %d" % err)
        self.en_vol = []                  # (WAVEHDR, tampon) en cours de jeu

    def _recuperer(self):
        restants = []
        for hdr, buf in self.en_vol:
            if hdr.dwFlags & self.WHDR_DONE:
                self.winmm.waveOutUnprepareHeader(self.hwo, ctypes.byref(hdr),
                                                  ctypes.sizeof(hdr))
            else:
                restants.append((hdr, buf))
        self.en_vol = restants

    def jouer(self, pcm):
        self._recuperer()
        if len(self.en_vol) >= MONITEUR_MAX_BLOCS:
            return False
        buf = ctypes.create_string_buffer(pcm, len(pcm))
        hdr = self.WAVEHDR()
        hdr.lpData = ctypes.addressof(buf)
        hdr.dwBufferLength = len(pcm)
        if self.winmm.waveOutPrepareHeader(self.hwo, ctypes.byref(hdr),
                                           ctypes.sizeof(hdr)):
            return False
        if self.winmm.waveOutWrite(self.hwo, ctypes.byref(hdr),
                                   ctypes.sizeof(hdr)):
            self.winmm.waveOutUnprepareHeader(self.hwo, ctypes.byref(hdr),
                                              ctypes.sizeof(hdr))
            return False
        self.en_vol.append((hdr, buf))
        return True

    def fermer(self):
        try:
            self.winmm.waveOutReset(self.hwo)
            for hdr, buf in self.en_vol:
                self.winmm.waveOutUnprepareHeader(self.hwo, ctypes.byref(hdr),
                                                  ctypes.sizeof(hdr))
            self.en_vol = []
            self.winmm.waveOutClose(self.hwo)
        except Exception:
            pass


def entrees_audio():
    """Entrees audio de Windows vues par ffmpeg (dshow) : [{nom, id}].
    L'identifiant est le "nom alternatif" (stable, sans ambiguite de
    doublon ni d'accent) ; le nom lisible sert a la page."""
    try:
        proc = subprocess.run(
            [FFMPEG, "-hide_banner", "-list_devices", "true", "-f", "dshow",
             "-i", "dummy"], capture_output=True, timeout=30, **SANS_FENETRE)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise RuntimeError("ffmpeg : %s" % e)
    entrees, courant, section = [], None, None
    for ligne in proc.stderr.decode("utf-8", "replace").splitlines():
        if "DirectShow audio devices" in ligne:
            section = "audio"
            continue
        if "DirectShow video devices" in ligne:
            section = "video"
            continue
        m = re.search(r'\]\s+"(.*)"\s+\((audio|video|none)\)\s*$', ligne)
        if m is None:
            m = re.search(r'\]\s+"(.*)"\s*$', ligne)      # vieux ffmpeg
            genre = section
        else:
            genre = m.group(2)
        if m:
            courant = {"nom": m.group(1), "id": m.group(1)} \
                if genre == "audio" else None
            if courant:
                entrees.append(courant)
            continue
        m = re.search(r'Alternative name\s+"(.*)"', ligne)
        if m and courant is not None:
            courant["id"] = m.group(1)
    for e in entrees:
        e["loopback"] = entree_loopback(e["nom"])
    return entrees


def source_direct(ident):
    """-> (commande ffmpeg sans -ac/-f, nombre de canaux) pour une entree.
    "fichier:<chemin>" = simulation en temps reel a partir d'un fichier
    (essais, demonstration) ; sinon identifiant dshow."""
    if ident.startswith("fichier:"):
        chemin = ident[len("fichier:"):]
        if not os.path.exists(chemin):
            raise RuntimeError("fichier introuvable : " + chemin)
        canaux = sonde(chemin)[0]
        cmd = [FFMPEG, "-loglevel", "error", "-re", "-stream_loop", "-1",
               "-i", chemin, "-vn"]
        if canaux == 4:
            cmd += ["-af", "channelmap=0|1|2|3:quad"]
        return cmd, canaux
    cmd = [FFMPEG, "-hide_banner", "-f", "dshow", "-i", "audio=" + ident,
           "-t", "0.2", "-f", "null", "-"]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=30,
                              **SANS_FENETRE)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise RuntimeError("ffmpeg : %s" % e)
    texte = proc.stderr.decode("utf-8", "replace")
    m = re.search(r"Stream #0:0.*?Audio: [^,]+, (\d+) Hz, ([^,]+)", texte)
    if not m:
        # message le plus parlant : la ligne dshow ("Could not find audio
        # only device with name [...]"), sinon la derniere
        lignes = [l.strip() for l in texte.splitlines() if l.strip()]
        utiles = [re.sub(r"^\[dshow @ [^\]]*\]\s*", "", l) for l in lignes
                  if l.startswith("[dshow")]
        raise RuntimeError((utiles or lignes or ["entree injoignable"])[0])
    layout = m.group(2).strip().lower()
    canaux = {"mono": 1, "stereo": 2, "2.1": 3, "quad": 4, "5.1": 6,
              "7.1": 8}.get(layout)
    if canaux is None:
        m2 = re.match(r"(\d+) channels", layout)
        canaux = int(m2.group(1)) if m2 else 2
    return [FFMPEG, "-loglevel", "error", "-f", "dshow",
            "-i", "audio=" + ident], canaux


class Direct:
    def __init__(self):
        self.verrou = threading.Lock()
        self.stop = threading.Event()
        self.actif = False
        self.ident = ""
        self.nom = ""
        self.canaux = 0
        self.lang = "fr"
        self.labels = {}
        self.proc = None
        self.blocs = deque()
        self.total = 0
        self.dernier_audio = 0.0
        self.depuis = 0.0
        self.erreur = ""
        self.niveau_db = None
        self.analyzer = None
        self.threads = []
        self.loopback = False          # entree de type mixage stereo
        self.moniteur = None           # objet Moniteur (thread capture)
        self.moniteur_voulu = False    # OFF par defaut, a chaque demarrage
        self.moniteur_message = ""     # "niveau" = coupe pour saturation
        self.sature = 0
        self._vider()

    def _vider(self):
        self.presences = {}      # sci -> sur, vu, beams, pos, dit_sur
        self.especes = {}        # sci -> {fr, en}
        self.sorties = {}        # sci -> instant de sortie (garde)
        self.historique = {}     # sci -> premiere, derniere, n, conf_max
        self.evenements = deque(maxlen=500)
        self.prochain_id = 1

    # --- commandes ---
    def demarrer(self, ident, nom, lang):
        with self.verrou:
            if self.actif:
                raise ValueError("le direct tourne deja")
        cmd, canaux = source_direct(ident)       # leve si entree injoignable
        if canaux < 1:
            raise RuntimeError("aucun canal audio sur cette entree")
        self.arreter()
        with self.verrou:
            self.stop.clear()
            self.ident, self.nom, self.canaux = ident, nom or ident, canaux
            self.lang = lang or "fr"
            self.labels = charger_labels(self.lang) or charger_labels("fr")
            self.cmd = cmd + ["-ac", str(canaux), "-ar", str(SR),
                              "-f", "f32le", "-"]
            self.blocs.clear()
            self.total = 0
            self.dernier_audio = 0.0
            self.depuis = time.time()
            self.erreur = ""
            self.niveau_db = None
            self.loopback = (not ident.startswith("fichier:") and
                             entree_loopback(nom))
            self.moniteur_voulu = False
            self.moniteur_message = ""
            self.sature = 0
            self._vider()
            self.actif = True
            self.threads = [threading.Thread(target=self._capture, daemon=True),
                            threading.Thread(target=self._analyse, daemon=True)]
        for th in self.threads:
            th.start()
        self._journal_ligne("--- direct demarre : %s (%s) ---" % (
            self.nom, "1 canal" if canaux == 1 else "%d canaux" % canaux))
        log("direct demarre : %s (%d canaux)" % (self.nom, canaux))

    def arreter(self):
        with self.verrou:
            if not self.actif:
                return
            self.actif = False
            self.stop.set()
            proc = self.proc
        if proc is not None:
            try:
                proc.kill()
            except OSError:
                pass
        for th in self.threads:
            th.join(10)
        self.threads = []
        self._journal_ligne("--- direct arrete ---")
        log("direct arrete.")

    def etat(self):
        with self.verrou:
            maintenant = time.time()
            with verrou:
                fichier = bool(config.get("direct_fichier"))
            return {
                "actif": self.actif, "entree": self.nom, "canaux": self.canaux,
                "depuis": self.depuis if self.actif else 0,
                "erreur": self.erreur,
                "audio_ok": self.actif and
                            maintenant - self.dernier_audio < DIRECT_AUDIO_PERIME,
                "niveau_db": None if self.niveau_db is None
                             else round(self.niveau_db, 1),
                "presences": {sci: {"sur": p["sur"], "pos": p["pos"]}
                              for sci, p in self.presences.items()},
                "especes": dict(self.especes),
                "historique": {sci: dict(h) for sci, h in
                               self.historique.items()},
                "evenements": list(self.evenements)[-50:],
                "fichier": fichier,
                "chemin_fichier": self._chemin_journal() if fichier else "",
                "moniteur": self.actif and self.moniteur_voulu,
                "moniteur_bloque": self.actif and self.loopback,
                "moniteur_message": self.moniteur_message,
            }

    # --- journal texte ---
    def _chemin_journal(self):
        return os.path.join(DIRECT_DIR, "direct_%s.txt" %
                            time.strftime("%Y-%m-%d"))

    def _journal_ligne(self, texte):
        with verrou:
            if not config.get("direct_fichier"):
                return
        try:
            os.makedirs(DIRECT_DIR, exist_ok=True)
            with open(self._chemin_journal(), "a", encoding="utf-8") as f:
                f.write(time.strftime("%H:%M:%S") + "  " + texte + "\n")
        except OSError as e:
            log("journal du direct : %s" % e)

    def _nom(self, sci):
        e = self.especes.get(sci, {})
        return self.labels.get(sci) or e.get("fr") or e.get("en") or sci

    def _evenement(self, genre, sci, conf, p):
        ev = {"id": self.prochain_id, "t": time.time(), "type": genre,
              "sci": sci, "conf": round(conf, 2), "sur": p["sur"],
              "pos": p["pos"]}
        self.prochain_id += 1
        self.evenements.append(ev)
        lieu = {"C": "centre", "CG": "centre gauche", "G": "gauche",
                "CD": "centre droit", "D": "droite", "H": "au-dessus"}.get(
                    p["pos"] or "", "")
        self._journal_ligne("%s (%s)  %s%d %%%s" % (
            self._nom(sci), sci,
            "confirme " if genre == "confirmation" else
            ("" if p["sur"] else "incertain "),
            round(conf * 100), ("  " + lieu) if lieu else ""))

    # --- capture (thread) ---
    def _capture(self):
        while not self.stop.is_set():
            proc = None
            try:
                proc = subprocess.Popen(self.cmd, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, **SANS_FENETRE)
                with self.verrou:
                    self.proc = proc
                taille = SR // 10 * self.canaux * 4       # blocs de 0,1 s
                while not self.stop.is_set():
                    brut = proc.stdout.read(taille)
                    if not brut:
                        break
                    y = np.frombuffer(brut, dtype=np.float32)
                    n = len(y) // self.canaux
                    if n == 0:
                        continue
                    y = y[:n * self.canaux].reshape(n, self.canaux)
                    with self.verrou:
                        self.blocs.append(y)
                        self.total += n
                        while (self.total - len(self.blocs[0]) >
                               DIRECT_FENETRE * SR):
                            self.total -= len(self.blocs.popleft())
                        self.dernier_audio = time.time()
                        self.erreur = ""
                    self._moniteur_bloc(y)
                if not self.stop.is_set():
                    err = proc.stderr.read().decode("utf-8", "replace")
                    lignes = [l for l in err.strip().splitlines() if l.strip()]
                    with self.verrou:
                        self.erreur = lignes[-1] if lignes else \
                            "entree audio perdue"
                    log("direct : %s" % self.erreur)
            except Exception as e:
                with self.verrou:
                    self.erreur = str(e)
                log("direct (capture) : %s" % e)
            finally:
                with self.verrou:
                    self.proc = None
                if proc is not None:
                    try:
                        proc.kill()
                    except OSError:
                        pass
                if self.moniteur is not None:
                    self.moniteur.fermer()
                    self.moniteur = None
            if not self.stop.is_set():
                self.stop.wait(5)                  # entree absente : on retente

    # --- moniteur (dans le thread de capture) ---
    def _moniteur_bloc(self, y):
        """Ouvre / ferme la sortie waveOut selon la demande de la page, joue
        le bloc, et se coupe TOUT SEUL si l'entree sature une seconde entiere
        (boucle entre la sortie et l'entree, ou micro devant l'enceinte)."""
        voulu = self.moniteur_voulu and not self.loopback
        if voulu and self.moniteur is None:
            try:
                self.moniteur = Moniteur(1 if self.canaux == 1 else 2)
                self.sature = 0
            except Exception as e:
                self.moniteur_voulu = False
                self.moniteur_message = str(e)
                log("moniteur : %s" % e)
                return
        elif not voulu and self.moniteur is not None:
            self.moniteur.fermer()
            self.moniteur = None
        if self.moniteur is None:
            return
        pic = float(np.max(np.abs(y))) if len(y) else 0.0
        self.sature = self.sature + 1 if pic > 0.95 else 0
        if self.sature >= MONITEUR_SATURATION:
            self.moniteur_voulu = False
            self.moniteur_message = "niveau"
            self.moniteur.fermer()
            self.moniteur = None
            self.sature = 0
            log("moniteur coupe : entree saturee une seconde (boucle ?)")
            return
        self.moniteur.jouer(self._pcm_moniteur(y))

    def _pcm_moniteur(self, y):
        """Bloc float32 (n, canaux) -> PCM 16 bits mono ou stereo : stereo
        tel quel, AmbiX decode en stereo (W+Y, W-Y) comme les extraits,
        au-dela de 4 canaux les deux premiers."""
        if self.canaux == 1:
            s = y[:, 0]
        elif self.canaux == 2:
            s = y
        elif self.canaux == 4:
            s = np.stack([y[:, 0] + y[:, 1], y[:, 0] - y[:, 1]], axis=1) * 0.7
        else:
            s = y[:, :2]
        return (np.clip(s, -1.0, 1.0) * 32767).astype(np.int16).tobytes()

    def _fenetre(self):
        with self.verrou:
            if not self.blocs:
                return None
            return np.concatenate(list(self.blocs), axis=0)

    # --- analyse (thread) ---
    def _analyse(self):
        try:
            if self.analyzer is None:
                Analyzer = charger_birdnetlib()[0]
                self.analyzer = Analyzer()
        except Exception as e:
            with self.verrou:
                self.erreur = "modele : %s" % e
            log("direct (modele) : %s" % e)
            return
        sos = signal.butter(10, HIGHPASS_CUTOFF, "hp", fs=SR, output="sos")
        while not self.stop.is_set():
            debut = time.time()
            try:
                self._passe(sos)
            except Exception as e:
                with self.verrou:
                    self.erreur = str(e)
                log("direct (analyse) : %s" % e)
            self.stop.wait(max(1.0, DIRECT_PERIODE - (time.time() - debut)))

    def _passe(self, sos):
        brut = self._fenetre()
        maintenant = time.time()
        if (brut is None or len(brut) < 3 * SR or
                maintenant - self.dernier_audio > DIRECT_AUDIO_PERIME):
            with self.verrou:
                self.niveau_db = None
            self._sorties(maintenant)
            return
        RecordingBuffer = charger_birdnetlib()[1]
        with verrou:
            lat, lon = float(config["lat"]), float(config["lon"])
        beams = construire_beams(brut, self.canaux)
        azimuts = {nom: az for nom, _, az in beams}
        niveau = 20 * np.log10(float(np.sqrt(np.mean(brut[:, 0] ** 2))) + 1e-12)
        with self.verrou:
            self.niveau_db = niveau
        vus, par_beam = {}, {}
        if niveau >= DIRECT_SILENCE_DB:
            for nom_beam, sig, _ in beams:
                if self.stop.is_set():
                    return
                y = signal.sosfilt(sos, sig)
                rms = float(np.sqrt(np.mean(y ** 2)))
                gain_db = min(DIRECT_GAIN_MAX_DB, max(
                    0.0, DIRECT_NIVEAU_CIBLE_DB - 20 * np.log10(rms + 1e-12)))
                y = (y * (10 ** (gain_db / 20))).astype(np.float32)
                rec = RecordingBuffer(self.analyzer, y, SR, lat=lat, lon=lon,
                                      date=datetime.now(),
                                      min_conf=DIRECT_MIN_CONF, overlap=OVERLAP)
                rec.analyze()
                for d in rec.detections:
                    sci = d["scientific_name"]
                    conf = float(d["confidence"])
                    if sci not in vus or conf > vus[sci][0]:
                        vus[sci] = (conf, d["common_name"])
                    b = par_beam.setdefault(sci, {})
                    if conf > b.get(nom_beam, 0):
                        b[nom_beam] = conf
        maintenant = time.time()
        with self.verrou:
            for sci, (conf, commun) in vus.items():
                if sci not in self.especes:
                    self.especes[sci] = {"en": commun,
                                         "fr": self.labels.get(sci, commun)
                                         if self.lang == "fr" else commun}
                p = self.presences.get(sci)
                nouveau = False
                if p is None:
                    if conf < DIRECT_CONF_ENTREE:
                        continue
                    p = {"sur": conf >= DIRECT_CONF_SUR, "vu": maintenant,
                         "beams": {}, "pos": None, "dit_sur": False}
                    self.presences[sci] = p
                    nouveau = True
                elif conf >= DIRECT_CONF_MAINTIEN:
                    p["vu"] = maintenant
                else:
                    continue
                for nb, c in par_beam.get(sci, {}).items():
                    p["beams"][nb] = (c, maintenant)
                frais = {nb: c for nb, (c, t0) in p["beams"].items()
                         if maintenant - t0 <= DIRECT_SORTIE}
                p["pos"] = position(frais, azimuts) if frais else None
                h = self.historique.setdefault(sci, {
                    "premiere": maintenant, "derniere": maintenant, "n": 0,
                    "conf_max": 0.0})
                h["derniere"] = maintenant
                h["n"] += 1
                if conf > h["conf_max"]:
                    h["conf_max"] = round(conf, 2)
                if nouveau:
                    sortie = self.sorties.pop(sci, None)
                    muet = (sortie is not None and
                            maintenant - sortie < DIRECT_GARDE)
                    if p["sur"]:
                        p["dit_sur"] = True
                    if not muet:
                        self._evenement("arrivee", sci, conf, p)
                elif conf >= DIRECT_CONF_SUR and not p["sur"]:
                    p["sur"] = True
                    if not p["dit_sur"]:
                        p["dit_sur"] = True
                        self._evenement("confirmation", sci, conf, p)
        self._sorties(maintenant)

    def _sorties(self, maintenant):
        with self.verrou:
            for sci in [s for s, p in self.presences.items()
                        if maintenant - p["vu"] > DIRECT_SORTIE]:
                del self.presences[sci]
                self.sorties[sci] = maintenant


direct = Direct()


# --- Coller des fichiers depuis l'Explorateur (Ctrl+C ou Ctrl+X, puis
# Ctrl+V dans la page ou bouton "Coller") -------------------------------------
# Une page web ne recoit PAS les fichiers copies dans l'Explorateur (le
# presse-papiers CF_HDROP n'arrive pas au JavaScript, et jamais les dossiers).
# Serveur et page etant sur la meme machine, c'est le SERVEUR qui lit le
# presse-papiers de Windows (ctypes, sans dependance) : il obtient les CHEMINS
# complets, copie (ou deplace, si les fichiers ont ete coupes) dans audio/, et
# fait SUIVRE l'analyse existante de chaque fichier (.birds.json a cote, ou
# dans notre analyses/ si la source est sous un dossier surveille) : rien
# n'est reanalyse pour rien. Rien n'est remplace : un fichier deja present
# dans audio/ est ignore et compte "deja". Travail en thread, etat `collage`
# publie dans /api/etat. Local uniquement (403 sinon : ce serait le
# presse-papiers du serveur, pas celui de l'utilisateur distant).
collage = {"en_cours": False, "total": 0, "faits": 0, "copies": 0,
           "deplaces": 0, "deja": 0, "suivis": 0, "ignores": 0, "erreur": "",
           "fini": 0.0}
verrou_collage = threading.Lock()
CF_HDROP = 15


def presse_papiers_fichiers():
    """-> (chemins, effet) : fichiers / dossiers copies ou coupes dans
    l'Explorateur (format CF_HDROP) ; effet = "deplacer" si coupes (format
    "Preferred DropEffect" avec le bit MOVE), sinon "copier". ([], "copier")
    si le presse-papiers ne contient pas de fichiers."""
    user32, shell32 = ctypes.windll.user32, ctypes.windll.shell32
    kernel32 = ctypes.windll.kernel32
    user32.OpenClipboard.argtypes = [ctypes.c_void_p]
    user32.GetClipboardData.restype = ctypes.c_void_p
    user32.GetClipboardData.argtypes = [ctypes.c_uint]
    user32.RegisterClipboardFormatW.argtypes = [ctypes.c_wchar_p]
    shell32.DragQueryFileW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                       ctypes.c_wchar_p, ctypes.c_uint]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    ouvert = False
    for _ in range(10):                     # un autre programme peut le tenir
        if user32.OpenClipboard(None):
            ouvert = True
            break
        time.sleep(0.05)
    if not ouvert:
        raise RuntimeError("presse-papiers occupe par un autre programme")
    try:
        h = user32.GetClipboardData(CF_HDROP)
        if not h:
            return [], "copier"
        n = shell32.DragQueryFileW(h, 0xFFFFFFFF, None, 0)
        chemins = []
        for i in range(n):
            taille = shell32.DragQueryFileW(h, i, None, 0) + 1
            tampon = ctypes.create_unicode_buffer(taille)
            shell32.DragQueryFileW(h, i, tampon, taille)
            if tampon.value:
                chemins.append(tampon.value)
        effet = "copier"
        fmt = user32.RegisterClipboardFormatW("Preferred DropEffect")
        hd = user32.GetClipboardData(fmt) if fmt else None
        if hd:
            ptr = kernel32.GlobalLock(hd)
            if ptr:
                try:
                    val = ctypes.cast(ptr, ctypes.POINTER(wintypes.DWORD))
                    if val.contents.value & 2:          # DROPEFFECT_MOVE
                        effet = "deplacer"
                finally:
                    kernel32.GlobalUnlock(hd)
        return chemins, effet
    finally:
        user32.CloseClipboard()


def vider_presse_papiers():
    """Apres un deplacement : comme l'Explorateur, on vide le presse-papiers
    (les fichiers coupes n'existent plus a l'ancien endroit)."""
    user32 = ctypes.windll.user32
    user32.OpenClipboard.argtypes = [ctypes.c_void_p]
    if user32.OpenClipboard(None):
        try:
            user32.EmptyClipboard()
        finally:
            user32.CloseClipboard()


def sidecar_source(audio):
    """Analyse existante d'un fichier a coller : .birds.json a cote (ancienne
    disposition, fichier venu d'une autre machine) ou dans NOTRE analyses/
    s'il est sous un dossier surveille. None sinon."""
    a_cote = audio + SUFFIXE
    if os.path.exists(a_cote):
        return a_cote
    sc = sidecar_de(audio)
    return sc if sc and os.path.exists(sc) else None


def coller_fichiers(chemins, effet):
    """Thread : copie (ou deplace) fichiers et dossiers dans audio/, analyses
    comprises. Les .birds.json de la source ne sont pas copies tels quels :
    ils sont ranges a la bonne place par sidecar_de()."""
    audio_dir = os.path.realpath(AUDIO_DIR)
    taches, ignores = [], 0
    for chemin in chemins:
        src = os.path.realpath(chemin)
        # inexistant, deja dans audio/, ou dossier qui CONTIENT audio/
        # (copie sans fin) : ignore
        if (not os.path.exists(src) or dans(src, audio_dir) or
                dans(audio_dir, src)):
            ignores += 1
            continue
        if os.path.isdir(src):
            nom = os.path.basename(src.rstrip("\\/")) or "dossier"
            base = os.path.join(audio_dir, nom)
            for racine, dossiers, fichiers in os.walk(src):
                dossiers[:] = [d for d in dossiers if not d.startswith(".")]
                for f in fichiers:
                    if f.endswith(SUFFIXE) or f.endswith(".tmp"):
                        continue
                    sf_ = os.path.join(racine, f)
                    taches.append((sf_, os.path.join(
                        base, os.path.relpath(sf_, src))))
        elif src.endswith(SUFFIXE):
            ignores += 1
        else:
            taches.append((src, os.path.join(audio_dir,
                                             os.path.basename(src))))
    with verrou_collage:
        collage.update(en_cours=True, total=len(taches), faits=0, copies=0,
                       deplaces=0, deja=0, suivis=0, ignores=ignores,
                       erreur="", fini=0.0)
    deplacer = effet == "deplacer"
    for src, dst in taches:
        try:
            if os.path.exists(dst):
                with verrou_collage:
                    collage["deja"] += 1
                continue
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            sc_src = (sidecar_source(src)
                      if src.lower().endswith(EXTENSIONS) else None)
            if deplacer:
                shutil.move(src, dst)
            else:
                shutil.copy2(src, dst)          # garde la date du fichier
            if sc_src:
                sc_dst = sidecar_de(dst, creer=True)
                if sc_dst:
                    shutil.copy2(sc_src, sc_dst)
                    if deplacer:
                        try:
                            os.remove(sc_src)
                        except OSError:
                            pass
                    with verrou_collage:
                        collage["suivis"] += 1
            with verrou_collage:
                collage["deplaces" if deplacer else "copies"] += 1
        except Exception as e:
            log("collage : %s : %s" % (src, e))
            with verrou_collage:
                collage["erreur"] = "%s : %s" % (os.path.basename(src), e)
        finally:
            with verrou_collage:
                collage["faits"] += 1
    if deplacer:
        for chemin in chemins:                 # dossiers sources vides
            src = os.path.realpath(chemin)
            if os.path.isdir(src):
                for racine, dossiers, fichiers in os.walk(src, topdown=False):
                    try:
                        os.rmdir(racine)
                    except OSError:
                        pass
        try:
            vider_presse_papiers()
        except Exception:
            pass
    with verrou_collage:
        collage.update(en_cours=False, fini=time.time())
        c = dict(collage)
    log("collage : %d copie(s), %d deplace(s), %d deja la, %d analyse(s) "
        "suivie(s), %d ignore(s)" % (c["copies"], c["deplaces"], c["deja"],
                                     c["suivis"], c["ignores"]))
    try:
        balayer()                              # analyses lancees de suite
    except Exception as e:
        log("balayage apres collage : %s" % e)


# --- Threads de fond ---
def worker():
    """Analyse en serie ce que la veille (ou la page) met en file. Le modele
    n'est charge qu'a la premiere analyse (l'import TensorFlow est lourd)."""
    analyzer = None
    labels_fr = charger_labels("fr")
    while True:
        with verrou:
            rel = file_attente.popleft() if file_attente else None
            if rel:
                etat["en_cours"] = rel
                etat["etape"] = "preparation"
                etat["depuis"] = time.time()
        if rel is None:
            time.sleep(1)
            continue
        try:
            if analyzer is None:
                log("chargement du modele BirdNET...")
                Analyzer = charger_birdnetlib()[0]
                analyzer = Analyzer()
                log("modele charge.")
            analyser_fichier(analyzer, labels_fr, rel)
            with verrou:
                etat["faits"] += 1
                erreurs.pop(rel, None)
        except Interrompu:
            log("%s : analyse interrompue par l'arret (reprise au prochain "
                "lancement)." % rel)
            with verrou:
                etat["en_cours"] = None
                etat["etape"] = ""
            return
        except Exception as e:
            try:
                mtime = os.path.getmtime(chemin_sur(rel))
            except OSError:
                mtime = 0
            with verrou:
                erreurs[rel] = (mtime, str(e))
            log("ERREUR sur %s : %s" % (rel, e))
        finally:
            with verrou:
                etat["en_cours"] = None
                etat["etape"] = ""
                fini = not file_attente
            if fini:
                son_fin()          # la file est vide : toutes analyses faites


def veille():
    while True:
        try:
            balayer()
        except Exception as e:
            log("veille : %s" % e)
        time.sleep(VEILLE_S)


# --- Serveur HTTP ---
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):           # journal HTTP silencieux
        pass

    def repondre(self, code, corps, ctype="application/json; charset=utf-8"):
        octets = corps if isinstance(corps, bytes) else corps.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(octets)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(octets)

    def repondre_json(self, objet, code=200):
        self.repondre(code, json.dumps(objet, ensure_ascii=False))

    def autorise(self):
        """Seule NOTRE page parle au serveur. Un site web ouvert dans le
        navigateur peut viser 127.0.0.1 : requete d'un autre site -> Origin
        etrangere (refusee) ; nom de domaine pointe sur 127.0.0.1 (rebinding
        DNS) -> en-tete Host etranger (refuse). Repond 403 sinon."""
        hotes = ("127.0.0.1:%d" % PORT, "localhost:%d" % PORT)
        hote = (self.headers.get("Host") or "").lower()
        origine = self.headers.get("Origin")
        if hote in hotes and (origine is None or origine.lower() in
                              ["http://" + h for h in hotes]):
            return True
        self.repondre_json({"erreur": "requete refusee"}, 403)
        return False

    def do_GET(self):
        if not self.autorise():
            return
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        try:
            if u.path == "/":
                with open(PAGE, encoding="utf-8") as f:
                    self.repondre(200, f.read(),
                                  "text/html; charset=utf-8")
            elif u.path == "/api/etat":
                with verrou:
                    self.repondre_json({
                        "en_cours": etat["en_cours"],
                        "etape": etat["etape"],
                        "depuis": round(time.time() - etat["depuis"])
                                  if etat["en_cours"] else 0,
                        "attente": list(file_attente),
                        "faits": etat["faits"],
                        "erreurs": {r: m for r, (_, m) in erreurs.items()},
                        "journal": list(journal),
                        "collage": self.etat_collage(),
                    })
            elif u.path == "/api/fichiers":
                self.repondre_json(self.lister())
            elif u.path == "/api/direct/entrees":
                with verrou:
                    choisie = config.get("direct_entree", "")
                self.repondre_json({"entrees": entrees_audio(),
                                    "choisie": choisie})
            elif u.path == "/api/direct/etat":
                self.repondre_json(direct.etat())
            elif u.path == "/api/oiseaux":
                rel = q.get("f", [""])[0]
                sc = sidecar_de(chemin_sur(rel))
                if not sc or not os.path.exists(sc):
                    self.repondre_json({"erreur": "pas encore analyse"}, 404)
                else:
                    with open(sc, encoding="utf-8") as f:
                        self.repondre(200, f.read())
            elif u.path == "/api/extrait":
                rel = q.get("f", [""])[0]
                chemin = chemin_sur(rel)          # anti-traversal
                if not os.path.exists(chemin):
                    self.repondre_json({"erreur": "fichier inconnu"}, 404)
                else:
                    debut = max(0.0, float(q.get("d", ["0"])[0]))
                    duree = min(EXTRAIT_MAX,
                                max(0.5, float(q.get("l", ["6"])[0])))
                    self.repondre(200, extraire(chemin, debut, duree),
                                  "audio/wav")
            elif u.path == "/api/config":
                with verrou:
                    c = dict(config)
                c["dossier_principal"] = AUDIO_DIR
                c["dossiers_absents"] = [d for d in c["dossiers"]
                                         if not os.path.isdir(d)]
                c["appli"] = APPLI           # None : page dans un navigateur
                self.repondre_json(c)
            elif u.path == "/api/pays":
                # tri alphabetique SANS tenir compte des accents (sinon
                # Egypte et Etats-Unis se retrouvent apres Zimbabwe).
                # "code" ISO -> la page affiche le nom du pays dans la
                # langue de l'appareil (Intl.DisplayNames) et re-trie ;
                # "nom" (francais) reste l'identifiant envoye a la config
                # et le nom de secours. Le centre du pays pre-remplit les
                # champs latitude/longitude de la page.
                self.repondre_json(sorted(
                    [{"nom": n, "code": PAYS_CODE.get(n),
                      "lat": la, "lon": lo}
                     for n, (la, lo) in PAYS.items()],
                    key=lambda p: unicodedata.normalize(
                        "NFD", p["nom"].lower()).encode("ascii", "ignore")))
            elif u.path == "/api/langues":
                self.repondre_json(
                    sorted([{"code": c, "nom": n} for c, n in LANGUES.items()
                            if os.path.exists(os.path.join(
                                LABELS_DIR, "labels_%s.txt" % c))],
                           key=lambda x: x["nom"]))
            elif u.path == "/api/labels":
                code = q.get("lang", ["fr"])[0]
                if code not in LANGUES:
                    self.repondre_json({"erreur": "langue inconnue"}, 404)
                else:
                    self.repondre_json(charger_labels(code))
            else:
                self.repondre_json({"erreur": "route inconnue"}, 404)
        except (ValueError, RuntimeError) as e:
            self.repondre_json({"erreur": str(e)}, 400)
        except Exception as e:
            self.repondre_json({"erreur": str(e)}, 500)

    def do_POST(self):
        if not self.autorise():
            return
        u = urllib.parse.urlparse(self.path)
        try:
            n = int(self.headers.get("Content-Length", 0) or 0)
            corps = json.loads(self.rfile.read(n) or b"{}") if n else {}
            if u.path == "/api/analyser":
                force = bool(corps.get("force"))
                rel = corps.get("f")
                if rel:
                    chemin = chemin_sur(rel)      # valide + anti-traversal
                    if not os.path.exists(chemin):
                        self.repondre_json({"erreur": "fichier inconnu"}, 404)
                        return
                    with verrou:
                        erreurs.pop(rel, None)
                    mettre_en_file(rel)
                else:
                    if force:
                        with verrou:
                            erreurs.clear()
                    balayer(force=force)
                with verrou:
                    en_attente = len(file_attente)
                self.repondre_json({"ok": True, "en_attente": en_attente})
            elif u.path == "/api/config":
                self.maj_config(corps)
            elif u.path == "/api/dossiers":
                self.maj_dossiers(corps)
            elif u.path == "/api/choisir_dossier":
                self.choisir_dossier()
            elif u.path == "/api/jouer":
                # Le son sort sur la machine du SERVEUR : refuse un client
                # distant (sa page basculera sur la lecture navigateur).
                if self.client_address[0] not in ("127.0.0.1", "::1"):
                    self.repondre_json(
                        {"erreur": "lecture serveur : local uniquement"}, 403)
                    return
                chemin = chemin_sur(corps.get("f") or "")
                if not os.path.exists(chemin):
                    self.repondre_json({"erreur": "fichier inconnu"}, 404)
                    return
                debut = max(0.0, float(corps.get("d") or 0))
                duree = min(EXTRAIT_MAX, max(0.5, float(corps.get("l") or 6)))
                jouer_local(chemin, debut, duree)
                self.repondre_json({"ok": True})
            elif u.path == "/api/arreter":
                arreter_local()
                self.repondre_json({"ok": True})
            elif u.path == "/api/pause":
                self.repondre_json({"ok": True, "etat": pause_local()})
            elif u.path == "/api/direct/demarrer":
                ident = str(corps.get("entree") or "")
                if not ident:
                    raise ValueError("aucune entree audio choisie")
                nom = str(corps.get("nom") or ident)
                direct.demarrer(ident, nom, str(corps.get("lang") or "fr"))
                with verrou:
                    config["direct_entree"] = ident
                    config["direct_entree_nom"] = nom
                    sauver_config()
                self.repondre_json({"ok": True, "canaux": direct.canaux})
            elif u.path == "/api/direct/arreter":
                direct.arreter()
                self.repondre_json({"ok": True})
            elif u.path == "/api/coller":
                # Le presse-papiers lu est celui du SERVEUR : seul un client
                # local a un sens (sinon ce serait un autre utilisateur).
                if self.client_address[0] not in ("127.0.0.1", "::1"):
                    self.repondre_json(
                        {"erreur": "collage : local uniquement"}, 403)
                    return
                with verrou_collage:
                    if collage["en_cours"]:
                        raise ValueError("un collage est deja en cours")
                chemins, effet = presse_papiers_fichiers()
                if not chemins:
                    self.repondre_json({"ok": True, "trouves": 0})
                    return
                threading.Thread(target=coller_fichiers,
                                 args=(chemins, effet), daemon=True).start()
                self.repondre_json({"ok": True, "trouves": len(chemins),
                                    "effet": effet})
            elif u.path == "/api/direct/moniteur":
                actif = bool(corps.get("actif"))
                if actif:
                    if not direct.actif:
                        raise ValueError("le direct n'est pas lance")
                    if direct.loopback:
                        raise ValueError("moniteur bloque : cette entree "
                                         "reprend ce que l'ordinateur joue "
                                         "(boucle)")
                    direct.moniteur_message = ""
                direct.moniteur_voulu = actif
                self.repondre_json({"ok": True, "moniteur": actif})
            elif u.path == "/api/direct/fichier":
                with verrou:
                    config["direct_fichier"] = bool(corps.get("actif"))
                    sauver_config()
                    actif = config["direct_fichier"]
                self.repondre_json({"ok": True, "actif": actif,
                                    "chemin": direct._chemin_journal()
                                    if actif else ""})
            elif u.path == "/api/quitter":
                # Arret propre demande depuis la page. On repond d'abord, puis
                # on quitte avec le code 0 : le .bat ne fait "pause" que sur
                # erreur, donc la fenetre de console se ferme toute seule.
                # Programme a fenetre : on ferme la fenetre, qui arrete tout.
                log("arret demande depuis la page.")
                self.repondre_json({"ok": True})

                def sortir():
                    # Analyse en cours : on lui laisse quelques secondes pour
                    # s'interrompre proprement (rien d'ecrit a moitie), puis
                    # fermeture sans aucune confirmation.
                    arreter_analyse()
                    if QUITTER:
                        QUITTER()
                    else:
                        os._exit(0)
                threading.Timer(0.2, sortir).start()
            elif u.path == "/api/montrer":
                # Programme relance alors qu'il tourne deja : la nouvelle
                # instance demande a celle-ci de montrer sa fenetre.
                if MONTRER:
                    MONTRER()
                self.repondre_json({"ok": bool(MONTRER)})
            else:
                self.repondre_json({"erreur": "route inconnue"}, 404)
        except (ValueError, RuntimeError) as e:
            self.repondre_json({"erreur": str(e)}, 400)
        except Exception as e:
            self.repondre_json({"erreur": str(e)}, 500)

    def maj_config(self, corps):
        """Lieu (par PAYS) + saison, valides puis persistes. S'appliquent aux
        PROCHAINES analyses (les sidecars existants gardent leurs resultats)."""
        pays = corps.get("pays")
        if pays is not None:
            if pays not in PAYS:
                raise ValueError("pays inconnu : " + str(pays))
            lat, lon = PAYS[pays]
            # coordonnees precises facultatives : si la page les envoie,
            # elles remplacent le centre du pays (filtre affine)
            if corps.get("lat") is not None and corps.get("lon") is not None:
                lat, lon = float(corps["lat"]), float(corps["lon"])
        else:                               # compat : lat/lon directs
            pays = ""
            lat, lon = float(corps.get("lat")), float(corps.get("lon"))
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError("latitude ou longitude hors bornes")
        mode = corps.get("date_mode", "auto")
        jour = int(corps.get("jour", 15))
        mois = int(corps.get("mois", 6))
        if mode not in ("auto", "fixe"):
            raise ValueError("date_mode inconnu")
        datetime(2024, mois, jour)          # leve ValueError si date impossible
        with verrou:
            config.update(pays=pays, lat=lat, lon=lon, date_mode=mode,
                          jour=jour, mois=mois)
            sauver_config()
        log("reglages : %s (lat %.1f lon %.1f), saison %s" %
            (pays or "coordonnees", lat, lon,
             "auto" if mode == "auto" else "%02d/%02d" % (jour, mois)))
        self.repondre_json({"ok": True})

    def maj_dossiers(self, corps):
        """Ajout / retrait d'un dossier surveille (en plus de audio/)."""
        ajouter, retirer = corps.get("ajouter"), corps.get("retirer")
        if ajouter:
            d = os.path.realpath(str(ajouter))
            if not os.path.isdir(d):
                raise ValueError("ce dossier n'existe pas : " + d)
            if dans(d, AUDIO_DIR):
                raise ValueError("ce dossier est deja dans audio/")
            with verrou:
                if d not in config["dossiers"]:
                    config["dossiers"].append(d)
                    sauver_config()
            log("dossier ajoute a la surveillance : " + d)
            balayer()                        # fichiers pris en compte de suite
        elif retirer:
            d = os.path.realpath(str(retirer))
            with verrou:
                if d in config["dossiers"]:
                    config["dossiers"].remove(d)
                    sauver_config()
                # Purge de la file : ces fichiers ne sont plus atteignables.
                for r in list(file_attente):
                    if dans(os.path.realpath(r.replace("/", os.sep)), d):
                        file_attente.remove(r)
            log("dossier retire de la surveillance : " + d)
        else:
            raise ValueError("il faut 'ajouter' ou 'retirer'")
        with verrou:
            liste = list(config["dossiers"])
        self.repondre_json({"ok": True, "dossiers": liste,
                            "dossiers_absents": [d for d in liste
                                                 if not os.path.isdir(d)]})

    def choisir_dossier(self):
        """Ouvre la boite Windows native de choix de dossier (accessible NVDA)
        SUR la machine du serveur, puis ajoute le dossier choisi. Le web ne
        peut pas transmettre un chemin local (securite navigateur) : comme le
        serveur est local, on passe par lui."""
        if not picker_verrou.acquire(blocking=False):
            self.repondre_json(
                {"erreur": "une fenetre de choix est deja ouverte "
                           "(elle est peut-etre derriere le navigateur)"}, 409)
            return
        try:
            if CHOISIR_DOSSIER:
                # Programme a fenetre : vraie boite Windows, rattachee a la
                # fenetre (donc au premier plan).
                choisi = CHOISIR_DOSSIER() or ""
            elif GELE:
                # Exe en mode navigateur (sans WebView2) : pas de
                # "python -c" ; l'exe ouvre la boite lui-meme et ecrit le
                # choix dans un fichier (un exe sans console n'a pas de
                # sortie standard fiable).
                sortie = os.path.join(TMP_DIR, "dossier_choisi.txt")
                if os.path.exists(sortie):
                    os.remove(sortie)
                subprocess.run([sys.executable, "--choisir-dossier", sortie],
                               timeout=600)
                choisi = ""
                if os.path.exists(sortie):
                    with open(sortie, encoding="utf-8") as f:
                        choisi = f.read().strip()
            else:
                res = subprocess.run([sys.executable, "-c",
                                      CODE_BOITE_DOSSIER],
                                     capture_output=True, timeout=600)
                choisi = res.stdout.decode("utf-8", "replace").strip()
        except subprocess.TimeoutExpired:
            self.repondre_json({"erreur": "fenetre restee ouverte trop "
                                          "longtemps, abandonnee"}, 408)
            return
        finally:
            picker_verrou.release()
        if not choisi:
            self.repondre_json({"dossier": None})     # annule par l'utilisateur
            return
        self.maj_dossiers({"ajouter": choisi})

    def etat_collage(self):
        with verrou_collage:
            return dict(collage)

    def lister(self):
        """Liste des enregistrements (recursif) avec resume du sidecar."""
        liste = []
        with verrou:
            en_cours = etat["en_cours"]
            attente = set(file_attente)
            err = {r: m for r, (_, m) in erreurs.items()}
        for chemin in lister_audio():
            rel = rel_de(chemin)
            info = {"f": rel, "octets": os.path.getsize(chemin)}
            # "statut" est un CODE (pas une phrase) : c'est la page qui le
            # traduit dans la langue de l'appareil.
            if rel == en_cours:
                info["statut"] = "en_cours"
            elif rel in attente:
                info["statut"] = "attente"
            elif rel in err:
                info["statut"] = "erreur"
                info["erreur"] = err[rel]
            elif os.path.exists(sidecar_de(chemin) or ""):
                info["statut"] = "analyse"
                try:
                    with open(sidecar_de(chemin), encoding="utf-8") as f:
                        sc = json.load(f)
                    info["duree"] = sc.get("duree")
                    info["canaux"] = sc.get("canaux")
                    info["date_prise"] = sc.get("date_prise")
                    info["n_especes"] = len(sc.get("especes", {}))
                    info["n_detections"] = len(sc.get("detections", []))
                    # Confiance max par espece : permet a la page de filtrer
                    # la liste par nom d'espece (en respectant le seuil
                    # "detections faibles") sans ouvrir chaque sidecar.
                    confs = {}
                    for d in sc.get("detections", []):
                        sci = d.get("sci")
                        if sci and d.get("conf", 0) > confs.get(sci, 0):
                            confs[sci] = d["conf"]
                    info["especes"] = confs
                except (OSError, ValueError):
                    info["statut"] = "illisible"
            else:
                info["statut"] = "pas_analyse"
            liste.append(info)
        return liste


def port_occupe():
    # Garde-fou "deja lance" (piege verifie dans accessible_chat) : sous
    # Windows, se lier a un port DEJA ecoute reussit SANS erreur, et c'est
    # l'ANCIEN processus qui recoit toutes les requetes -> interface figee
    # que relancer n'arrange pas. On sonde donc le port avant de se lier.
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1.0)
    deja = s.connect_ex(("127.0.0.1", PORT)) == 0
    s.close()
    return deja


def demarrer():
    """Prepare les dossiers, lance analyse et veille, et renvoie le serveur
    HTTP lie au port (pas encore en service : serve_forever a l'appelant)."""
    charger_calcul()
    os.makedirs(AUDIO_DIR, exist_ok=True)
    os.makedirs(TMP_DIR, exist_ok=True)
    purge_tmp()
    charger_config()
    ranger()
    if not os.path.exists(PAGE):
        print("ATTENTION : birdnet.html introuvable a cote du serveur.")
    if os.sep not in FFMPEG:
        print("ATTENTION : ffmpeg n'est pas dans bin\\ (on tente le PATH). "
              "Double-cliquer sur installer_ffmpeg.bat pour le mettre dans "
              "le pack.")
    threading.Thread(target=worker, daemon=True).start()
    threading.Thread(target=veille, daemon=True).start()
    serveur = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    log("Serveur BirdNET local : http://127.0.0.1:%d/" % PORT)
    log("Dossier surveille (sous-dossiers compris) : %s" % AUDIO_DIR)
    return serveur


def main():
    if port_occupe():
        print("Le serveur BirdNET tourne DEJA (port %d occupe)." % PORT)
        print("Utiliser la page deja ouverte : http://127.0.0.1:%d/" % PORT)
        print("Pour forcer un redemarrage, fermer d'abord l'ancienne fenetre")
        print("de console, ou :  netstat -ano | findstr :%d" % PORT)
        print("puis :  taskkill /PID <numero> /F")
        sys.exit(1)
    serveur = demarrer()
    try:
        serveur.serve_forever()
    except KeyboardInterrupt:
        print("\nArret.")


if __name__ == "__main__":
    main()

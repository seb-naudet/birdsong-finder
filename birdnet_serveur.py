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
# Sidecar <fichier>.birds.json a cote de chaque audio (format v2 du VPS).
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
BIN_DIR = os.path.join(RESSOURCES, "bin")
PAGE = os.path.join(RESSOURCES, "birdnet.html")

# Crochets poses par birdnet_app.py (programme a fenetre). Restent None
# quand le serveur tourne seul (page dans le navigateur, .bat).
APPLI = None            # {"nom": ..., "version": ...} : infos pour la page
CHOISIR_DOSSIER = None  # () -> chemin ou None : boite native de la fenetre
QUITTER = None          # () -> ferme la fenetre (et donc le programme)
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
    DERNIERE ligne de ffmpeg (lisible dans la page) au lieu de la commande."""
    res = subprocess.run(cmd, capture_output=True, **SANS_FENETRE)
    if res.returncode != 0:
        lignes = res.stderr.decode("utf-8", "replace").strip().splitlines()
        raise RuntimeError("%s : %s" % (os.path.basename(cmd[0]),
                                        lignes[-1] if lignes else
                                        "code %d" % res.returncode))
    return res.stdout

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
}
config = dict(CONFIG_DEFAUT)


def charger_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            j = json.load(f)
        for cle in CONFIG_DEFAUT:
            if cle in j:
                config[cle] = j[cle]
        config["dossiers"] = [d for d in config["dossiers"]
                              if isinstance(d, str) and os.path.isdir(d)]
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
    sc = chemin + SUFFIXE
    if not os.path.exists(sc):
        return True
    try:
        return os.path.getmtime(sc) < os.path.getmtime(chemin)
    except OSError:
        return True


def nettoyer():
    """Sidecars orphelins (audio supprime) -> retires. Recursif, ne touche
    JAMAIS l'audio. Seul audio/ est nettoye : dans un dossier AJOUTE puis
    retire de la surveillance, on ne supprime rien."""
    for base, dossiers, fichiers in os.walk(AUDIO_DIR):
        dossiers[:] = [d for d in dossiers if not d.startswith(".")]
        for f in fichiers:
            if f.endswith(SUFFIXE):
                sc = os.path.join(base, f)
                if not os.path.exists(sc[:-len(SUFFIXE)]):
                    try:
                        os.remove(sc)
                        log("nettoyage : " + rel_de(sc) + " (audio supprime)")
                    except OSError as e:
                        log("nettoyage impossible : %s" % e)


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
        with open(chemin + SUFFIXE, encoding="utf-8") as f:
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
# machine, on joue l'extrait ICI avec winsound : aucune politique
# navigateur, la barre d'espace marche toujours. La page garde Web Audio en
# secours (acces distant, ou serveur non Windows).
verrou_lecture = threading.Lock()
lecture_idx = 0


def jouer_local(chemin, debut, duree):
    import winsound            # Windows seulement : sinon ImportError -> 500,
    global lecture_idx         # et la page bascule sur la lecture navigateur
    donnees = extraire(chemin, debut, duree)
    with verrou_lecture:
        # Deux fichiers en alternance : winsound tient ouvert le WAV en cours
        # de lecture, on ne peut pas le reecrire directement.
        lecture_idx ^= 1
        sortie = os.path.join(TMP_DIR, "lecture_%d.wav" % lecture_idx)
        winsound.PlaySound(None, winsound.SND_PURGE)
        with open(sortie, "wb") as f:
            f.write(donnees)
        winsound.PlaySound(sortie, winsound.SND_FILENAME | winsound.SND_ASYNC
                           | winsound.SND_NODEFAULT)


def arreter_local():
    import winsound
    winsound.PlaySound(None, winsound.SND_PURGE)


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
    # Ecriture atomique, temporaire propre au processus.
    sidecar = chemin + SUFFIXE
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
                    })
            elif u.path == "/api/fichiers":
                self.repondre_json(self.lister())
            elif u.path == "/api/oiseaux":
                rel = q.get("f", [""])[0]
                sc = chemin_sur(rel) + SUFFIXE
                if not os.path.exists(sc):
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
        except ValueError as e:
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
            elif u.path == "/api/quitter":
                # Arret propre demande depuis la page. On repond d'abord, puis
                # on quitte avec le code 0 : le .bat ne fait "pause" que sur
                # erreur, donc la fenetre de console se ferme toute seule.
                # Programme a fenetre : on ferme la fenetre, qui arrete tout.
                log("arret demande depuis la page.")
                threading.Timer(0.4, QUITTER or os._exit,
                                [] if QUITTER else [0]).start()
                self.repondre_json({"ok": True})
            elif u.path == "/api/montrer":
                # Programme relance alors qu'il tourne deja : la nouvelle
                # instance demande a celle-ci de montrer sa fenetre.
                if MONTRER:
                    MONTRER()
                self.repondre_json({"ok": bool(MONTRER)})
            else:
                self.repondre_json({"erreur": "route inconnue"}, 404)
        except ValueError as e:
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
        self.repondre_json({"ok": True, "dossiers": liste})

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
            elif os.path.exists(chemin + SUFFIXE):
                info["statut"] = "analyse"
                try:
                    with open(chemin + SUFFIXE, encoding="utf-8") as f:
                        sc = json.load(f)
                    info["duree"] = sc.get("duree")
                    info["canaux"] = sc.get("canaux")
                    info["date_prise"] = sc.get("date_prise")
                    info["n_especes"] = len(sc.get("especes", {}))
                    info["n_detections"] = len(sc.get("detections", []))
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

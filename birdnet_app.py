# birdnet_app.py - programme Windows a fenetre autour du serveur BirdNET local
#
# Meme serveur, meme page (birdnet.html), mais affichee dans SA PROPRE
# fenetre (WebView2, le moteur web integre a Windows, via pywebview) au lieu
# d'un onglet de navigateur. Fermer la fenetre arrete tout. Point d'entree
# de l'exe portable (construire_exe.py) ; marche aussi depuis un venv :
#     venv\Scripts\python birdnet_app.py [--navigateur]
#
# - Fenetre ouverte TOUT DE SUITE sur une page d'attente ; le serveur (et
#   ses bibliotheques de calcul, longues a charger la premiere fois) demarre
#   derriere, puis la fenetre passe sur la vraie page.
# - Une seule instance : relancer le programme ramene sa fenetre devant.
#   Si c'est le serveur du .bat qui tourne, sa page s'ouvre dans le
#   navigateur.
# - Fermer pendant une analyse demande confirmation (elle reprendra au
#   prochain lancement de toute facon).
# - Pas de WebView2 (vieux Windows 10) ou --navigateur : la page s'ouvre
#   dans le navigateur par defaut, le bouton « Quitter » de la page arrete.
# - Exe sans console : sorties et erreurs dans data\journal.txt.
import ctypes
import json
import os
import sys
import threading
import traceback
import urllib.request
import webbrowser
from ctypes import wintypes

NOM = "Birdsong Finder"          # nom affiche (fenetre, page, boites)
NOM_FICHIER = "BirdsongFinder"   # nom de l'exe, du dossier et de l'archive
VERSION = "1.1.0"
URL_SOURCES = "https://github.com/seb-naudet/birdsong-finder"

# --- Textes du lanceur (la page a sa propre traduction) ---
TEXTES = {
    "fr": {
        "attente": "{nom} démarre… Le premier lancement peut prendre une "
                   "minute.",
        "port": "Le port {port} est déjà utilisé par un autre programme : "
                "{nom} ne peut pas démarrer.",
        "erreur": "{nom} n'a pas pu démarrer.",
        "journal": "Détails dans le journal : {journal}",
    },
    "en": {
        "attente": "{nom} is starting… The first start can take a minute.",
        "port": "Port {port} is already used by another program: {nom} "
                "cannot start.",
        "erreur": "{nom} could not start.",
        "journal": "Details in the log: {journal}",
    },
    "de": {
        "attente": "{nom} startet… Der erste Start kann eine Minute "
                   "dauern.",
        "port": "Port {port} wird bereits von einem anderen Programm "
                "benutzt: {nom} kann nicht starten.",
        "erreur": "{nom} konnte nicht starten.",
        "journal": "Einzelheiten im Protokoll: {journal}",
    },
    "es": {
        "attente": "{nom} se está iniciando… El primer inicio puede tardar "
                   "un minuto.",
        "port": "El puerto {port} ya lo usa otro programa: {nom} no puede "
                "iniciarse.",
        "erreur": "{nom} no ha podido iniciarse.",
        "journal": "Detalles en el registro: {journal}",
    },
    "it": {
        "attente": "{nom} si sta avviando… Il primo avvio può richiedere "
                   "un minuto.",
        "port": "La porta {port} è già usata da un altro programma: {nom} "
                "non può avviarsi.",
        "erreur": "{nom} non è riuscito ad avviarsi.",
        "journal": "Dettagli nel registro: {journal}",
    },
}
LANGUES_WINDOWS = {0x0C: "fr", 0x07: "de", 0x0A: "es", 0x10: "it"}


def langue():
    """Langue de l'interface de Windows (anglais si non traduite)."""
    try:
        lid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        return LANGUES_WINDOWS.get(lid & 0x3FF, "en")
    except Exception:
        return "en"


LANGUE = langue()


def texte(cle, **valeurs):
    return TEXTES[LANGUE][cle].format(nom=NOM, **valeurs)


# --- Windows : boites de message, fenetre au premier plan ---
user32 = ctypes.windll.user32
user32.MessageBoxW.argtypes = (wintypes.HWND, wintypes.LPCWSTR,
                               wintypes.LPCWSTR, wintypes.UINT)
user32.FindWindowW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
user32.FindWindowW.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND,
                                            ctypes.POINTER(wintypes.DWORD))
user32.IsIconic.argtypes = (wintypes.HWND,)
user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
MB_OK, MB_YESNO, MB_ICONERROR, MB_ICONQUESTION = 0x0, 0x4, 0x10, 0x20
MB_DEFBUTTON2, MB_SETFOREGROUND, IDYES = 0x100, 0x10000, 6


def message(texte_, drapeaux=MB_OK | MB_ICONERROR, hwnd=None):
    return user32.MessageBoxW(hwnd, texte_, NOM, drapeaux | MB_SETFOREGROUND)


def ma_fenetre():
    """Poignee de NOTRE fenetre (titre = NOM, meme processus), ou None."""
    hwnd = user32.FindWindowW(None, NOM)
    if not hwnd:
        return None
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return hwnd if pid.value == os.getpid() else None


def premier_plan():
    hwnd = ma_fenetre()
    if hwnd:
        user32.ShowWindow(hwnd, 9 if user32.IsIconic(hwnd) else 5)
        user32.SetForegroundWindow(hwnd)


def webview2_present():
    """Le moteur WebView2 est-il installe ? (Windows 11 : toujours ; Windows
    10 : presque toujours, installe par les mises a jour.)"""
    import winreg
    cle = r"Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    for racine, chemin in (
            (winreg.HKEY_LOCAL_MACHINE, "SOFTWARE\\WOW6432Node\\" + cle),
            (winreg.HKEY_LOCAL_MACHINE, "SOFTWARE\\" + cle),
            (winreg.HKEY_CURRENT_USER, "Software\\" + cle)):
        try:
            with winreg.OpenKey(racine, chemin) as k:
                pv = winreg.QueryValueEx(k, "pv")[0]
        except OSError:
            continue
        if pv and pv != "0.0.0.0":
            return True
    return False


# --- Modes de lancement ---
def boite_dossier(sortie):
    """--choisir-dossier <fichier> : boite Windows de choix de dossier pour
    le serveur en mode navigateur (l'exe n'a pas de « python -c »)."""
    import tkinter as tk
    from tkinter import filedialog
    r = tk.Tk()
    r.withdraw()
    r.attributes("-topmost", True)
    d = filedialog.askdirectory(parent=r)
    with open(sortie, "w", encoding="utf-8") as f:
        f.write(d or "")


def journal_fichier(srv):
    """Exe : sorties et erreurs TOUJOURS vers data\\journal.txt (refait a
    chaque lancement), qu'il ait ou non recu des sorties de son lanceur.
    Depuis les sources (python.exe et sa console), rien ne change."""
    if not getattr(sys, "frozen", False) and \
            sys.stdout is not None and sys.stderr is not None:
        return None
    os.makedirs(srv.DONNEES, exist_ok=True)
    chemin = os.path.join(srv.DONNEES, "journal.txt")
    f = open(chemin, "w", encoding="utf-8", errors="replace", buffering=1)
    sys.stdout = sys.stderr = f
    return chemin


def deja_lance(srv, url):
    """Le port est pris : notre programme (fenetre ramenee devant), le
    serveur du .bat (page ouverte dans le navigateur) ou un intrus."""
    try:
        with urllib.request.urlopen(url + "api/config", timeout=5) as r:
            conf = json.load(r)
    except Exception:
        conf = None
    if not isinstance(conf, dict) or "pays" not in conf:
        message(texte("port", port=srv.PORT))
        return
    if (conf.get("appli") or {}).get("fenetre"):
        # Nous sommes au premier plan (lances par l'utilisateur) : on cede
        # ce droit a l'instance deja ouverte pour qu'elle passe devant.
        user32.AllowSetForegroundWindow(-1)
        requete = urllib.request.Request(url + "api/montrer", data=b"{}",
                                         method="POST")
        try:
            urllib.request.urlopen(requete, timeout=5).close()
        except Exception:
            pass
    else:
        webbrowser.open(url)


def mode_navigateur(srv, url):
    serveur = srv.demarrer()
    webbrowser.open(url)
    serveur.serve_forever()


def page_simple(titre, *paragraphes):
    import html
    corps = "".join("<p>%s</p>" % html.escape(p) for p in paragraphes)
    return ("<!DOCTYPE html><html lang='%s'><head><meta charset='utf-8'>"
            "<title>%s</title><style>body{font-family:system-ui,sans-serif;"
            "margin:2em;font-size:1.2em;line-height:1.5}</style></head>"
            "<body><h1>%s</h1>%s</body></html>"
            % (LANGUE, html.escape(titre), html.escape(titre), corps))


def mode_fenetre(srv, url, journal):
    import webview

    fenetre = webview.create_window(
        NOM, html=page_simple(NOM, texte("attente")), width=1100, height=850,
        min_size=(640, 480), text_select=True)

    def focus_page():
        """Rend le focus clavier a la page apres une boite de dialogue :
        sinon il reste sur le cadre de la fenetre et NVDA n'est plus dans
        la page (vu le 2026-11-24 apres « Annuler » du choix de dossier).
        La page retrouve alors l'element qui avait le focus."""
        try:
            from System import Action           # pythonnet (WinForms)
            vue = fenetre.native
            vue.Invoke(Action(lambda: vue.browser.webview.Focus()))
        except Exception:
            traceback.print_exc()

    def choisir_dossier():
        try:
            choix = fenetre.create_file_dialog(webview.FileDialog.FOLDER)
        finally:
            focus_page()
        return choix[0] if choix else None

    def quitter():
        fenetre.destroy()

    def montrer():
        premier_plan()
        focus_page()

    srv.CHOISIR_DOSSIER = choisir_dossier
    srv.QUITTER = quitter
    srv.MONTRER = montrer

    # Croix, Alt+F4, bouton Quitter : AUCUNE confirmation, meme pendant une
    # analyse (demande de l'auteur, 2026-11-24) : la fenetre se ferme tout de
    # suite et l'analyse en cours est interrompue proprement juste apres
    # (voir la fin de mode_fenetre) ; le fichier repart au prochain lancement.

    def demarrage():
        try:
            serveur = srv.demarrer()
        except Exception as e:
            traceback.print_exc()
            details = [texte("erreur"), str(e)]
            if journal:
                details.append(texte("journal", journal=journal))
            fenetre.load_html(page_simple(NOM, *details))
            return
        threading.Thread(target=serveur.serve_forever, daemon=True).start()
        fenetre.load_url(url)

    # private_mode=False + dossier a nous : la page garde ses choix (langue
    # des noms d'oiseaux, duree des extraits) d'un lancement a l'autre.
    webview.start(demarrage, private_mode=False,
                  storage_path=os.path.join(srv.DONNEES, "webview"))
    # Fenetre fermee : on laisse a l'analyse en cours quelques secondes pour
    # s'interrompre (ffmpeg tue, aucun sidecar partiel), puis le processus
    # se termine.
    srv.arreter_analyse()


def main():
    args = sys.argv[1:]
    if args[:1] == ["--choisir-dossier"]:
        boite_dossier(args[1])
        return
    if getattr(sys, "frozen", False):
        # Lance par un raccourci, le dossier courant peut etre n'importe ou.
        os.chdir(os.path.dirname(os.path.abspath(sys.executable)))
    import birdnet_serveur as srv       # leger : calcul charge au demarrage
    url = "http://127.0.0.1:%d/" % srv.PORT
    if srv.port_occupe():
        deja_lance(srv, url)
        return
    journal = journal_fichier(srv)
    fenetre = "--navigateur" not in args and webview2_present()
    srv.APPLI = {"nom": NOM, "version": VERSION, "sources": URL_SOURCES,
                 "fenetre": fenetre}
    try:
        if fenetre:
            mode_fenetre(srv, url, journal)
        else:
            mode_navigateur(srv, url)
    except Exception:
        traceback.print_exc()
        message(texte("erreur") + "\n\n" + traceback.format_exc(limit=3)
                + ("\n" + texte("journal", journal=journal) if journal
                   else ""))
    finally:
        try:
            srv.arreter_local()          # coupe un extrait en cours
        except Exception:
            pass
        # Fenetre fermee : analyse et veille (fils de fond) s'arretent net ;
        # le prochain lancement reprendra ce qui n'etait pas fini.
        os._exit(0)


if __name__ == "__main__":
    main()

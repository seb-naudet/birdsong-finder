"""Construit le programme Windows portable (PyInstaller, dossier « onedir »).

    exe\\venv\\Scripts\\python.exe construire_exe.py      (ou double-clic sur construire_exe.bat)

1. ffmpeg LGPL (BtbN, version figee, empreinte SHA-256 verifiee) -> exe/ffmpeg/ ;
   textes de licence a telecharger (Python, SDK WebView2) -> exe/telechargements/ ;
2. icone (dessinee ici avec Pillow) et fiche de version de l'exe -> exe/build/ ;
3. PyInstaller -> exe/dist/<NOM_FICHIER>/ (<NOM_FICHIER>.exe + _internal) ;
4. documents : README.txt, LISEZMOI.txt, LICENSE.txt, et LICENSES.txt GENERE
   (chaque composant embarque, sa version, sa licence et son texte complet) ;
5. archive exe/<NOM_FICHIER>-<VERSION>-windows-x64.zip (a publier).

Rien n'est ecrit hors du dossier du projet ; tout le construit est ignore par
git (exe/.gitignore).
"""
import ast
import hashlib
import importlib.metadata as md
import importlib.util
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile

ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ICI)
from birdnet_app import NOM, NOM_FICHIER, URL_SOURCES, VERSION  # noqa: E402

EXE = os.path.join(ICI, "exe")
BUILD = os.path.join(EXE, "build")
DIST_RACINE = os.path.join(EXE, "dist")
DIST = os.path.join(DIST_RACINE, NOM_FICHIER)
TELE = os.path.join(EXE, "telechargements")
FFMPEG_DIR = os.path.join(EXE, "ffmpeg")
ARCHIVE = os.path.join(EXE, "%s-%s-windows-x64.zip" % (NOM_FICHIER, VERSION))
ANNEE = "2026"

# ffmpeg : construction LGPL (aucun composant GPL) de BtbN/FFmpeg-Builds,
# version FIGEE (le lien "latest" change chaque jour). Sources exactes :
# commit FFmpeg ci-dessous + scripts de construction au meme tag.
FFMPEG_TAG = "autobuild-2026-11-24-12-41"
FFMPEG_ZIP = "ffmpeg-n8.1.3-41-ga8029b6528-win64-lgpl-shared-8.1.zip"
FFMPEG_URL = ("https://github.com/BtbN/FFmpeg-Builds/releases/download/%s/%s"
              % (FFMPEG_TAG, FFMPEG_ZIP))
FFMPEG_SHA256 = "dbed3e64d68de76472bf2cd0b0f3250384fb96bac14b635a708e998f6e1d6996"
FFMPEG_COMMIT = "a8029b6528"
FFMPEG_GARDER = ("ffmpeg.exe", "ffprobe.exe", "avcodec-62.dll",
                 "avdevice-62.dll", "avfilter-11.dll", "avformat-62.dll",
                 "avutil-60.dll", "swresample-6.dll", "swscale-9.dll")

# Textes de licence absents des paquets installes.
WEBVIEW2_SDK = "1.0.3856.49"         # version des DLL livrees par pywebview
LICENCES_WEB = {
    "python-LICENSE.txt":
        "https://raw.githubusercontent.com/python/cpython/v3.11.9/LICENSE",
    "python-license.rst":
        "https://raw.githubusercontent.com/python/cpython/v3.11.9/Doc/license.rst",
    "webview2-sdk.nupkg":
        "https://www.nuget.org/api/v2/package/Microsoft.Web.WebView2/"
        + WEBVIEW2_SDK,
}

# birdnetlib tire ces modules, inutiles ici (voir charger_birdnetlib dans
# birdnet_serveur.py) : ~230 Mo de moins. Le reste ne sert qu'a construire.
EXCLURE = ["librosa", "numba", "llvmlite", "sklearn", "matplotlib", "PIL",
           "pooch", "soxr", "joblib", "lazy_loader", "msgpack", "IPython",
           "pandas", "tensorflow", "pytest", "PyInstaller", "pip",
           "setuptools", "pkg_resources", "watchdog"]


def etape(texte):
    print("\n=== " + texte, flush=True)


def telecharger(url, chemin, sha256=None):
    if not os.path.exists(chemin):
        print("telechargement : " + url, flush=True)
        os.makedirs(os.path.dirname(chemin), exist_ok=True)
        requete = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(requete, timeout=120) as r, \
                open(chemin + ".part", "wb") as f:
            shutil.copyfileobj(r, f)
        os.replace(chemin + ".part", chemin)
    if sha256:
        h = hashlib.sha256()
        with open(chemin, "rb") as f:
            for bloc in iter(lambda: f.read(1 << 20), b""):
                h.update(bloc)
        if h.hexdigest() != sha256:
            sys.exit("EMPREINTE INATTENDUE pour %s : %s" % (chemin, h.hexdigest()))
    return chemin


def ffmpeg():
    """ffmpeg.exe, ffprobe.exe et leurs DLL dans exe/ffmpeg/ (+ licence)."""
    if all(os.path.exists(os.path.join(FFMPEG_DIR, f)) for f in FFMPEG_GARDER):
        return
    zip_ = telecharger(FFMPEG_URL, os.path.join(TELE, FFMPEG_ZIP), FFMPEG_SHA256)
    os.makedirs(FFMPEG_DIR, exist_ok=True)
    with zipfile.ZipFile(zip_) as z:
        for nom in z.namelist():
            base = nom.rsplit("/", 1)[-1]
            if base in FFMPEG_GARDER or nom.endswith("/LICENSE.txt"):
                with z.open(nom) as src, open(os.path.join(FFMPEG_DIR, base), "wb") as dst:
                    shutil.copyfileobj(src, dst)


def icone():
    """Oiseau blanc qui chante, sur disque vert. ICO multi-tailles."""
    from PIL import Image, ImageDraw
    t = 1024                                   # dessin en grand, puis reduit
    img = Image.new("RGBA", (t, t), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = t / 256.0

    def S(*pts):
        return [v * s for v in pts]

    vert, blanc, aile = (31, 111, 80, 255), (255, 255, 255, 255), (214, 232, 222, 255)
    orange, jaune = (255, 167, 38, 255), (255, 213, 79, 255)
    d.ellipse(S(8, 8, 248, 248), fill=vert)
    d.polygon(S(58, 150, 14, 178, 24, 194, 74, 172), fill=blanc)           # queue
    d.ellipse(S(44, 104, 160, 190), fill=blanc)                            # corps
    d.ellipse(S(118, 66, 182, 130), fill=blanc)                            # tete
    d.ellipse(S(66, 124, 136, 168), fill=aile)                             # aile
    d.polygon(S(178, 88, 212, 98, 178, 108), fill=orange)                  # bec
    d.ellipse(S(152, 86, 164, 98), fill=vert)                              # oeil
    d.line(S(92, 186, 88, 214), fill=orange, width=int(6 * s))            # pattes
    d.line(S(116, 186, 120, 214), fill=orange, width=int(6 * s))
    for r in (16, 30):                                                     # chant
        d.arc(S(212 - r, 98 - r, 212 + r, 98 + r), start=-45, end=45,
              fill=jaune, width=int(7 * s))
    chemin = os.path.join(BUILD, "icone.ico")
    os.makedirs(BUILD, exist_ok=True)
    img.resize((256, 256), Image.LANCZOS).save(
        chemin, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
                       (128, 128), (256, 256)])
    img.resize((256, 256), Image.LANCZOS).save(os.path.join(BUILD, "icone.png"))
    return chemin


def fiche_version():
    """Proprietes de l'exe (nom, version, editeur) vues par Windows."""
    v = tuple(int(x) for x in VERSION.split(".")) + (0,)
    texte = """VSVersionInfo(
  ffi=FixedFileInfo(filevers=%(v)r, prodvers=%(v)r, mask=0x3f, flags=0x0,
                    OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'seb-naudet'),
      StringStruct('FileDescription', %(nom)r),
      StringStruct('FileVersion', %(version)r),
      StringStruct('InternalName', %(fichier)r),
      StringStruct('LegalCopyright', %(copy)r),
      StringStruct('OriginalFilename', %(exe)r),
      StringStruct('ProductName', %(nom)r),
      StringStruct('ProductVersion', %(version)r)])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""" % {"v": v[:4], "nom": NOM, "version": VERSION, "fichier": NOM_FICHIER,
       "exe": NOM_FICHIER + ".exe",
       "copy": "Copyright (c) %s seb-naudet - MIT licence" % ANNEE}
    chemin = os.path.join(BUILD, "version.txt")
    with open(chemin, "w", encoding="utf-8") as f:
        f.write(texte)
    return chemin


def dossier_paquet(nom):
    return list(importlib.util.find_spec(nom).submodule_search_locations)[0]


def pyinstaller(ico, version):
    sep = os.pathsep
    modeles = os.path.join(dossier_paquet("birdnetlib"), "models", "analyzer")
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
           "--windowed", "--name", NOM_FICHIER, "--icon", ico,
           "--version-file", version,
           "--distpath", DIST_RACINE, "--workpath", os.path.join(BUILD, "pyi"),
           "--specpath", BUILD, "--paths", ICI,
           "--hidden-import", "birdnet_serveur",
           "--hidden-import", "tflite_runtime.interpreter",
           # modele BirdNET (analyse + filtre lieu/saison) et ses noms
           "--add-data", modeles + sep + "birdnetlib/models/analyzer",
           "--add-data", os.path.join(ICI, "labels") + sep + "labels",
           "--add-data", os.path.join(ICI, "birdnet.html") + sep + "."]
    for m in EXCLURE:
        cmd += ["--exclude-module", m]
    cmd.append(os.path.join(ICI, "birdnet_app.py"))
    subprocess.run(cmd, check=True, cwd=ICI)
    # ffmpeg copie APRES PyInstaller, dans _internal/bin : donne a PyInstaller
    # (meme en --add-data), il suit les DLL dont ffmpeg.exe depend et les
    # recopie AUSSI a la racine de _internal (+160 Mo, vu le 2026-11-24).
    bin_ = os.path.join(DIST, "_internal", "bin")
    os.makedirs(bin_, exist_ok=True)
    for f in FFMPEG_GARDER:
        shutil.copy2(os.path.join(FFMPEG_DIR, f), os.path.join(bin_, f))


# --- LICENSES.txt : genere depuis ce qui est REELLEMENT dans l'exe ---------
def modules_embarques():
    """Noms de premier niveau des modules et fichiers places dans l'exe,
    lus dans les tables de PyInstaller (PYZ = modules Python, COLLECT =
    binaires et donnees)."""
    pyi = os.path.join(BUILD, "pyi", NOM_FICHIER)
    noms = set()

    def listes(objet):
        """Les listes de triplets (nom, chemin, type) dans une table."""
        if isinstance(objet, list) and objet and all(
                isinstance(e, tuple) and len(e) == 3 for e in objet):
            yield objet
        elif isinstance(objet, (list, tuple)):
            for o in objet:
                yield from listes(o)

    for toc in ("PYZ-00.toc", "COLLECT-00.toc"):
        with open(os.path.join(pyi, toc), encoding="utf-8") as f:
            contenu = ast.literal_eval(f.read())
        for liste in listes(contenu):
            for e in liste:
                nom = e[0].replace("\\", "/")
                noms.add(nom.split(".")[0] if toc.startswith("PYZ")
                         else nom.split("/")[0].split(".")[0])
    return noms


def licence_de(dist):
    m = dist.metadata
    if m.get("License-Expression"):
        return m["License-Expression"]
    classes = [c.split(" :: ")[-1] for c in (m.get_all("Classifier") or [])
               if c.startswith("License ::")]
    if classes:
        return ", ".join(classes)
    lic = (m.get("License") or "").strip()
    return lic.splitlines()[0][:80] if lic else "see the text below"


def textes_licence(dist):
    textes = []
    for f in dist.files or []:
        base = f.name.upper()
        if any(base.startswith(p) for p in ("LICENSE", "LICENCE", "COPYING", "NOTICE")) \
                and ".dist-info" in str(f).replace("\\", "/"):
            try:
                textes.append((str(f), f.read_text(encoding="utf-8")))
            except (OSError, UnicodeDecodeError):
                pass
    return textes


def lire(chemin):
    with open(chemin, encoding="utf-8", errors="replace") as f:
        return f.read().strip()


def licences():
    """Ecrit LICENSES.txt dans le dossier construit : composants hors Python
    (modele BirdNET, ffmpeg, Python, PyInstaller, WebView2) puis chaque
    paquet Python embarque, avec le texte complet de sa licence."""
    for nom, url in LICENCES_WEB.items():
        telecharger(url, os.path.join(TELE, nom))
    with zipfile.ZipFile(os.path.join(TELE, "webview2-sdk.nupkg")) as z:
        webview2 = z.read("LICENSE.txt").decode("utf-8", "replace").strip()
    birdnetlib = dossier_paquet("birdnetlib")
    cc = lire(os.path.join(birdnetlib, "models", "analyzer", "LICENSE"))
    import soundfile                              # chemin de libsndfile
    sndfile = lire(os.path.join(os.path.dirname(soundfile.__file__),
                                "_soundfile_data", "COPYING"))

    trait = "=" * 78
    parties = []
    resume = []

    def composant(titre, licence, lignes, texte=None):
        resume.append("- %s: %s" % (titre, licence))
        bloc = [trait, titre, "Licence: " + licence] + list(lignes)
        if texte:
            bloc += ["", texte]
        parties.append("\n".join(bloc))

    composant(
        "BirdNET GLOBAL 6K V2.4 (analysis model, location/season filter "
        "model, translated species names)", "CC BY-NC-SA 4.0 (NON-COMMERCIAL use only)",
        ["Authors: K. Lisa Yang Center for Conservation Bioacoustics, Cornell "
         "Lab of Ornithology, and Chemnitz University of Technology.",
         "Source: https://github.com/birdnet-team/BirdNET-Analyzer",
         "The model is shipped as is, unmodified. Because its licence is "
         "non-commercial, this program may not be sold.",
         "Requested citation: Kahl, S., Wood, C. M., Eibl, M., & Klinck, H. "
         "(2021). BirdNET: A deep learning solution for avian diversity "
         "monitoring. Ecological Informatics, 61, 101236."], cc)
    composant(
        "FFmpeg %s (ffmpeg.exe, ffprobe.exe and their DLLs, folder _internal/bin)"
        % FFMPEG_ZIP.split("-win64")[0].replace("ffmpeg-", ""),
        "LGPL 3.0 or later (LGPL build: no GPL component)",
        ["Build: https://github.com/BtbN/FFmpeg-Builds (tag %s)" % FFMPEG_TAG,
         "Exact sources: https://github.com/FFmpeg/FFmpeg/commit/%s ; build "
         "scripts and library versions: "
         "https://github.com/BtbN/FFmpeg-Builds/tree/%s" % (FFMPEG_COMMIT, FFMPEG_TAG),
         "The DLLs are separate files: they can be replaced by another "
         "compatible build."],
        lire(os.path.join(FFMPEG_DIR, "LICENSE.txt")))
    composant(
        "libsndfile (DLL shipped with the soundfile package)",
        "LGPL 2.1 or later",
        ["Source: https://github.com/libsndfile/libsndfile ; build: "
         "https://github.com/bastibe/libsndfile-binaries",
         "Separate DLL (_internal/_soundfile_data), replaceable."], sndfile)
    composant(
        "Python %d.%d.%d (interpreter and standard library, including Tcl/Tk "
        "for tkinter)" % sys.version_info[:3],
        "PSF License Agreement (+ licences of incorporated software)",
        ["Source: https://www.python.org/ ; incorporated software (OpenSSL, "
         "libffi, SQLite, Tcl/Tk, zlib...): see the second text below ; "
         "Tcl/Tk: _internal/_tcl_data/license.terms and _tk_data/license.terms."],
        lire(os.path.join(TELE, "python-LICENSE.txt")) + "\n\n"
        + lire(os.path.join(TELE, "python-license.rst")))
    composant(
        "PyInstaller %s (bootloader of the exe)" % md.version("pyinstaller"),
        "GPL 2.0 or later WITH the bootloader exception",
        ["The exception allows distributing the built program under any "
         "licence. Source: https://github.com/pyinstaller/pyinstaller"])
    composant(
        "Microsoft WebView2 SDK %s (Microsoft.Web.WebView2.Core.dll, "
        "Microsoft.Web.WebView2.WinForms.dll, WebView2Loader.dll)" % WEBVIEW2_SDK,
        "BSD 3-Clause (Microsoft)",
        ["Shipped with pywebview. The WebView2 engine itself is part of "
         "Windows and is NOT included here."], webview2)
    composant(
        "Microsoft Visual C++ Runtime and Universal CRT (VCRUNTIME140.dll, "
        "MSVCP140.dll, api-ms-win-*.dll)",
        "Microsoft redistributable files",
        ["Shipped with Python for Windows, under Microsoft's redistribution "
         "terms (Visual Studio, Windows SDK)."])

    ignores = {"birdnet_serveur", "birdnet_app", "tflite_runtime"}
    tables = md.packages_distributions()
    dists = {}
    for nom in sorted(modules_embarques()):
        for dist in tables.get(nom, []):
            dists.setdefault(dist.lower(), dist)
    for cle in sorted(dists):
        if cle in ("pyinstaller", "pyinstaller-hooks-contrib") or cle in ignores:
            continue
        dist = md.distribution(dists[cle])
        url = dist.metadata.get("Home-page") or ""
        for u in dist.metadata.get_all("Project-URL") or []:
            if not url and ("source" in u.lower() or "home" in u.lower()):
                url = u.split(",", 1)[-1].strip()
        textes = textes_licence(dist)
        composant("Python package %s %s" % (dist.metadata["Name"], dist.version),
                  licence_de(dist), ["Source: " + url] if url else [],
                  "\n\n".join(t for _, t in textes) or None)

    entete = [
        "%s %s - components and licences" % (NOM, VERSION),
        "",
        "The code of %s is under the MIT licence (file LICENSE.txt)." % NOM,
        "Source code: " + URL_SOURCES,
        "",
        "The program includes the following components, each under its own",
        "licence (full text of each one below):",
        ""] + resume + [""]
    with open(os.path.join(DIST, "LICENSES.txt"), "w", encoding="utf-8",
              newline="\r\n") as f:
        f.write("\n".join(entete) + "\n" + "\n\n".join(parties) + "\n")
    return len(resume)


def documents():
    """Modes d'emploi et licence, avec le nom, la version et l'adresse des
    sources du programme (marques @NOM@, @EXE@, @VERSION@, @SOURCES@), en
    fins de ligne Windows."""
    for nom in ("README.txt", "LISEZMOI.txt", "LICENSE.txt"):
        source = os.path.join(EXE, nom)
        if not os.path.exists(source):
            print("ATTENTION : %s manque (relancer avec --docs)" % nom)
            continue
        texte = lire(source)
        for marque, valeur in (("@NOM@", NOM), ("@EXE@", NOM_FICHIER),
                               ("@VERSION@", VERSION), ("@SOURCES@", URL_SOURCES)):
            texte = texte.replace(marque, valeur)
        with open(os.path.join(DIST, nom), "w", encoding="utf-8",
                  newline="\r\n") as f:
            f.write(texte + "\n")


def archive():
    with zipfile.ZipFile(ARCHIVE + ".part", "w", zipfile.ZIP_DEFLATED,
                         compresslevel=9) as z:
        for racine, _dossiers, fichiers in os.walk(DIST):
            for f in fichiers:
                chemin = os.path.join(racine, f)
                z.write(chemin, os.path.join(NOM_FICHIER, os.path.relpath(chemin, DIST)))
    os.replace(ARCHIVE + ".part", ARCHIVE)
    return os.path.getsize(ARCHIVE)


def taille(d):
    return sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(d) for f in fs)


if __name__ == "__main__":
    if "--docs" not in sys.argv:        # --docs : documents + archive seulement
        etape("ffmpeg LGPL (%s)" % FFMPEG_TAG)
        ffmpeg()
        etape("icone et fiche de version")
        ico, version = icone(), fiche_version()
        etape("PyInstaller : %s %s" % (NOM, VERSION))
        pyinstaller(ico, version)
    etape("documents et licences")
    documents()
    n = licences()
    etape("archive a publier")
    octets = archive()
    print("\nPret : %s" % DIST)
    print("Dossier : %.0f Mo ; %d composants dans LICENSES.txt ; archive %s : %.0f Mo"
          % (taille(DIST) / 1e6, n, os.path.basename(ARCHIVE), octets / 1e6))

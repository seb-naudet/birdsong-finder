"""Interpreteur TFLite fourni par ai-edge-litert (voir __init__.py).

PIEGE MESURE : birdnetlib cree son interpreteur avec `num_threads=1` en dur
(valeur par defaut de BirdNET-Analyzer). TensorFlow ignorait ce reglage et
parallelisait quand meme ; LiteRT, lui, obeit — l'analyse devenait 4 fois
plus LENTE (23,7 s contre 6,1 s sur 152 s d'audio). On remet donc le nombre
de coeurs quand l'appelant n'a rien demande de particulier : LiteRT repasse
alors devant TensorFlow (4,97 s).
"""
import os

from ai_edge_litert.interpreter import *            # noqa: F401,F403
from ai_edge_litert.interpreter import (             # noqa: F401
    Interpreter as _Interpreter, load_delegate)

# Plafond a 8 : au-dela le gain est negligeable sur ce modele, et on laisse
# de la place au reste de la machine (decodage ffmpeg, filtrage, serveur).
COEURS = max(1, min(8, os.cpu_count() or 2))


def Interpreter(*args, **kwargs):
    """Comme ai_edge_litert.Interpreter, mais multi-coeurs par defaut."""
    if kwargs.get("num_threads", 1) in (None, 1):
        kwargs["num_threads"] = COEURS
    return _Interpreter(*args, **kwargs)

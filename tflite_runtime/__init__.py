"""Passerelle vers LiteRT — remplace TensorFlow pour l'analyse BirdNET.

birdnetlib essaie d'importer `tflite_runtime` AVANT de se rabattre sur
`tensorflow.lite`. Or le paquet officiel `tflite-runtime` n'existe PAS pour
Windows (uniquement Linux), d'ou l'usage historique de TensorFlow ici : 2 Go
de dependances pour n'utiliser que son interpreteur TFLite.

`ai-edge-litert` est le successeur officiel de tflite-runtime, et lui EXISTE
pour Windows (et pour Python 3.10 a 3.14). Ce petit module se fait passer
pour `tflite_runtime` et renvoie vers lui : meme modele, meme noyau XNNPACK,
donc EXACTEMENT les memes detections (verifie au millionieme), mais un
chargement bien plus rapide et un environnement 3,5 fois plus leger.

Ce dossier est place a cote de birdnet_serveur.py : Python ajoute le dossier
du script en tete du chemin d'import, il est donc trouve sans installation.

Trappe de secours : avec la variable d'environnement BIRDNET_MOTEUR=tensorflow
ce module refuse de se charger, et birdnetlib se rabat sur tensorflow.lite
(si TensorFlow est installe). Sert a comparer les deux moteurs, ou a depanner.
"""
import os

if os.environ.get("BIRDNET_MOTEUR", "").lower() == "tensorflow":
    raise ImportError("moteur TensorFlow demande par BIRDNET_MOTEUR")

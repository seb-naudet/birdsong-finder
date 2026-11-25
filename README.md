# Birdsong Finder

**Birdsong Finder** is a free Windows program that identifies bird species in your audio and video recordings, and live from an audio input. It uses the BirdNET model from the Cornell Lab of Ornithology and Chemnitz University of Technology.

It was designed by and for screen reader users (NVDA, JAWS, Narrator). Everything runs locally, on your own computer: nothing is uploaded, and no Internet connection is needed.

Birdsong Finder is an independent project. It is not affiliated with or endorsed by the BirdNET team.

## Download

1. Download the ZIP file of the [latest release](https://github.com/seb-naudet/birdsong-finder/releases/latest) (Windows 10 or 11, 64-bit).
2. Unzip it, for example into Documents. There is nothing to install.
3. Open the `BirdsongFinder` folder and start `BirdsongFinder.exe`.

The program is not signed with a paid certificate, so the first time Windows may say "Windows protected your PC". Choose "More info", then "Run anyway".

## What it does

- Analyses the recordings you put in its `audio` folder, and any other folder you add. New files are analysed automatically. You can also copy or cut files and folders in Explorer and paste them into the program with Ctrl+V: they go into the `audio` folder and their existing analyses follow them.
- Accepts audio files (wav, flac, mp3, ogg, opus, m4a, aac, aiff, wma) and videos (mp4, mov, mkv, avi, webm and more). For videos, only the sound track is analysed.
- Lists the species heard in each recording, in the order you chose: as they appear, from most to least confident, or the reverse. Each passage has a button that plays the excerpt around the song. Space plays, Ctrl+Space pauses or resumes, Shift+Space stops.
- Search: type one or more species names and get every passage where they appear, across all your recordings, ready to play.
- For stereo recordings, analyses the left and right channels separately and tells you where each bird is: left, centre left, centre, centre right or right. Four-channel ambisonic (AmbiX) recordings are analysed through six virtual microphones.
- Live analysis: listens to an audio input of the computer (microphone, audio interface, "Stereo Mix" or a virtual cable) and names the species as they sing. The list of species heard grows as they are detected; each one can be announced automatically by the screen reader, and written to a text file.
- Shows bird names in the 35 BirdNET languages. The window itself is in English, French, German, Spanish or Italian, following Windows.
- Uses your country, or your own coordinates, and the season to keep only the species that are plausible there.

## With a screen reader

The window shows an accessible web page, so NVDA and JAWS work in browse mode, as in Chrome or Edge.

- Headings are organised by level: level 2 for sections, level 3 for recordings, level 4 for species.
- Each play button gives the full context, for example "Play Common Blackbird - at 2 min 14 s - 78% - centre left".
- The lists never reorganise themselves while you are reading them.
- A sound plays when all analyses are finished.
- In live analysis, the automatic announcement of each new species is optional and off by default.

The full user guide is in [README.txt](exe/README.txt), and in French in [LISEZMOI.txt](exe/LISEZMOI.txt).

## Licences

- **Code:** MIT licence (see [LICENSE](LICENSE)).
- **BirdNET model** (BirdNET GLOBAL 6K V2.4, shipped unmodified) **and its translated species names** (the `labels` folder of this repository): [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/), which allows **non-commercial use only**. As a result, this program may be shared freely but not sold. If you use BirdNET in research, please cite:
  Kahl, S., Wood, C. M., Eibl, M., & Klinck, H. (2021). BirdNET: A deep learning solution for avian diversity monitoring. *Ecological Informatics*, 61, 101236.
- **Bundled components:** FFmpeg (an LGPL build by [BtbN](https://github.com/BtbN/FFmpeg-Builds), pinned and checked by SHA-256), Python, NumPy, SciPy, soundfile/libsndfile, birdnetlib, LiteRT, pywebview, the Microsoft WebView2 SDK and others. The `LICENSES.txt` file in each release lists every component with its version and full licence text. The build script generates it from what is actually bundled.

## Building from source

You need Windows and Python 3.11 (`py -3.11`).

1. Double-click `construire_exe.bat`. The first time, it creates the build environment `exe\venv` from `exe\requirements.txt`, which pins every version.
2. The script downloads the pinned FFmpeg build, then builds the program with PyInstaller into `exe\dist\BirdsongFinder\`. It also makes the ZIP to publish, in `exe\`.

To run from source without building: `exe\venv\Scripts\python birdnet_app.py` opens the program window, and `python birdnet_serveur.py` starts the server alone, for use in a browser at http://127.0.0.1:8790/.

How it works: `birdnet_serveur.py` is a small local HTTP server that runs the analysis and serves the page `birdnet.html`. `birdnet_app.py` shows that page in its own window with WebView2, the web engine built into Windows. The `tflite_runtime` folder is a small bridge that lets birdnetlib use LiteRT instead of TensorFlow, which gives the same detections with a much lighter install. Source comments are in French.

## Credits and contact

Designed and written by Seb, Chris and Claude (Anthropic's Claude Code), 2026. Thanks to the BirdNET team, to Joe Weiss for birdnetlib, and to all the open source projects listed in `LICENSES.txt`.

Contact: sebno42@gmail.com. Bug reports and suggestions are also welcome in the [issues](https://github.com/seb-naudet/birdsong-finder/issues).

## En français

**Birdsong Finder** reconnaît les espèces d'oiseaux dans vos enregistrements audio et vidéo, et en direct depuis une entrée audio, grâce au modèle BirdNET. Il a été conçu pour les lecteurs d'écran (NVDA, JAWS, Narrateur) et tout se passe en local sur votre ordinateur. Pour l'installer, téléchargez le ZIP de la [dernière version](https://github.com/seb-naudet/birdsong-finder/releases/latest), décompressez-le et lancez `BirdsongFinder.exe`. Le mode d'emploi complet est dans [LISEZMOI.txt](exe/LISEZMOI.txt).

Le programme est gratuit et son code est sous licence MIT. Le modèle BirdNET est sous licence CC BY-NC-SA 4.0 et n'est permis qu'en usage non commercial : le programme peut donc être partagé, mais pas vendu.

Contact : sebno42@gmail.com

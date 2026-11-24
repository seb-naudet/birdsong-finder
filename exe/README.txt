=====================================================================
 @NOM@ @VERSION@ - identify the birds singing in your recordings
 (Version francaise : voir LISEZMOI.txt)
=====================================================================

@NOM@ is a free Windows program that identifies birds in your audio
and video recordings with BirdNET, the bird sound recognition model of
the Cornell Lab of Ornithology and Chemnitz University of Technology.
It was designed for screen reader users (NVDA, JAWS, Narrator) and is
just as usable by everyone.

Everything runs on your own computer: nothing is uploaded, and no
Internet connection is needed.

Not affiliated with or endorsed by the BirdNET team.

---------------------------------------------------------------------
 GETTING STARTED
---------------------------------------------------------------------
1. Unzip the archive wherever you like, for example in Documents.
   Nothing to install. Avoid the "Program Files" folder.

2. Open the @EXE@ folder and start  @EXE@.exe
   The FIRST time, Windows may say "Windows protected your PC" (the
   program is not signed by a paid certificate): choose "More info",
   then "Run anyway". The first start can also take up to a minute.

3. The program window opens. Put your recordings in the  audio
   folder next to the program (subfolders included), or add any
   folder of your computer in the "Analysed folders" section of the
   window. Analysis starts by itself; a sound announces when all
   files are done.

4. In the list of recordings, "Show species" lists the birds heard,
   in the order you hear them in the recording, with one button per
   passage to listen to the excerpt around the song.

To close the program: close its window (Alt+F4) or use the "Quit the app"
button. If an analysis is running, the program asks for
confirmation; an unfinished analysis starts again at the next launch.
Starting the program again while it is open brings its window back.

Accepted formats: wav, flac, mp3, ogg, opus, m4a, aac, aiff, wma, and
videos mp4, m4v, mov, mkv, avi, webm, wmv, mpg, mpeg, 3gp, mts, m2ts
(the sound track is extracted automatically).

Stereo files: left and right channels are analysed separately (more
species found). Four-channel ambisonic files (AmbiX): six "virtual
microphones" are analysed. In both cases the direction of each bird
is given on a left-right scale (left, centre left, centre, centre
right, right, plus "above" for ambisonic files).

---------------------------------------------------------------------
 SETTINGS (in the window)
---------------------------------------------------------------------
- Country: BirdNET keeps only the species plausible where you
  recorded. Latitude and longitude: the centre of the country by
  default, enter your own to refine.
- Season: the date of each file by default, or a fixed day and month.
- Language of bird names: the 35 BirdNET languages.
- The window itself follows the language of Windows (English, French,
  German, Spanish, Italian; English otherwise).
- Length of the played excerpts: 3, 6, 10 or 15 seconds.

Results are stored in a small "file-name.birds.json" file next to
each recording: if you move a recording, move its .birds.json too and
the analysis follows.

---------------------------------------------------------------------
 SCREEN READERS
---------------------------------------------------------------------
The window shows a web page: NVDA and JAWS use browse mode, as in a
browser.
- Headings: level 2 = sections, level 3 = recordings, level 4 =
  species. A list of links at the top of the recordings jumps
  straight to one of them.
- Each listening button says everything: "Play Common Blackbird - at
  2 min 14 s - 78% - centre left". Space bar or Enter plays it.
- The list never reorganises itself while you are reading it.

---------------------------------------------------------------------
 WHERE ARE MY FILES?
---------------------------------------------------------------------
Everything stays in the program folder:
- audio\  ........ your recordings (and their .birds.json results)
- data\   ........ settings, log (journal.txt), temporary files and
                   the window's cache
Unzipped in a read-only folder, the program uses
%LocalAppData%\@EXE@ instead.

To uninstall: close the program and delete its folder (move your
recordings out of the audio folder first if you want to keep them).
Nothing is written in the registry or elsewhere. Folders that you
added in the window keep their .birds.json result files.

---------------------------------------------------------------------
 TROUBLESHOOTING
---------------------------------------------------------------------
- The window does not open but the page opens in your web browser:
  your Windows lacks the "WebView2" component (Windows 11 always has
  it; it can be installed on Windows 10 from Microsoft's website).
  The program still works in the browser: use the "Quit the app"
  button of the page to stop it.
- "Port 8790 is already used by another program": another program
  uses the same network port on your computer; close it and try
  again.
- An antivirus can slow down the first start and the first analysis.
- If something goes wrong, the file data\journal.txt tells what
  happened: please attach it when you report a problem.

---------------------------------------------------------------------
 LICENCES AND CREDITS
---------------------------------------------------------------------
- @NOM@ is free software under the MIT licence (LICENSE.txt).
- Bird identification: BirdNET GLOBAL 6K V2.4, by the K. Lisa Yang
  Center for Conservation Bioacoustics (Cornell Lab of Ornithology)
  and Chemnitz University of Technology. The BirdNET model is licensed
  CC BY-NC-SA 4.0: NON-COMMERCIAL USE ONLY. For this reason this
  program may be shared freely but not sold.
  Kahl, S., Wood, C. M., Eibl, M., & Klinck, H. (2021). BirdNET: A
  deep learning solution for avian diversity monitoring. Ecological
  Informatics, 61, 101236.
- Also included: birdnetlib (Joe Weiss), LiteRT (Google), FFmpeg
  (LGPL build by BtbN), Python, NumPy, SciPy, pywebview and others.
  The complete list with every licence text is in LICENSES.txt.
- Designed and written by Seb, Chris and Claude (Anthropic's Claude
  Code), 2026.
- Contact: sebno42@gmail.com
- Source code, new versions and problem reports:
  @SOURCES@

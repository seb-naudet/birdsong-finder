=====================================================================
 @NOM@ @VERSION@ - identify bird species in your recordings
 (Version francaise : voir LISEZMOI.txt)
=====================================================================

@NOM@ is a free Windows program that identifies birds in your audio
and video recordings with BirdNET, the bird sound recognition model of
the Cornell Lab of Ornithology and Chemnitz University of Technology.
It was designed for screen reader users (NVDA, JAWS, Narrator).

Everything runs locally, on your own computer: nothing is uploaded,
and no Internet connection is needed.

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
   You can also copy (Ctrl+C) or cut (Ctrl+X) files or folders in
   Explorer, come back to the window and press Ctrl+V (or the "Paste
   into audio" button): they are copied (or moved) into the audio
   folder, and the analyses already made for them follow, so nothing
   is analysed twice. Nothing already present is replaced.

4. In the list of recordings, "Show species" lists the birds heard,
   in the order you chose, with one button per
   passage to listen to the excerpt around the song.

To close the program: close its window (Alt+F4) or use the "Quit the app"
button. No confirmation is asked; an unfinished analysis simply
starts again at the next launch.
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
- Keys: Space on a passage plays it (again), Ctrl+Space pauses or
  resumes, Shift+Space stops.

Results are stored in "data\analyses": "analyses\audio\" mirrors the
audio folder, and each folder you added
in the window gets its own sub-folder ("folder-name_xxxxxxxx", with a
dossier.txt naming the original path). If a recording is deleted or
moved away, its result is removed automatically; if you remove a
folder from the watched list, its results are deleted at the next
start. A watched folder that is not found at start (unplugged disk?)
keeps its results and is shown as "not found" in the window. Older
versions stored results next to each recording: they are moved into
"data\analyses" at the first start.

---------------------------------------------------------------------
 LIVE ANALYSIS (section "Live" in the page)
---------------------------------------------------------------------
Birdsong Finder can also listen to an audio input of the computer in
real time: a microphone, an audio interface, the "Stereo Mix" of the
sound card, or a virtual cable (for example VB-Cable) that carries what
another program plays. Choose the input in the list, then "Start live
analysis". Mono and stereo inputs are analysed like files (left and
right separately for stereo, with a left-right position).
- The list "Species heard during this live session" grows as birds
  are detected; each line says whether the bird is heard right now,
  how many times it was detected, when it was first heard and its best
  confidence. The list never reorganises itself: you can browse it with
  a screen reader at any time.
- "Announce each species heard automatically": when checked, each new
  species (and each confirmation of an uncertain one) is spoken by the
  screen reader as it arrives. Off by default.
- "Write the species heard to a text file": when checked, the same
  events are appended to data\direct\direct_<date>.txt (one file per
  day; see the folder list below).
- Rules, same as the live stream of the ambisonic VPS: a species
  appears at 25 % confidence, is "sure" from 50 %, stays 40 seconds
  after its last detection; a name can arrive a few seconds after the
  song (the analysis runs every 6 seconds on the last 12 seconds).
- "Listen to the audio input (monitor)": plays what the input receives
  on the default output of the computer, to check a microphone or a
  cable. Off by default, and off again at each start. It is blocked
  for "Stereo Mix" type inputs (they carry what the computer plays:
  the monitor would hear itself, louder and louder), and it switches
  itself off if the input stays saturated for a whole second.

Stereo Mix and virtual cables
"Stereo Mix" (Realtek cards; "What U Hear" on Creative cards) is an
input that records everything the computer plays: a web radio, a
player, a live stream in the browser... With it, Birdsong Finder
analyses in real time what you are listening to. Windows usually
disables or hides it; to turn it on:
  1. Press Windows+R, type   control mmsys.cpl,,1   then Enter: the
     "Recording" tab of the sound settings opens.
  2. In the list, open the context menu (Applications key or
     Shift+F10) and tick "Show Disabled Devices".
  3. On "Stereo Mix", context menu, "Enable".
  4. Back in Birdsong Finder, "Refresh the list of inputs".
Some sound cards (many laptops, USB headsets) have no Stereo Mix at
all: use a virtual cable instead.
A virtual cable (for example VB-Cable, free) adds a pair of devices:
send the program you want to analyse to "CABLE Input" (choose it as
that program's output device, or as the Windows default output), and
choose "CABLE Output" as the input in Birdsong Finder. To keep hearing
the program yourself, either switch on the monitor in the page, or, in
the Recording tab, properties of "CABLE Output", tab "Listen", tick
"Listen to this device" - not both at once. Never switch on the
monitor if the cable is also the default output of Windows: that is a
loop too.

---------------------------------------------------------------------
 SCREEN READERS
---------------------------------------------------------------------
The window shows a web page: NVDA and JAWS use browse mode, as in
Chrome or Edge.
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
- audio\  ........ your recordings
- data\   ........ analysis results (analyses\), live logs (direct\),
                   settings, log
                   (journal.txt), temporary files and the window's cache
Unzipped in a read-only folder, the program uses
%LocalAppData%\@EXE@ instead.

To uninstall: close the program and delete its folder (move your
recordings out of the audio folder first if you want to keep them).
Nothing is written in the registry or elsewhere, not even in the
folders you added in the window.

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

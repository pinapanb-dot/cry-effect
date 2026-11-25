Cry Effect
==========

Cry Effect adds crying characteristics (tremor, sob inhales, voice breaks and more) to the
regions you choose in a vocal WAV. The output is a 32-bit float WAV with the same length and
sample rate as the input. The original file is never overwritten.


Requirements
------------
- Windows 10 / 11 (64-bit)
- Microsoft Edge (included with Windows). If it is missing, Google Chrome is used, and
  otherwise your default browser.
- No internet connection is needed; everything runs on this PC.


Starting
--------
1. Extract the whole zip (it cannot run from inside the zip).
2. Double-click CryEffect.exe in the CryEffect folder.
   - The first time, Windows may show "Windows protected your PC" because the app is not
     signed. Click "More info" and then "Run anyway".
3. The tool window opens. Closing the window quits the tool.

Use the "日本語 / English" menu at the top right to switch the language (it is remembered).


Basic use
---------
1. Click "Open…" and choose a WAV (44.1 kHz / 48 kHz, mono or stereo).
   The first analysis takes a while (about 15 s for a 3-minute song); later opens are instant.
2. Drag on an effect's lane to create a region (double-click on marker lanes).
3. Click a region to adjust its strength and parameters on the right. Changes apply automatically.
4. Press B to compare original / processed, and L to loop the selected region.
5. "Save" stores the project (<wav name>.cry.json); "Export WAV…" writes the processed WAV.

Keys
  Space         Play / stop
  B             Switch original / processed
  L             Loop the selected region
  Delete        Delete the selected region
  Esc           Deselect
  Ctrl+wheel    Zoom          Shift+wheel   Scroll sideways
  Ctrl+S        Save the project


Effects
-------
  Irregular tremor / Phrase-end breakdown / Breathiness / Sob (lower formants) /
  Sob pulses / Voice break (marker) / Blurred articulation / Sob inhale /
  Glottal catch (marker) / F2 raise / Nasal voice / Sound effect (sniff etc.)

Hover over a lane name, or select a region, to see what each effect does.
The "Cry amount" slider scales every region at once.


Materials (inhales, sniffs, etc.)
---------------------------------
Put WAV files in these folders inside the CryEffect folder:
  materials\inhale\   inhale sounds for "Sob inhale"
  materials\sfx\      sniffs and other sounds for "Sound effect"
They then appear in the region's "Source" menu. Without them, synthesized sounds are used.


Where data is stored
--------------------
Settings, the analysis cache and logs are kept in:
  %LOCALAPPDATA%\CryEffect\
    settings.json   language and other settings
    cache\          analysis cache (about 140 MB per 3-minute song; safe to delete)
    logs\app.log    log file (useful when something goes wrong)
    window\         browser settings used by the tool window

To uninstall, delete the CryEffect folder and the folder above.


Troubleshooting
---------------
- The window does not open: wait a moment and start CryEffect.exe again. If it still does not
  open, check logs\app.log.
- The file dialog is not visible: it may be behind another window.
- No sound: press "▶ Play", wait a few seconds, and check the Windows output device.


Cry Effect itself is released under the MIT License (LICENSE.txt).
Licenses of the bundled software are in THIRD_PARTY_LICENSES.txt.

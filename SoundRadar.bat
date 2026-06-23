@echo off
title SoundRadar  (close this window to stop)
cd /d "%~dp0"
echo Starting SoundRadar surround radar... close this window to stop.
echo (Make sure VoiceMeeter is running and set up - see SETUP.md)
python run.py --route-audio --device "Voicemeeter VAIO3 Input" --gain 2.2 --sensitivity 50 --size 40 --adapt 40 --out-gain 0.5
if errorlevel 1 (
  echo.
  echo SoundRadar exited with an error - see the messages above.
  echo If it could not open the audio device, check the SETUP.md checklist.
  pause
)

@echo off
rem Uruchamia okno BookReader. Mozna przeciagnac plik EPUB/PDF na ten plik.
start "" /D "%~dp0" "%~dp0.venv\Scripts\pythonw.exe" -m bookreader.gui %*

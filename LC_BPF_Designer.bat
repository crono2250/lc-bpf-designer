@echo off
rem LC BPF Designer launcher (double-click). Python 3.8+ with tkinter; no extra packages.
rem Arguments are passed through, e.g.  LC_BPF_Designer.bat --demo elliptic 5
where pyw >nul 2>nul
if %errorlevel%==0 (
  start "" pyw "%~dp0lc_bpf_designer.pyw" %*
  exit /b
)
where pythonw >nul 2>nul
if %errorlevel%==0 (
  start "" pythonw "%~dp0lc_bpf_designer.pyw" %*
  exit /b
)
echo Python 3 with tkinter was not found. Use dist\LC_BPF_Designer.exe, or install Python from https://www.python.org/
pause

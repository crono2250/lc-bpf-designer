@echo off
rem LC BPF designer launcher (double-click to start)
cd /d "%~dp0"
where pyw >nul 2>nul
if %errorlevel%==0 (
  start "" pyw -3 "%~dp0lc_bpf_designer.pyw" %*
  exit /b
)
where pythonw >nul 2>nul
if %errorlevel%==0 (
  start "" pythonw "%~dp0lc_bpf_designer.pyw" %*
  exit /b
)
echo Python 3 with tkinter was not found. Install it from https://www.python.org/
pause

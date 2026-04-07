@echo off
set PYTHON_PATH="%LOCALAPPDATA%\Programs\Python\Python312\python.exe"

if not exist ".venv" (
    echo Creating virtual environment...
    %PYTHON_PATH% -m venv .venv
)
echo Activating virtual environment...
call .\.venv\Scripts\activate.bat
echo Installing dependencies...
pip install -r requirements.txt
echo Starting application...
python app.py
pause

if (-Not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment..."
    python -m venv .venv
}
Write-Host "Activating virtual environment..."
& .\.venv\Scripts\Activate.ps1
Write-Host "Installing dependencies..."
pip install -r requirements.txt
Write-Host "Starting application..."
python app.py

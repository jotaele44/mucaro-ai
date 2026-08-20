# Desktop Launcher Mode

Desktop Launcher Mode lets Mucaro AI run with one command.

It starts the private local backend on `127.0.0.1:8000`, serves the built React UI through FastAPI, opens the browser, and stops cleanly with `Ctrl+C`.

## Run

```bash
cd "/Users/jotaele/Developer/mucaro-ai"
source .venv/bin/activate
python scripts/launch_app.py

import streamlit.web.cli as stcli
import os, sys

def resolve_path(path):
    if getattr(sys, "frozen", False):
        # If running as an exe, look for files in the temporary folder
        basedir = sys._MEIPASS
    else:
        basedir = os.path.dirname(__file__)
    return os.path.join(basedir, path)

if __name__ == "__main__":
    # Point this to your main Streamlit script (e.g., app.py)
    app_path = resolve_path("app.py") 
    sys.argv = [
        "streamlit",
        "run",
        app_path,
        "--server.port=8501",
        "--global.developmentMode=false",
    ]
    sys.exit(stcli.main())

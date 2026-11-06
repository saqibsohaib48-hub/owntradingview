"""Start OpenTrade:  python run.py  [--host 127.0.0.1] [--port 8000]"""
import argparse
import webbrowser

import uvicorn

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="OpenTrade - charting, Pine/Python indicators and backtesting")
    ap.add_argument("--host", default="127.0.0.1", help="Keep 127.0.0.1 unless you trust your network: "
                                                         "the editor can save and run Python code.")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    if not a.no_browser:
        webbrowser.open(f"http://{a.host}:{a.port}")
    uvicorn.run("app.server:app", host=a.host, port=a.port, reload=False)

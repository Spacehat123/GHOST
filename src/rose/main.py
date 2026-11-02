"""Entrypoint for Rose Voice Assistant."""
import uvicorn
import argparse

def main():
    parser = argparse.ArgumentParser(description="Rose Voice Assistant")
    parser.add_argument("--host", default="127.0.0.1", help="Host address")
    parser.add_argument("--port", type=int, default=8765, help="Port")
    args = parser.parse_args()

    print(f"🌹 Starting Rose Voice Assistant on http://{args.host}:{args.port}")
    uvicorn.run("rose.server.app:app", host=args.host, port=args.port, reload=False)

if __name__ == "__main__":
    main()

"""Allow running the CLI with ``python -m xvideo``."""

from xvideo.cli import app

if __name__ == "__main__":
    app(prog_name="xvideo")

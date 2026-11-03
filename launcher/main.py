"""Source entry point for the native C++ / Qt ECHOES launcher."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools' / 'launcher_native'))
from host import main

if __name__ == '__main__':
    raise SystemExit(main())

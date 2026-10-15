from pathlib import Path

def get_project_root() -> Path:
    """Finds project root by finding the pyproject.toml file"""
    current = Path(__file__).parent
    while current != current.parent:
        if (current / "pyproject.toml").exists():
            return current
        else:
            current = current.parent

    raise FileNotFoundError("No pyproject.toml could be found!")

PROJECT_ROOT = get_project_root()
INPUT_DIR = PROJECT_ROOT / "data" / "input"
OUTPUT_DIR = PROJECT_ROOT / "data" / "output"

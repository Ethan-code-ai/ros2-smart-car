"""Content identity for capability acceptance; cached for this process lifetime."""
from functools import lru_cache
import hashlib
from pathlib import Path


@lru_cache(maxsize=1)
def component_manifest():
    root = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for path in sorted(root.glob("*.py")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    config = root.parent / "config"
    for path in sorted(config.glob("*")):
        if path.is_file():
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
    return {"protocol": 1, "source_sha256": digest.hexdigest()}

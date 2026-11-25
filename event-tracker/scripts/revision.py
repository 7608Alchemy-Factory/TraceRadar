"""Find the source revision in a Git checkout or an exported submission."""
import re
import subprocess
from pathlib import Path


def source_revision(repo: Path) -> dict:
    marker = repo / 'SUBMISSION_REVISION'
    if marker.is_file():
        revision = marker.read_text(encoding='ascii').strip()
        if re.fullmatch(r'[0-9a-f]{40,64}', revision):
            return {'commit': revision, 'working_tree_dirty': None}

    if (repo / '.git').exists():
        try:
            commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip()
            status = subprocess.check_output(['git', 'status', '--porcelain'], cwd=repo, text=True)
            return {'commit': commit, 'working_tree_dirty': bool(status)}
        except (OSError, subprocess.CalledProcessError):
            pass

    return {'commit': None, 'working_tree_dirty': None}

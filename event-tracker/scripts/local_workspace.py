"""Serve the built workspace and its API together on localhost."""
import argparse
import logging
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))


def create_workspace():
    from fastapi.staticfiles import StaticFiles
    from app.main import create_app

    static_root = ROOT / 'frontend' / 'dist'
    if not (static_root / 'index.html').is_file():
        raise RuntimeError('Build the frontend first: cd frontend && npm ci && npm run build')
    workspace = create_app()

    @workspace.get('/api/local-workspace')
    def local_status():
        return dict(application='TraceRadar', mode='bundled-local',
                    workspace_root=str(ROOT), process_id=os.getpid(),
                    database_path=workspace.state.repo.path)

    # API/docs routes take precedence over the static mount.
    workspace.mount('/', StaticFiles(directory=static_root, html=True), name='workspace')
    return workspace


def main():
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=5173)
    parser.add_argument('--database-path', type=Path)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('--port must be between 1 and 65535')
    if args.database_path is not None:
        os.environ['DATABASE_PATH'] = str(args.database_path)

    log_name = 'local-workspace.log' if args.port == 5173 else f'local-workspace-{args.port}.log'
    log_path = ROOT / 'data' / log_name
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s',
                        handlers=[logging.FileHandler(log_path, encoding='utf-8')], force=True)
    uvicorn.run(create_workspace(), host='127.0.0.1', port=args.port, log_config=None)


if __name__ == '__main__':
    main()

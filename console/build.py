"""Build the dependency-free, offline-capable NightWatch static frontend."""
import shutil
from pathlib import Path


def main():
    console = Path(__file__).resolve().parent
    sources = {name: console / 'src' / name for name in ('index.html', 'app.css', 'app.js', 'data.js')}
    recording = console.parent / 'contracts' / 'fixtures' / 'catalog_pool_leak'
    sources.update({f'recordings/{name}': recording / name for name in ('state.initial.json', 'state.json', 'snapshots.jsonl', 'events.jsonl')})
    missing = [str(path) for path in sources.values() if not path.is_file()]
    if missing:
        raise SystemExit('Build failed: missing files\n' + '\n'.join(missing))
    total = 0
    for relative, source in sources.items():
        target = console / 'dist' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        size = target.stat().st_size
        total += size
        print(f'{relative}: {size} bytes')
    print(f'Build complete: {len(sources)} files, {total} bytes. No network or third-party dependencies.')


if __name__ == '__main__':
    main()

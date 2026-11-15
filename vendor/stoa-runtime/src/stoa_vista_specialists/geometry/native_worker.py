"""Isolated source-native enumeration child; stdout is one JSON tool receipt."""
import json
from pathlib import Path
import sys
from .tools import GeometryTools


def main():
    root, folder = map(Path, sys.argv[1:])
    request = json.load(sys.stdin)
    geometry = GeometryTools(root, folder, request['source_id'], request['source_sha256'], request['page'], request['width'], request['height'],native_source=request.get('native_source'))
    try:
        result = geometry.native_room_candidates(request['source_id'], request['page'], request.get('room_identifier'))
        print(json.dumps(result, allow_nan=False))
    finally:
        geometry.close()


if __name__ == '__main__':
    main()

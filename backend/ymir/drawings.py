"""Drawing library: bundled samples (data/samples) plus uploads (YMIR_DATA_DIR/drawings).

Each drawing is three files side by side: <id>.json (payload), <id>.trace.json (build trace) and its image.
"""
from __future__ import annotations

import json
import re
import secrets
from pathlib import Path

from . import settings

ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


class DrawingStore:
    def __init__(self, samples: Path = settings.SAMPLES_DIR, uploads: Path = settings.UPLOADS_DIR):
        self.samples, self.uploads = Path(samples), Path(uploads)

    def _folder(self, drawing_id: str) -> Path:
        if not ID.fullmatch(drawing_id):
            raise KeyError(drawing_id)
        for folder in (self.samples, self.uploads):
            if (folder / f"{drawing_id}.json").is_file():
                return folder
        raise KeyError(drawing_id)

    def raw(self, drawing_id: str) -> dict:
        return json.loads((self._folder(drawing_id) / f"{drawing_id}.json").read_text())

    def image_path(self, drawing_id: str) -> Path:
        d = self.raw(drawing_id)
        return self._folder(drawing_id) / Path(d["image"]["url"]).name

    def payload(self, drawing_id: str) -> dict:
        """The payload as the frontend gets it: image served by the API."""
        d = self.raw(drawing_id)
        d["image"] = {**d["image"], "url": f"api/drawings/{drawing_id}/image"}
        return d

    def trace(self, drawing_id: str) -> list:
        path = self._folder(drawing_id) / f"{drawing_id}.trace.json"
        return json.loads(path.read_text()) if path.is_file() else []

    def library(self) -> list[dict]:
        ids = []
        manifest = self.samples / "library.json"
        if manifest.is_file():
            ids += [e["id"] for e in json.loads(manifest.read_text())]
        if self.uploads.is_dir():
            uploads = sorted(self.uploads.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
            ids += [p.stem for p in uploads if not p.name.endswith(".trace.json")]
        out = []
        for i in ids:
            d = self.raw(i)
            out.append({"id": i, "title": d["title"], "thumbnail": f"api/drawings/{i}/image",
                        "source": {k: d["source"].get(k) for k in ("dataset", "kind")}})
        return out

    def _projects(self) -> list[dict]:
        manifest = self.samples / "projects.json"
        return json.loads(manifest.read_text()) if manifest.is_file() else []

    def projects(self) -> list[dict]:
        """Sample projects: buildings made of several sheets (see scripts/build_ifc_project.py)."""
        return [{"id": p["id"], "title": p["title"], "thumbnail": p.get("thumbnail"), "sheets": len(p["sheets"]),
                 "source": {k: p.get("source", {}).get(k) for k in ("dataset", "credit")}} for p in self._projects()]

    def project(self, project_id: str) -> dict:
        p = next((p for p in self._projects() if p["id"] == project_id), None)
        if p is None:
            raise KeyError(project_id)
        return p

    def delete(self, drawing_id: str) -> None:
        """Remove an uploaded drawing (payload, trace and image). Samples can't be deleted: PermissionError."""
        if self._folder(drawing_id) != self.uploads:
            raise PermissionError(drawing_id)
        image = self.image_path(drawing_id)
        for path in (self.uploads / f"{drawing_id}.json", self.uploads / f"{drawing_id}.trace.json", image):
            path.unlink(missing_ok=True)

    def save(self, payload: dict, trace: list, image: bytes, ext: str) -> str:
        """Store a newly built drawing; returns its id."""
        self.uploads.mkdir(parents=True, exist_ok=True)
        drawing_id = f"u-{secrets.token_hex(6)}"
        name = f"{drawing_id}.{ext}"
        (self.uploads / name).write_bytes(image)
        payload = {**payload, "id": drawing_id, "image": {**payload["image"], "url": name}}
        (self.uploads / f"{drawing_id}.trace.json").write_text(json.dumps(trace, ensure_ascii=False))
        (self.uploads / f"{drawing_id}.json").write_text(json.dumps(payload, ensure_ascii=False))
        return drawing_id

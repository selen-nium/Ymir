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

    def _relocate_uploaded_source(self, source: dict) -> dict:
        """Resolve preserved document paths after moving the workspace directory.

        Historical JSON stays byte-identical. Only paths inside this drawing's
        document may resolve against the current workspace's document folder.
        """
        document_id=source.get('document_id')
        if not isinstance(document_id,str) or not ID.fullmatch(document_id):return source
        folder=(self.uploads.parent/'documents'/document_id).resolve()
        def relocate(value):
            if isinstance(value,dict):return {k:relocate(v) for k,v in value.items()}
            if isinstance(value,list):return [relocate(v) for v in value]
            if not isinstance(value,str) or not Path(value).is_absolute():return value
            parts=Path(value).parts
            for index in range(len(parts)-1):
                if parts[index:index+2]!=('documents',document_id):continue
                suffix=parts[index+2:]
                if '..' in suffix:return value
                target=folder.joinpath(*suffix).resolve()
                if target.is_relative_to(folder) and target.exists():return str(target)
            return value
        return relocate(source)

    def raw(self, drawing_id: str) -> dict:
        folder=self._folder(drawing_id)
        d=json.loads((folder / f"{drawing_id}.json").read_text())
        if folder==self.samples:
            source=d.get('lens_source',{})
            for key in ('image','pdf'):
                if source.get(key) and not Path(source[key]).is_absolute():source[key]=str((self.samples/source[key]).resolve())
        elif folder==self.uploads and d.get('lens_source'):
            d['lens_source']=self._relocate_uploaded_source(d['lens_source'])
        return d

    def catalog(self) -> dict | None:
        path=self.samples/'featured-catalog.json'
        return json.loads(path.read_text()) if path.is_file() else None

    def image_path(self, drawing_id: str) -> Path:
        d = self.raw(drawing_id)
        return self._folder(drawing_id) / Path(d["image"]["url"]).name

    def payload(self, drawing_id: str) -> dict:
        """The payload as the frontend gets it: image served by the API."""
        d = self.raw(drawing_id)
        d.pop("lens_source", None)
        d["image"] = {**d["image"], "url": f"api/drawings/{drawing_id}/image"}
        return d

    def trace(self, drawing_id: str) -> list:
        path = self._folder(drawing_id) / f"{drawing_id}.trace.json"
        return json.loads(path.read_text()) if path.is_file() else []

    def library(self) -> list[dict]:
        ids = []
        manifest = self.samples / "library.json"
        if self.catalog():
            ids += self.catalog()['drawings']
        elif manifest.is_file():
            ids += [e["id"] for e in json.loads(manifest.read_text())]
        if self.uploads.is_dir():
            uploads = sorted(self.uploads.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
            ids += [p.stem for p in uploads if not p.name.endswith(".trace.json")]
        out = []
        for i in ids:
            d = self.raw(i)
            out.append({"id": i, "title": d["title"], "thumbnail": f"api/drawings/{i}/image",
                        "source": {k: d["source"].get(k) for k in ("dataset", "kind", "credit", "licence")}})
        return out

    def _projects(self) -> list[dict]:
        manifest = self.samples / "projects.json"
        legacy=json.loads(manifest.read_text()) if manifest.is_file() else []
        featured=self.samples/'featured-projects.json'
        return legacy+(json.loads(featured.read_text()) if featured.is_file() else [])

    def projects(self) -> list[dict]:
        """Sample projects: buildings made of several sheets (see scripts/build_ifc_project.py)."""
        return [{"id": p["id"], "title": p["title"], "thumbnail": p.get("thumbnail"), "sheets": len(p["sheets"]),
                 "source": {k: p.get("source", {}).get(k) for k in ("dataset", "credit", "licence", "url")}} for p in self._projects() if not self.catalog() or p['id'] in self.catalog()['projects']]

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
        import shutil
        shutil.rmtree(self.model_dir(drawing_id), ignore_errors=True)

    def model_dir(self, drawing_id: str) -> Path:
        """Folder of the converter's building-model files for an uploaded drawing."""
        if not ID.fullmatch(drawing_id):
            raise KeyError(drawing_id)
        return self.uploads / f"{drawing_id}.model"

    def set_model_files(self, drawing_id: str, record: dict, geometry: dict | None = None) -> None:
        path = self.uploads / f"{drawing_id}.json"
        payload = json.loads(path.read_text())
        payload["model_files"] = record
        if geometry:
            # Build's own line walls stay in the file for reference; the app draws the converter's model.
            payload["build_lines"] = {"walls": payload["walls"], "rooms": payload["rooms"], "openings": payload.get("openings", [])}
            payload["walls"] = []
            payload.update(geometry)
        path.write_text(json.dumps(payload, ensure_ascii=False))

    def model_file(self, drawing_id: str, name: str) -> Path:
        from .build.floorplan.model_files import FILES
        if name not in FILES or name not in self.raw(drawing_id).get("model_files", {}).get("files", []):
            raise KeyError(name)
        path = self.model_dir(drawing_id) / name
        if not path.is_file():
            raise KeyError(name)
        return path

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

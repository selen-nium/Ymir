"""Door / window / fixture detector for the PNG-to-IFC converter, served from Modal.

The converter's `--detector` option uses the counting trials' RF-DETR Medium fine-tuned on Swiss Dwellings
(classes: door, window, toilet, sink, bathtub, shower). The weights are not in this repository. Upload them
once, then deploy:

    modal volume create ymir-openings-v1
    modal volume put ymir-openings-v1 <stoahackjunction>/data/counting-trials/runs/rfdetr/swiss/checkpoint_best_total.pth /checkpoint_best_total.pth
    modal deploy deploy/modal_openings.py

The API calls `detect` when YMIR_OPENINGS_BACKEND=modal (backend/ymir/build/floorplan/model_files.py).
Licence: the weights are fine-tuned on Swiss Dwellings v3.0.0 (CC BY 4.0) from RF-DETR's released weights.
"""
import hashlib
from pathlib import Path

import modal

WEIGHTS_SHA256 = "bb33892868ae4ab6460a2ad972197ed3f29fcdd2015108cdd873abd858a7c59e"
SRC = Path(__file__).resolve().parents[1] / "research/stoa-lens/src"

app = modal.App("ymir-openings")
weights = modal.Volume.from_name("ymir-openings-v1", create_if_missing=False)
image = (modal.Image.debian_slim(python_version="3.12")
         .apt_install("libgl1", "libglib2.0-0")
         .pip_install("torch==2.11.0", "torchvision", "rfdetr==1.11.2", "supervision==0.31.3", "pillow", "numpy")
         .add_local_dir(str(SRC / "stoa_counting"), "/root/stoa_counting"))


@app.cls(image=image, gpu="A10G", cpu=2, memory=8192, timeout=300, volumes={"/weights": weights},
         scaledown_window=60, max_containers=1, retries=0)
class Openings:
    @modal.enter()
    def load(self):
        path = Path("/weights/checkpoint_best_total.pth")
        with path.open("rb") as f:
            if hashlib.file_digest(f, "sha256").hexdigest() != WEIGHTS_SHA256:
                raise ValueError("Opening-detector weights hash mismatch")
        from stoa_counting import detect as Dt
        self.Dt = Dt
        self.model = Dt.RFDETR("swiss", size="medium", weights=path)

    @modal.method()
    def detect(self, png: bytes) -> dict:
        """png: the page already resized to the detector's training scale. Returns boxes in that image's
        pixels as [class_index, score, x0, y0, x1, y1]; classes as in stoa_counting.common.CLASSES['swiss']."""
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".png") as f:
            f.write(png)
            f.flush()
            dets = self.Dt.predict_page(self.model, f.name)
        return {"weights_sha256": WEIGHTS_SHA256, "classes": self.Dt.CLASSES["swiss"],
                "detections": [[int(c), float(s), float(x0), float(y0), float(x1), float(y1)] for c, s, x0, y0, x1, y1 in dets]}

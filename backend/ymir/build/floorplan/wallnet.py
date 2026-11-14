"""Wall-segmentation network (U-Net decoder on an ImageNet ResNet-50 encoder) and inference helper."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np

from ...settings import WEIGHTS_DIR

WEIGHTS = WEIGHTS_DIR / "wallnet.pt"
_MEAN = np.array([0.485, 0.456, 0.406], np.float32)
_STD = np.array([0.229, 0.224, 0.225], np.float32)


def preprocess(bgr: np.ndarray):
    import torch
    rgb = bgr[..., ::-1].astype(np.float32) / 255.0
    return torch.from_numpy(((rgb - _MEAN) / _STD).transpose(2, 0, 1).copy())


def _modules():
    import torch
    import torch.nn as nn
    import torch.nn.functional as F

    def conv(i, o):
        return nn.Sequential(nn.Conv2d(i, o, 3, padding=1, bias=False), nn.BatchNorm2d(o), nn.ReLU(inplace=True))

    class Up(nn.Module):
        def __init__(self, i, skip, o):
            super().__init__()
            self.block = nn.Sequential(conv(i + skip, o), conv(o, o))

        def forward(self, x, s):
            x = F.interpolate(x, size=s.shape[-2:], mode="bilinear", align_corners=False)
            return self.block(torch.cat([x, s], 1))

    return nn, conv, Up


def WallNet(pretrained: bool = False):
    import torch.nn as nn_
    import torchvision
    nn, conv, Up = _modules()

    class _WallNet(nn_.Module):
        def __init__(self):
            super().__init__()
            w = torchvision.models.ResNet50_Weights.IMAGENET1K_V2 if pretrained else None
            r = torchvision.models.resnet50(weights=w)
            self.stem = nn.Sequential(r.conv1, r.bn1, r.relu)   # /2, 64
            self.pool = r.maxpool
            self.l1, self.l2, self.l3 = r.layer1, r.layer2, r.layer3  # /4 256, /8 512, /16 1024
            self.full = nn.Sequential(conv(3, 24), conv(24, 24))  # /1 detail for thin walls
            self.d3 = Up(1024, 512, 256)
            self.d2 = Up(256, 256, 128)
            self.d1 = Up(128, 64, 64)
            self.d0 = Up(64, 24, 32)
            self.head = nn.Conv2d(32, 1, 1)

        def forward(self, x):
            f0 = self.full(x)
            s = self.stem(x)
            a = self.l1(self.pool(s))
            b = self.l2(a)
            c = self.l3(b)
            y = self.d0(self.d1(self.d2(self.d3(c, b), a), s), f0)
            return self.head(y)

    return _WallNet()


@lru_cache(maxsize=1)
def _load(path: str):
    import torch
    dev = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    m = WallNet(pretrained=False)
    m.load_state_dict(torch.load(path, map_location="cpu"))
    return m.to(dev).eval(), dev


def available(path: Path = WEIGHTS) -> bool:
    return Path(path).is_file()


def predict(bgr: np.ndarray, path: Path = WEIGHTS, tile: int = 1024, tta: bool = False) -> np.ndarray:
    """Wall probability (float32 HxW, 0..1). Tiles large images with overlap; tta averages a mirrored pass."""
    import cv2
    import torch
    model, dev = _load(str(path))
    H, W = bgr.shape[:2]
    prob = np.zeros((H, W), np.float32)
    weight = np.zeros((H, W), np.float32)
    step = tile - 128
    ys = list(range(0, max(1, H - 128), step)) or [0]
    xs = list(range(0, max(1, W - 128), step)) or [0]
    with torch.no_grad():
        for y in ys:
            for x in xs:
                sub = bgr[y:y + tile, x:x + tile]
                h, w = sub.shape[:2]
                sub = cv2.copyMakeBorder(sub, 0, (-h) % 32, 0, (-w) % 32, cv2.BORDER_CONSTANT, value=(255, 255, 255))
                inp = preprocess(sub)[None].to(dev)
                if tta:
                    out = torch.sigmoid(model(torch.cat([inp, torch.flip(inp, dims=[3])])))
                    p = ((out[0, 0] + torch.flip(out[1, 0], dims=[1])) / 2)[:h, :w].cpu().numpy()
                else:
                    p = torch.sigmoid(model(inp))[0, 0, :h, :w].cpu().numpy()
                prob[y:y + h, x:x + w] += p
                weight[y:y + h, x:x + w] += 1
    return prob / np.maximum(weight, 1)

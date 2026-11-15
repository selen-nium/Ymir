"""RF-DETR 1.12.1 extension for per-image, per-class reference coverage.

Coverage is attached *after* the upstream COCO conversion and transforms. This
module does not change upstream source or the independent six-class trial.
"""
from __future__ import annotations

import json
from pathlib import Path

import torch
import torch.nn.functional as F
from rfdetr.models.criterion import SetCriterion
from rfdetr.training import RFDETRDataModule
from rfdetr.utilities import box_ops

CLASSES = ("door", "window", "column", "staircase", "elevator", "toilet_bowl",
           "urinal", "bathtub", "shower", "sink", "appliance", "bed", "chair",
           "table", "sofa", "cabinet_wardrobe")
STATES = {"unknown", "partial", "complete", "verified_absent"}


class CoveredDataset(torch.utils.data.Dataset):
    """Attach sidecar coverage by transformed target image ID, never by order."""
    def __init__(self, dataset, sidecar: Path):
        self.dataset = dataset
        self.coverage = json.loads(Path(sidecar).read_text())
        for key, states in self.coverage.items():
            if not isinstance(key, str) or not isinstance(states, list) or len(states) != len(CLASSES) or any(s not in STATES for s in states):
                raise ValueError("Invalid coverage sidecar")

    def __len__(self):
        return len(self.dataset)

    def __getitem__(self, index):
        image, target = self.dataset[index]
        key = str(int(target["image_id"].item()))
        states = self.coverage[key]  # fail closed on missing coverage
        target["class_coverage"] = torch.tensor([s in {"complete", "verified_absent"} for s in states], dtype=torch.bool)
        return image, target

    def __getattr__(self, name):
        if name in {"dataset", "coverage"}:
            raise AttributeError(name)
        return getattr(self.dataset, name)


class CoveredDataModule(RFDETRDataModule):
    """Add coverage after COCO conversion and its per-sample transforms.

    Packed targets and GPU-side augmentations are rejected until their handling
    of custom per-image fields is independently verified.
    """
    def __init__(self, model_config, train_config):
        if train_config.pack_targets or train_config.pad_targets_to is not None:
            raise ValueError("Coverage requires unpacked, unpadded targets")
        if str(train_config.augmentation_backend).lower() not in {"cpu", "torchvision", "albumentations", "augmentationbackend.cpu", "augmentationbackend.torchvision", "augmentationbackend.albu"}:
            raise ValueError("Coverage requires a CPU augmentation backend")
        super().__init__(model_config, train_config)

    def setup(self, stage):
        super().setup(stage)
        root = Path(self.train_config.dataset_dir)
        for attr, split in (("_dataset_train", "train"), ("_dataset_val", "valid")):
            dataset = getattr(self, attr)
            if dataset is not None and not isinstance(dataset, CoveredDataset):
                setattr(self, attr, CoveredDataset(dataset, root / split / "coverage.json"))
        if stage == "test":
            raise ValueError("Use separately audited coverage-aware evaluation")


class CoverageCriterion(SetCriterion):
    """Apply coverage inside IA-BCE before reduction for every output layer."""
    def _can_batch_detection_losses(self, *args, **kwargs):
        # The upstream compiled fast path has no per-image coverage input.
        return False

    def loss_labels(self, outputs, targets, indices, num_boxes, log=True, matched_targets=None):
        if not self.ia_bce_loss:
            raise ValueError("Partial coverage requires IA-BCE")
        logits = outputs["pred_logits"]
        if logits.shape[-1] != len(CLASSES) + 1:
            raise ValueError("Unexpected class head; expected 16 classes and the upstream extra slot")
        coverage = torch.stack([t["class_coverage"] for t in targets]).to(device=logits.device, dtype=torch.bool)
        if coverage.shape != (logits.shape[0], len(CLASSES)):
            raise ValueError("Coverage must be batch by 16 classes")
        # Keep the extra slot exactly as RF-DETR's stock background behavior.
        coverage = torch.cat((coverage, torch.ones((len(targets), 1), dtype=torch.bool, device=logits.device)), dim=1)
        idx = self._get_src_permutation_idx(indices) if matched_targets is None else matched_targets.source_indices
        labels = torch.cat([t["labels"][j] for t, (_, j) in zip(targets, indices)]) if matched_targets is None else matched_targets.labels
        boxes = torch.cat([t["boxes"][j] for t, (_, j) in zip(targets, indices)]) if matched_targets is None else matched_targets.boxes
        valid = self._matched_valid_mask(targets, indices)
        prob = logits.sigmoid()
        iou, _ = box_ops.elementwise_box_iou(box_ops.box_cxcywh_to_xyxy(outputs["pred_boxes"][idx].detach()), box_ops.box_cxcywh_to_xyxy(boxes))
        score = (prob[idx[0], idx[1], labels].pow(self.focal_alpha) * iou.detach().pow(1-self.focal_alpha)).clamp(min=.01).detach()
        pos = torch.zeros_like(logits)
        neg = prob.square() * coverage[:, None, :]
        positive = (idx[0], idx[1], labels)
        keep = torch.ones_like(score, dtype=torch.bool) if valid is None else valid.bool()
        # A real matched positive retains both terms, regardless of coverage.
        pos[positive] = (score * keep).to(pos.dtype)
        neg[positive] = torch.where(keep, (1-score).to(neg.dtype), neg[positive])
        loss = (neg * logits - F.logsigmoid(logits) * (pos + neg)).sum() / num_boxes
        result = {"loss_ce": loss}
        if log:
            # Reuse upstream diagnostic, but not its unmasked classification loss.
            with torch.no_grad():
                diagnostic = super().loss_labels(outputs, targets, indices, num_boxes, log=True, matched_targets=matched_targets)
            result["class_error"] = diagnostic["class_error"]
        return result


def install_criterion(module):
    """Replace the existing criterion without changing matcher or loss weights."""
    if type(module.criterion) is not SetCriterion or not module.criterion.ia_bce_loss:
        raise ValueError("Unsupported RF-DETR criterion configuration")
    module.criterion.__class__ = CoverageCriterion

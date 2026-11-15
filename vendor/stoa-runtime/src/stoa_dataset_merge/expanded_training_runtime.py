"""Isolated RF-DETR training body. No remote dispatch or spend reservation.

Requires a separately verified release and a verified COCO initialization. The
caller owns container lifecycle and the hard provider budget. Validation and
checkpoint selection are performed separately by expanded_evaluation; upstream
COCO callbacks are intentionally disabled because they cannot mask incomplete
class inventories.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import time

from .expanded_audit import CLASSES
from .expanded_export import verify_release
from .expanded_modal_gate import CONFIG

PRETRAINED_SHA256 = "0f4e20e19a99c0f8a62b5685f57f6c8b5c371c59081feda6752a0561a79ccf38"
PACKAGES = {"rfdetr": "1.12.1", "torch": "2.10.0", "torchvision": "0.25.0",
            "pytorch-lightning": "2.6.6", "torchmetrics": "1.8.2", "pycocotools": "2.0.11",
            "numpy": "2.3.5", "transformers": "5.3.0", "timm": "1.0.29", "peft": "0.21.3",
            "scipy": "1.17.1", "pillow": "12.1.1"}


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for b in iter(lambda: f.read(8 * 2**20), b""):
            h.update(b)
    return h.hexdigest()


def _write(path, value):
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    os.replace(tmp, path)


def implementation_digest():
    folder = Path(__file__).parent
    paths = [Path(__file__), folder / "partial_coverage_training.py", folder / "expanded_export.py",
             folder / "expanded_audit.py", folder / "expanded_modal_gate.py",
             folder / "expanded_evaluation.py", folder / "expanded_artifact_limits.py", folder / "expanded-requirements.txt",
             folder / "expanded_modal_launcher.py"]
    return hashlib.sha256(json.dumps({p.name: digest(p) for p in paths}, sort_keys=True).encode()).hexdigest()


def make_configs(dataset, weights, output, *, accelerator="gpu"):
    """Construct the exact model and training configs without loading weights."""
    from rfdetr.config import RFDETRLargeConfig, TrainConfig
    model = RFDETRLargeConfig(num_classes=len(CLASSES), resolution=CONFIG.resolution,
        positional_encoding_size=CONFIG.resolution // 16, pretrain_weights=str(weights),
        gradient_checkpointing=CONFIG.gradient_checkpointing, model_name="RFDETRLarge", device="cuda" if accelerator == "gpu" else "cpu", num_select=300)
    train = TrainConfig(dataset_dir=str(dataset), output_dir=str(output), epochs=CONFIG.epochs,
        batch_size=CONFIG.batch_size, grad_accum_steps=CONFIG.accumulation, eval_batch_size=2,
        lr=CONFIG.lr, lr_encoder=CONFIG.lr_encoder, lr_scheduler="cosine", class_names=list(CLASSES),
        seed=20261114, num_workers=4, multi_scale=False, expanded_scales=False, scale_jitter=False,
        use_ema=True, pack_targets=False, augmentation_backend="cpu", amp_dtype="bf16", accelerator=accelerator,
        devices=1, run_test=False, tensorboard=False, wandb=False, mlflow=False, clearml=False)
    return model, train


def run(dataset: Path, weights: Path, output: Path, *, smoke: bool,
        smoke_receipt: Path | None = None, resume: Path | None = None,
        wall_seconds: int = 21000, commit=None):
    """Train, saving full resumable checkpoints. Caller must enforce spend cap."""
    dataset, weights, output = map(Path, (dataset, weights, output))
    release = verify_release(dataset)
    if digest(weights) != PRETRAINED_SHA256:
        raise ValueError("COCO initialization hash does not match the verified local trial")
    installed = {package: importlib.metadata.version(package).split("+")[0] for package in PACKAGES}
    if installed != PACKAGES:
        raise ValueError("Runtime dependencies do not match the pinned local trial")
    release_hash = digest(dataset / "manifest.json")
    implementation_hash = implementation_digest()
    if not smoke:
        if smoke_receipt is None:
            raise ValueError("A successful matching five-update smoke receipt is required")
        receipt = json.loads(Path(smoke_receipt).read_text())
        if (receipt.get("status") != "passed" or receipt.get("global_step") != 5
            or receipt.get("dataset_manifest_sha256") != release_hash
            or receipt.get("implementation_sha256") != implementation_hash
            or receipt.get("checkpoint_reload_and_inference") is not True
            or receipt.get("ema_enabled") is not True or receipt.get("accumulation") != 2):
            raise ValueError("Smoke proof does not match this dataset and implementation")
        if digest(Path(smoke_receipt).parent / "last.ckpt") != receipt.get("checkpoint_sha256"):
            raise ValueError("Smoke checkpoint no longer matches the receipt")
    if output.exists() and resume is None:
        raise ValueError("Run output already exists; use a new path")
    if resume is not None and (not output.exists() or not Path(resume).is_file()):
        raise ValueError("Resume requires the existing run and a full checkpoint")
    prior_started_ns = None
    if resume is not None:
        resume = Path(resume).resolve()
        if resume.parent != output.resolve() or resume.name not in {"last.ckpt", "interrupted.ckpt"}:
            raise ValueError("Resume must use a full checkpoint within this run")
        previous = json.loads((output / "state.json").read_text())
        if (previous.get("dataset_manifest_sha256") != release_hash
                or previous.get("implementation_sha256") != implementation_hash
                or previous.get("smoke") is not smoke or previous.get("wall_seconds") != wall_seconds):
            raise ValueError("Resume dataset, implementation, or stage differs from the original run")
        prior_started_ns = previous["started_at_ns"]
        history = output / "history"
        history.mkdir(exist_ok=True)
        shutil.copyfile(output / "state.json", history / f"state-{time.time_ns()}.json")
    output.mkdir(parents=True, exist_ok=resume is not None)
    for name, value in {"WANDB_MODE": "disabled", "COMET_MODE": "DISABLED", "DO_NOT_TRACK": "1",
                        "HF_HUB_DISABLE_TELEMETRY": "1", "TOKENIZERS_PARALLELISM": "false"}.items():
        os.environ[name] = value
    import torch
    from pytorch_lightning import seed_everything
    from pytorch_lightning.callbacks import ModelCheckpoint
    from rfdetr.training import RFDETRModelModule, build_trainer
    from rfdetr.training.callbacks import RFDETREMACallback
    from .partial_coverage_training import CoveredDataModule, install_criterion
    from .expanded_artifact_limits import CHECKPOINT_FILE_LIMIT, require_capacity, verify_checkpoint
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is required for the production smoke and training")
    seed_everything(20261114, workers=True)
    torch.set_num_threads(CONFIG.cpu_cores)
    if wall_seconds <= 0 or wall_seconds > CONFIG.max_train_seconds:
        raise ValueError("Invalid wall-time limit")
    # Count even time between interrupted attempts against the original wall
    # budget. This conservative bound also survives a hard container kill.
    elapsed_prior = max(0, (time.time_ns() - prior_started_ns) / 1e9) if prior_started_ns else 0
    remaining_seconds = int(wall_seconds - elapsed_prior)
    if remaining_seconds <= 0:
        raise ValueError("Original run wall-time limit has expired")
    model_config, train_config = make_configs(dataset, weights, output)
    config_hash = hashlib.sha256(json.dumps({"model": model_config.model_dump(mode="json"),
        "train": train_config.model_dump(mode="json")}, sort_keys=True).encode()).hexdigest()
    data = CoveredDataModule(model_config, train_config)
    module = RFDETRModelModule(model_config, train_config)
    install_criterion(module)
    ema = RFDETREMACallback(decay=train_config.ema_decay, tau=train_config.ema_tau,
                            update_interval_steps=train_config.ema_update_interval)
    class DurableCheckpoint(ModelCheckpoint):
        def _save_checkpoint(self, trainer, filepath):
            require_capacity(output.parent, CHECKPOINT_FILE_LIMIT)
            super()._save_checkpoint(trainer, filepath)
            if commit is not None:
                commit()
            verify_checkpoint(Path(filepath))

    checkpoints = DurableCheckpoint(dirpath=output, filename="epoch-{epoch:03d}", every_n_epochs=1,
                                  save_top_k=-1, save_last=False, save_on_train_epoch_end=True)
    periodic = DurableCheckpoint(dirpath=output, filename="step-{step:08d}", every_n_train_steps=250,
                                 save_top_k=1, save_last=True, save_on_train_epoch_end=False)
    trainer = build_trainer(train_config, model_config, callbacks=[ema, checkpoints, periodic], logger=False,
                            enable_checkpointing=True, enable_progress_bar=False, enable_model_summary=False,
                            limit_val_batches=0, num_sanity_val_steps=0,
                            max_time={"seconds": remaining_seconds}, max_steps=5 if smoke else -1)
    state = {"status": "running", "smoke": smoke, "dataset_manifest_sha256": release_hash,
             "implementation_sha256": implementation_hash, "config_sha256": config_hash,
             "pretrained_sha256": PRETRAINED_SHA256, "ema_enabled": True,
             "packages": installed,
             "accumulation": CONFIG.accumulation, "started_at_ns": prior_started_ns or time.time_ns(),
             "wall_seconds": wall_seconds,
             "release_verification": release}
    _write(output / "state.json", state)
    if commit is not None:
        commit()
    try:
        trainer.fit(module, datamodule=data, ckpt_path=str(resume) if resume else None)
        require_capacity(output.parent, CHECKPOINT_FILE_LIMIT)
        trainer.save_checkpoint(output / "last.ckpt")
        verify_checkpoint(output / "last.ckpt")
        state["global_step"] = trainer.global_step
        if smoke:
            if trainer.global_step != 5:
                raise RuntimeError("Smoke did not complete exactly five optimizer updates")
            # Reload the saved weights from disk, then infer a transformed image.
            saved = torch.load(output / "last.ckpt", map_location="cpu", weights_only=False)
            module.load_state_dict(saved["state_dict"], strict=True)
            ema_states = [v for v in saved.get("callbacks", {}).values() if isinstance(v, dict)
                          and "average_model_state_dict" in v]
            if not ema_states or not saved.get("optimizer_states"):
                raise RuntimeError("Checkpoint lacks EMA or optimizer state")
            if not any(v.get("latest_update_step", 0) > 0 for v in ema_states):
                raise RuntimeError("EMA did not update during smoke training")
            module.eval()
            data.setup("validate")
            images, _ = next(iter(data.val_dataloader()))
            images = images.to(module.device)
            with torch.no_grad():
                result = module.model(images)
            if not torch.isfinite(result["pred_logits"]).all():
                raise RuntimeError("Reloaded inference produced non-finite logits")
            state["checkpoint_reload_and_inference"] = True
        state.update(status="passed" if smoke else "completed_or_time_limited",
                     completed_at_ns=time.time_ns(), checkpoint_sha256=digest(output / "last.ckpt"))
        _write(output / "state.json", state)
        if commit is not None:
            commit()
        return state
    except BaseException as exc:
        try:
            require_capacity(output.parent, CHECKPOINT_FILE_LIMIT)
            trainer.save_checkpoint(output / "interrupted.ckpt")
            verify_checkpoint(output / "interrupted.ckpt")
        except BaseException:
            pass
        state.update(status="failed", reason=f"{type(exc).__name__}: {exc}", stopped_at_ns=time.time_ns())
        _write(output / "state.json", state)
        if commit is not None:
            commit()
        raise

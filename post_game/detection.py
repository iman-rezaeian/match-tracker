"""YOLO11 person + ball detection on perspective crops."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np

from . import config


@dataclass
class Detection:
    frame_index: int
    cls: int
    confidence: float
    bbox_crop: tuple[float, float, float, float]
    bbox_eq: tuple[float, float, float, float]


class Detector:
    def __init__(
        self,
        model_name: str = config.YOLO_MODEL,
        device: str = config.DEVICE,
        confidence: float = config.DETECT_CONFIDENCE,
    ) -> None:
        from ultralytics import YOLO
        self.model = YOLO(model_name)
        self.device = device
        self.confidence = confidence

    def _predict(self, crops: Iterable[np.ndarray], classes: list[int], conf: float):
        imgs = list(crops)
        if not imgs:
            return []
        return self.model.predict(
            imgs,
            classes=classes,
            conf=conf,
            device=self.device,
            verbose=False,
        )

    def detect_persons(self, crops: Iterable[np.ndarray]) -> list[list[Detection]]:
        results = self._predict(crops, [config.PERSON_CLASS_ID], self.confidence)
        return [self._extract(r) for r in results]

    def detect_ball(self, crops: Iterable[np.ndarray]) -> list[list[Detection]]:
        results = self._predict(crops, [config.BALL_CLASS_ID], 0.15)
        return [self._extract(r) for r in results]

    def _extract(self, result) -> list[Detection]:
        dets: list[Detection] = []
        if result.boxes is None:
            return dets
        for box in result.boxes:
            xyxy = box.xyxy[0].tolist()
            dets.append(
                Detection(
                    frame_index=-1,
                    cls=int(box.cls[0].item()),
                    confidence=float(box.conf[0].item()),
                    bbox_crop=(xyxy[0], xyxy[1], xyxy[2], xyxy[3]),
                    bbox_eq=(0.0, 0.0, 0.0, 0.0),
                )
            )
        return dets

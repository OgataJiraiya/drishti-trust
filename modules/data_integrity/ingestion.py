import json
import math
from pathlib import Path
from typing import Any
from PIL import Image, UnidentifiedImageError
from .safe_images import SafeImageError, safe_dataset_path, safe_image_files


SUPPORTED_IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".bmp",
    ".tif",
    ".tiff",
    ".webp",
}


class DatasetIngestionError(Exception):
    """Raised when a dataset cannot be safely ingested."""


def create_sample(
    sample_id: str,
    image_path: str,
    labels: list[str] | None = None,
    annotations: list[dict[str, Any]] | None = None,
    contributor_id: str | None = None,
    batch_id: str | None = None,
    dataset_id: str | None = None,
) -> dict[str, Any]:
    """Create the common internal representation for one dataset sample."""

    return {
        "sample_id": sample_id,
        "image_path": image_path.replace("\\", "/"),
        "labels": labels or [],
        "annotations": annotations or [],
        "contributor_id": contributor_id,
        "batch_id": batch_id,
        "dataset_id": dataset_id,
    }


def load_coco(
    annotation_file: str,
    contributor_id: str | None = None,
    batch_id: str | None = None,
    dataset_id: str | None = None,
    *,
    dataset_root: str | Path | None = None,
    max_images: int | None = None,
) -> list[dict[str, Any]]:
    """Load COCO annotations into the common sample representation."""

    try:
        with open(annotation_file, "r", encoding="utf-8") as file:
            coco = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        raise DatasetIngestionError(
            f"Unable to read COCO annotation file: {annotation_file}"
        ) from exc

    annotation_path = Path(annotation_file)
    categories = {}
    for category in coco.get("categories", []):
        category_id = category.get("id")
        if category_id is None or category_id in categories:
            raise DatasetIngestionError(
                f"Duplicate COCO category ID: {category_id}"
            )

        categories[category_id] = category.get("name")

    images = {}
    for image in coco.get("images", []):
        image_id = image.get("id")
        if image_id is None or image_id in images:
            raise DatasetIngestionError(
                f"Duplicate COCO image ID: {image_id}"
            )

        images[image_id] = image

    annotations_by_image: dict[int, list[dict[str, Any]]] = {}

    annotation_ids = set()
    for annotation in coco.get("annotations", []):
        annotation_id = annotation.get("id")
        if annotation_id is None or annotation_id in annotation_ids:
            raise DatasetIngestionError(f"Duplicate COCO annotation ID: {annotation_id}")
        annotation_ids.add(annotation_id)
        image_id = annotation.get("image_id")
        category_id = annotation.get("category_id")

        if image_id not in images:
            raise DatasetIngestionError(
                f"COCO annotation references unknown image ID: {image_id}"
            )

        if category_id not in categories:
            raise DatasetIngestionError(
                f"COCO annotation references unknown category ID: {category_id}"
            )

        bbox = annotation.get("bbox")
        if (not isinstance(bbox, list) or len(bbox) != 4 or
                any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in bbox) or
            any(not math.isfinite(value) for value in bbox) or
            bbox[2] <= 0 or bbox[3] <= 0):
            raise DatasetIngestionError(f"Malformed COCO bounding box for annotation ID: {annotation_id}")
        annotations_by_image.setdefault(image_id, []).append(
            {
                "annotation_id": annotation.get("id"),
                "class_id": category_id,
                "class_name": categories[category_id],
                "bbox": annotation.get("bbox"),
                "bbox_format": "coco",
                "area": annotation.get("area"),
                "segmentation": annotation.get("segmentation"),
                "iscrowd": annotation.get("iscrowd", 0),
            }
        )

    root = Path(dataset_root) if dataset_root is not None else annotation_path.parent
    image_dimensions: dict[int, tuple[int, int]] = {}
    for image_id, image in images.items():
        image_path = image.get("file_name")
        if not image_path:
            raise DatasetIngestionError(f"COCO image {image_id} has no file_name")
        try:
            resolved = safe_dataset_path(root, image_path)
            with Image.open(resolved) as opened_image:
                image_dimensions[image_id] = opened_image.size
        except (SafeImageError, OSError, UnidentifiedImageError) as exc:
            raise DatasetIngestionError(
                f"Unable to read COCO image {image_id}: {image_path}"
            ) from exc

    for image_id, annotations in annotations_by_image.items():
        width, height = image_dimensions[image_id]
        for annotation in annotations:
            x, y, box_width, box_height = annotation["bbox"]
            if x < 0 or y < 0 or x + box_width > width or y + box_height > height:
                raise DatasetIngestionError(
                    f"COCO bounding box exceeds image dimensions for annotation ID: "
                    f"{annotation['annotation_id']}"
                )

    samples = []

    selected_image_ids = sorted(images)
    if max_images is not None:
        if max_images <= 0:
            raise ValueError("max_images must be greater than zero.")
        selected_image_ids = selected_image_ids[:max_images]

    for image_id in selected_image_ids:
        image = images[image_id]
        image_path = image.get("file_name")

        if not image_path:
            raise DatasetIngestionError(
                f"COCO image {image_id} has no file_name"
            )
        try:
            safe_dataset_path(root, image_path)
        except SafeImageError as exc:
            raise DatasetIngestionError(str(exc)) from exc

        annotations = annotations_by_image.get(image_id, [])

        labels = sorted(
            {
                annotation["class_name"]
                for annotation in annotations
                if annotation["class_name"] is not None
            }
        )

        sample_id = (
            f"{dataset_id}:{image_path}"
            if dataset_id
            else str(image_id)
        )

        samples.append(
            create_sample(
                sample_id=sample_id,
                image_path=image_path,
                labels=labels,
                annotations=annotations,
                contributor_id=contributor_id,
                batch_id=batch_id,
                dataset_id=dataset_id,
            )
        )

    return samples


def load_yolo(
    images_dir: str,
    labels_dir: str,
    classes_file: str,
    contributor_id: str | None = None,
    batch_id: str | None = None,
    dataset_id: str | None = None,
    require_labels: bool = False,
    max_images: int | None = None,
    dataset_root: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Load YOLO annotations into the common sample representation."""

    images_path = Path(images_dir)
    labels_path = Path(labels_dir)

    if not images_path.is_dir():
        raise DatasetIngestionError(
            f"Images directory does not exist: {images_dir}"
        )

    if not labels_path.is_dir():
        raise DatasetIngestionError(
            f"Labels directory does not exist: {labels_dir}"
        )

    try:
        with open(classes_file, "r", encoding="utf-8") as file:
            raw_classes = [line.strip() for line in file]
    except OSError as exc:
        raise DatasetIngestionError(
            f"Unable to read classes file: {classes_file}"
        ) from exc

    if not raw_classes or any(not name for name in raw_classes) or len(set(raw_classes)) != len(raw_classes):
        raise DatasetIngestionError("YOLO class names must be non-empty and unique.")
    classes = raw_classes
    try:
        image_files = safe_image_files(
            images_path, dataset_root or images_path, max_images=max_images
        )
    except SafeImageError as exc:
        raise DatasetIngestionError(str(exc)) from exc
    stems: dict[str, Path] = {}
    for image_file in image_files:
        if image_file.stem in stems:
            raise DatasetIngestionError(f"Duplicate image stem: {image_file.stem}")
        stems[image_file.stem] = image_file
    for label_file in labels_path.glob("*.txt"):
        if label_file.stem not in stems and label_file.name != Path(classes_file).name:
            raise DatasetIngestionError(f"Orphan YOLO label file: {label_file}")
    samples = []

    for image_file in image_files:
        label_file = labels_path / f"{image_file.stem}.txt"
        annotations = []

        if label_file.exists():
            try:
                with open(label_file, "r", encoding="utf-8") as file:
                    lines = file.readlines()
            except OSError as exc:
                raise DatasetIngestionError(
                    f"Unable to read label file: {label_file}"
                ) from exc

            for line_number, line in enumerate(lines, start=1):
                parts = line.strip().split()

                if not parts:
                    continue

                if len(parts) != 5:
                    raise DatasetIngestionError(
                        f"Invalid YOLO annotation at {label_file}:{line_number}"
                    )

                try:
                    class_id = int(parts[0])
                    bbox = [float(value) for value in parts[1:]]
                except ValueError as exc:
                    raise DatasetIngestionError(
                        f"Non-numeric YOLO annotation at "
                        f"{label_file}:{line_number}"
                    ) from exc

                if not 0 <= class_id < len(classes):
                    raise DatasetIngestionError(
                        f"Invalid YOLO class ID {class_id} at "
                        f"{label_file}:{line_number}"
                    )

                x_center, y_center, width, height = bbox
                if not (0 <= x_center <= 1 and 0 <= y_center <= 1 and width > 0 and height > 0 and
                        x_center - width / 2 >= 0 and x_center + width / 2 <= 1 and
                        y_center - height / 2 >= 0 and y_center + height / 2 <= 1):
                    raise DatasetIngestionError(
                        f"Invalid YOLO bounding box at "
                        f"{label_file}:{line_number}"
                    )

                annotations.append(
                    {
                        "class_id": class_id,
                        "class_name": classes[class_id],
                        "bbox": bbox,
                        "bbox_format": "yolo",
                    }
                )

        elif require_labels:
            raise DatasetIngestionError(f"Missing YOLO label file: {label_file}")

        labels = sorted(
            {annotation["class_name"] for annotation in annotations}
        )

        relative_image_path = image_file.relative_to(images_path)

        if dataset_id:
            sample_id = f"{dataset_id}:{relative_image_path.as_posix()}"
        else:
            sample_id = relative_image_path.as_posix()

        samples.append(
            create_sample(
                sample_id=sample_id,
                image_path=str(relative_image_path),
                labels=labels,
                annotations=annotations,
                contributor_id=contributor_id,
                batch_id=batch_id,
                dataset_id=dataset_id,
            )
        )

    return samples

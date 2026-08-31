import json
from pathlib import Path
from typing import Any


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
) -> list[dict[str, Any]]:
    """Load COCO annotations into the common sample representation."""

    try:
        with open(annotation_file, "r", encoding="utf-8") as file:
            coco = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        raise DatasetIngestionError(
            f"Unable to read COCO annotation file: {annotation_file}"
        ) from exc

    categories = {}
    for category in coco.get("categories", []):
        category_id = category.get("id")

        if category_id in categories:
            raise DatasetIngestionError(
                f"Duplicate COCO category ID: {category_id}"
            )

        categories[category_id] = category.get("name")

    images = {}
    for image in coco.get("images", []):
        image_id = image.get("id")

        if image_id in images:
            raise DatasetIngestionError(
                f"Duplicate COCO image ID: {image_id}"
            )

        images[image_id] = image

    annotations_by_image: dict[int, list[dict[str, Any]]] = {}

    for annotation in coco.get("annotations", []):
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

    samples = []

    for image_id in sorted(images):
        image = images[image_id]
        image_path = image.get("file_name")

        if not image_path:
            raise DatasetIngestionError(
                f"COCO image {image_id} has no file_name"
            )

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
            classes = [line.strip() for line in file if line.strip()]
    except OSError as exc:
        raise DatasetIngestionError(
            f"Unable to read classes file: {classes_file}"
        ) from exc

    samples = []

    image_files = sorted(
        image_file
        for image_file in images_path.iterdir()
        if image_file.is_file()
        and image_file.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
    )

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

                if not all(0.0 <= value <= 1.0 for value in bbox):
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
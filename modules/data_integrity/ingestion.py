import json
from pathlib import Path
from typing import Any


def create_sample(
    sample_id: str,
    image_path: str,
    labels: list[str] | None = None,
    contributor_id: str | None = None,
    batch_id: str | None = None,
) -> dict[str, Any]:
    """
    Create the common representation used by the Data Integrity module.
    """

    return {
        "sample_id": sample_id,
        "image_path": image_path.replace("\\", "/"),
        "labels": labels or [],
        "contributor_id": contributor_id,
        "batch_id": batch_id,
    }


def load_coco(annotation_file: str) -> list[dict[str, Any]]:
    """
    Load a COCO annotation JSON file and convert it
    into the common sample representation.
    """

    with open(annotation_file, "r", encoding="utf-8") as file:
        coco = json.load(file)

    categories = {
        category["id"]: category["name"]
        for category in coco.get("categories", [])
    }

    annotations_by_image: dict[int, list[str]] = {}

    for annotation in coco.get("annotations", []):
        image_id = annotation["image_id"]
        category_id = annotation["category_id"]

        label = categories.get(category_id)

        if label is not None:
            annotations_by_image.setdefault(image_id, []).append(label)

    samples = []

    for image in coco.get("images", []):
        image_id = image["id"]
        image_path = image["file_name"]

        labels = annotations_by_image.get(image_id, [])

        samples.append(
            create_sample(
                sample_id=str(image_id),
                image_path=image_path,
                labels=sorted(set(labels)),
            )
        )
    return samples

def load_yolo(
    images_dir: str,
    labels_dir: str,
    classes_file: str,
) -> list[dict[str, Any]]:
    """
    Load a YOLO dataset and convert it into the common sample representation.
    """

    with open(classes_file, "r", encoding="utf-8") as file:
        classes = [line.strip() for line in file if line.strip()]

    samples = []

    for image_file in sorted(Path(images_dir).iterdir()):
        if not image_file.is_file():
            continue

        label_file = Path(labels_dir) / f"{image_file.stem}.txt"

        labels = []

        if label_file.exists():
            with open(label_file, "r", encoding="utf-8") as file:
                for line in file:
                    parts = line.strip().split()

                    if not parts:
                        continue

                    class_id = int(parts[0])

                    if 0 <= class_id < len(classes):
                        labels.append(classes[class_id])

        samples.append(
            create_sample(
                sample_id=image_file.stem,
                image_path=str(image_file),
                labels=sorted(set(labels)),
            )
        )

    return samples
    

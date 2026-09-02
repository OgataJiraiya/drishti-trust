import json
import pytest
from PIL import Image
from modules.data_integrity.ingestion import DatasetIngestionError, load_coco


def _coco(tmp_path, images=None, categories=None, annotations=None):
    images = images or [{"id": 1, "file_name": "images/one.jpg"}]
    categories = categories or [{"id": 1, "name": "plane"}]
    annotations = annotations or [{"id": 1, "image_id": 1, "category_id": 1, "bbox": [1, 2, 3, 4]}]
    for image in images:
        path = tmp_path / image["file_name"]
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (100, 80), "white").save(path)
    path = tmp_path / "annotations.json"
    path.write_text(json.dumps({"images": images, "categories": categories, "annotations": annotations}))
    return path


def test_load_coco_valid_relationships(tmp_path):
    sample = load_coco(str(_coco(tmp_path)))[0]
    assert sample["image_path"] == "images/one.jpg"
    assert sample["labels"] == ["plane"]
    assert sample["annotations"][0]["bbox_format"] == "coco"


@pytest.mark.parametrize("section, entries, message", [
    ("images", [{"id": 1, "file_name": "a.jpg"}, {"id": 1, "file_name": "b.jpg"}], "Duplicate COCO image ID"),
    ("categories", [{"id": 1, "name": "a"}, {"id": 1, "name": "b"}], "Duplicate COCO category ID"),
    ("annotations", [{"id": 1, "image_id": 1, "category_id": 1, "bbox": [1, 1, 1, 1]}, {"id": 1, "image_id": 1, "category_id": 1, "bbox": [1, 1, 1, 1]}], "Duplicate COCO annotation ID"),
])
def test_coco_duplicate_ids(tmp_path, section, entries, message):
    path = _coco(tmp_path)
    payload = json.loads(path.read_text())
    payload[section] = entries
    if section == "images":
        for entry in entries: (tmp_path / entry["file_name"]).write_bytes(b"x")
    path.write_text(json.dumps(payload))
    with pytest.raises(DatasetIngestionError, match=message): load_coco(str(path))


@pytest.mark.parametrize("annotation, message", [
    ({"id": 2, "image_id": 99, "category_id": 1, "bbox": [1, 2, 3, 4]}, "unknown image"),
    ({"id": 2, "image_id": 1, "category_id": 99, "bbox": [1, 2, 3, 4]}, "unknown category"),
    ({"id": 2, "image_id": 1, "category_id": 1, "bbox": [1, 2, -3, 4]}, "Malformed COCO"),
])
def test_coco_invalid_annotations(tmp_path, annotation, message):
    with pytest.raises(DatasetIngestionError, match=message): load_coco(str(_coco(tmp_path, annotations=[annotation])))


@pytest.mark.parametrize("name", ["../../outside.jpg", "/tmp/outside.jpg", "C:\\outside.jpg"])
def test_coco_rejects_unsafe_paths(tmp_path, name):
    path = tmp_path / "annotations.json"
    path.write_text(json.dumps({"images": [{"id": 1, "file_name": name}], "categories": [], "annotations": []}))
    with pytest.raises(DatasetIngestionError): load_coco(str(path))


def test_coco_accepts_bbox_inside_actual_image_dimensions(tmp_path):
    path = _coco(tmp_path, annotations=[{"id": 2, "image_id": 1, "category_id": 1, "bbox": [0, 0, 100, 80]}])
    assert load_coco(str(path))[0]["annotations"][0]["bbox"] == [0, 0, 100, 80]


@pytest.mark.parametrize("bbox", [[-1, 0, 10, 10], [95, 0, 10, 10], [0, 75, 10, 10]])
def test_coco_rejects_bbox_outside_actual_image_dimensions(tmp_path, bbox):
    path = _coco(tmp_path, annotations=[{"id": 2, "image_id": 1, "category_id": 1, "bbox": bbox}])
    with pytest.raises(DatasetIngestionError, match="dimensions"):
        load_coco(str(path))


def test_coco_rejects_unreadable_image(tmp_path):
    path = _coco(tmp_path)
    (tmp_path / "images" / "one.jpg").write_bytes(b"not an image")
    with pytest.raises(DatasetIngestionError, match="Unable to read COCO image"):
        load_coco(str(path))

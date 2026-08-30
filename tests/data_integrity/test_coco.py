import json

from modules.data_integrity.ingestion import load_coco


def test_load_coco(tmp_path):
    coco_data = {
        "images": [
            {
                "id": 1,
                "file_name": "images/img_001.jpg",
            },
            {
                "id": 2,
                "file_name": "images/img_002.jpg",
            },
        ],
        "categories": [
            {
                "id": 1,
                "name": "car",
            },
            {
                "id": 2,
                "name": "person",
            },
        ],
        "annotations": [
            {
                "image_id": 1,
                "category_id": 1,
            },
            {
                "image_id": 1,
                "category_id": 2,
            },
            {
                "image_id": 2,
                "category_id": 1,
            },
        ],
    }

    annotation_file = tmp_path / "annotations.json"
    annotation_file.write_text(
        json.dumps(coco_data),
        encoding="utf-8",
    )

    samples = load_coco(str(annotation_file))

    assert len(samples) == 2

    assert samples[0]["sample_id"] == "1"
    assert samples[0]["image_path"] == "images/img_001.jpg"
    assert samples[0]["labels"] == ["car", "person"]

    assert samples[1]["sample_id"] == "2"
    assert samples[1]["labels"] == ["car"]
from modules.data_integrity.ingestion import load_yolo


def test_load_yolo(tmp_path):
    images_dir = tmp_path / "images"
    labels_dir = tmp_path / "labels"
    classes_file = tmp_path / "classes.txt"

    images_dir.mkdir()
    labels_dir.mkdir()

    (images_dir / "img_001.jpg").write_bytes(b"fake image")
    (images_dir / "img_002.jpg").write_bytes(b"fake image")

    (classes_file).write_text(
        "car\nperson\nbicycle\n",
        encoding="utf-8",
    )

    (labels_dir / "img_001.txt").write_text(
        "0 0.5 0.5 0.4 0.4\n"
        "1 0.3 0.3 0.2 0.2\n",
        encoding="utf-8",
    )

    (labels_dir / "img_002.txt").write_text(
        "2 0.5 0.5 0.3 0.3\n",
        encoding="utf-8",
    )

    samples = load_yolo(
        str(images_dir),
        str(labels_dir),
        str(classes_file),
    )

    assert len(samples) == 2

    assert samples[0]["sample_id"] == "img_001.jpg"
    assert samples[0]["labels"] == ["car", "person"]

    assert samples[1]["sample_id"] == "img_002.jpg"
    assert samples[1]["labels"] == ["bicycle"]
